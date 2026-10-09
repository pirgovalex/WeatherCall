"""Manifest loading, startup checks, read-only access and /meta."""

import json
import os

import duckdb
import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.warehouse import StartupError

from .conftest import edit_manifest


def test_meta_matches_the_fixture_manifest(client, manifest):
    res = client.get("/api/v1/meta")
    assert res.status_code == 200
    assert res.json() == manifest


def test_meta_passes_the_optional_range_fields_through(client, manifest):
    marts = client.get("/api/v1/meta").json()["marts"]
    assert marts["mart_weather_aq_hourly"]["max_analysis_valid_at_utc"] == manifest["marts"]["mart_weather_aq_hourly"]["max_analysis_valid_at_utc"]
    assert marts["mart_weather_aq_hourly"]["max_analysis_valid_at_utc"] == "2026-10-27T23:00:00Z"
    assert (marts["mart_aq_daily"]["min_day"], marts["mart_aq_daily"]["max_day"]) == ("2026-10-14", "2026-10-27")


def test_meta_omits_the_optional_range_fields_when_the_manifest_has_none(marts_copy):
    def strip(m):
        m["marts"]["mart_weather_aq_hourly"].pop("max_analysis_valid_at_utc")
        m["marts"]["mart_aq_daily"].pop("min_day")
        m["marts"]["mart_aq_daily"].pop("max_day")

    edit_manifest(marts_copy, strip)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as c:
        res = c.get("/api/v1/meta")
        assert res.status_code == 200
        marts = res.json()["marts"]
        assert "max_analysis_valid_at_utc" not in marts["mart_weather_aq_hourly"]
        assert "min_day" not in marts["mart_aq_daily"] and "max_day" not in marts["mart_aq_daily"]
        assert marts["mart_weather_aq_hourly"]["max_valid_at_utc"] == "2026-10-27T23:00:00Z"


def test_a_malformed_optional_range_field_fails_startup(marts_copy):
    edit_manifest(marts_copy, lambda m: m["marts"]["mart_aq_daily"].__setitem__("max_day", "27/10/2026"))
    with pytest.raises(StartupError, match="manifest.schema.json"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_connections_are_read_only(client):
    con = client.app.state.warehouse.connect()
    try:
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE should_fail (i INTEGER)")
        with pytest.raises(duckdb.Error):
            con.execute("ATTACH ':memory:' AS other; CREATE TABLE other.t (i INTEGER)")
    finally:
        con.close()


def test_schema_version_mismatch_fails_startup(marts_copy):
    path = marts_copy / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = "2"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StartupError, match="schema_version"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_missing_manifest_fails_startup(tmp_path):
    with pytest.raises(StartupError, match="manifest"):
        with TestClient(create_app(allowed_origins=[], marts_dir=tmp_path)):
            pass


def test_manifest_that_breaks_the_schema_fails_startup(marts_copy):
    path = marts_copy / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["attribution"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StartupError, match="manifest"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_mart_files_that_do_not_exist_fail_startup(marts_copy):
    for p in (marts_copy / "mart_aq_daily" / "latest").glob("*.parquet"):
        p.unlink()
    with pytest.raises(StartupError, match="mart_aq_daily"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_new_pipeline_run_is_picked_up_without_restart(marts_copy):
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        assert client.get("/api/v1/meta").json()["run_id"] == "2026-10-07T14-00-00Z-abc123"
        path = marts_copy / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["run_id"] = "2026-10-08T14-00-00Z-def456"
        path.write_text(json.dumps(data), encoding="utf-8")
        later = path.stat().st_mtime + 10
        os.utime(path, (later, later))
        assert client.get("/api/v1/meta").json()["run_id"] == "2026-10-08T14-00-00Z-def456"


def test_a_broken_new_manifest_keeps_serving_the_previous_run(marts_copy):
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        path = marts_copy / "manifest.json"
        path.write_text("{ not json", encoding="utf-8")
        later = path.stat().st_mtime + 10
        os.utime(path, (later, later))
        res = client.get("/api/v1/meta")
        assert res.status_code == 200
        assert res.json()["run_id"] == "2026-10-07T14-00-00Z-abc123"
