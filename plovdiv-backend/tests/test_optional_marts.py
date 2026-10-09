"""Optional marts: startup refusals, manifest reload and runs without the new marts."""

import json

import pytest
from fastapi.testclient import TestClient

from src.main import create_app
from src.warehouse import StartupError

from .conftest import FIXTURES, assert_envelope, edit_manifest

NEW_ENDPOINTS = [
    ("/api/v1/aq/trend", {"station_id": "BG0051A", "from": "2026-01-01", "to": "2026-10-31"}),
    ("/api/v1/aq/risk", {"station_id": "BG0051A", "from": "2026-10-01", "to": "2026-10-28"}),
    ("/api/v1/aq/risk", {"station_id": "BG0051A", "latest": "true"}),
    ("/api/v1/model/card", {"model_id": "pm10_exceed_gbm_v1"}),
    ("/api/v1/model/calibration", {"model_id": "pm10_exceed_gbm_v1"}),
]

NEW_MARTS = ["mart_model_calibration", "mart_aq_risk_drivers", "mart_aq_risk_daily", "mart_model_card", "mart_aq_trend_monthly"]


@pytest.fixture
def original_manifest():
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


def old_manifest(marts_dir):
    """Rewrite the manifest as an earlier run would have: no new marts and no stations."""

    def strip(m):
        for name in NEW_MARTS:
            m["marts"].pop(name)
        m.pop("stations")

    edit_manifest(marts_dir, strip)


def test_a_run_without_the_new_marts_serves_the_old_endpoints(marts_copy):
    old_manifest(marts_copy)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        meta = client.get("/api/v1/meta").json()
        assert set(meta["marts"]) == {"mart_weather_aq_hourly", "mart_aq_daily"}
        assert "stations" not in meta
        daily = client.get("/api/v1/aq/daily", params={"pollutant": "pm10", "location": "plovdiv", "from": "2026-10-14", "to": "2026-10-27"})
        assert daily.status_code == 200
        for path, params in NEW_ENDPOINTS:
            res = client.get(path, params=params)
            assert_envelope(res, 503, "SERVICE_UNAVAILABLE")


def test_validation_errors_come_before_the_503(marts_copy):
    old_manifest(marts_copy)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        assert_envelope(client.get("/api/v1/aq/trend", params={"station_id": "BG0051A"}), 422, "VALIDATION_ERROR")
        assert_envelope(client.get("/api/v1/model/card"), 422, "VALIDATION_ERROR")


def test_new_marts_appear_after_a_manifest_change_without_restart(marts_copy, original_manifest):
    old_manifest(marts_copy)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        path, params = NEW_ENDPOINTS[0]
        assert_envelope(client.get(path, params=params), 503, "SERVICE_UNAVAILABLE")

        def restore(m):
            m["marts"] = original_manifest["marts"]
            m["stations"] = original_manifest["stations"]
            m["run_id"] = "2026-10-08T14-00-00Z-def456"

        edit_manifest(marts_copy, restore)
        res = client.get(path, params=params)
        assert res.status_code == 200
        assert res.json()["warehouse"]["run_id"] == "2026-10-08T14-00-00Z-def456"
        assert client.get("/api/v1/model/card", params={"model_id": "pm10_exceed_gbm_v1"}).status_code == 200
        assert client.get("/api/v1/aq/risk", params={"station_id": "BG0051A", "latest": "true"}).json()["drivers"]["rank"] == [1, 2, 3, 4, 5]


def test_a_mart_removed_by_a_new_manifest_returns_503_at_once(marts_copy):
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        path, params = NEW_ENDPOINTS[3]
        assert client.get(path, params=params).status_code == 200
        edit_manifest(marts_copy, lambda m: [m["marts"].pop(n) for n in ("mart_model_calibration", "mart_aq_risk_drivers", "mart_aq_risk_daily", "mart_model_card")])
        assert_envelope(client.get(path, params=params), 503, "SERVICE_UNAVAILABLE")
        assert client.get("/api/v1/aq/trend", params=NEW_ENDPOINTS[0][1]).status_code == 200


def test_a_new_run_with_other_station_ids_is_used(marts_copy):
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        params = NEW_ENDPOINTS[0][1]
        assert client.get("/api/v1/aq/trend", params=params).status_code == 200

        def rename(m):
            m["stations"] = {"BG0099A": m["stations"]["BG0051A"]}

        edit_manifest(marts_copy, rename)
        assert_envelope(client.get("/api/v1/aq/trend", params=params), 404, "NOT_FOUND")


def test_a_broken_new_manifest_keeps_serving_the_new_endpoints(marts_copy):
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        path = marts_copy / "manifest.json"
        path.write_text("{ not json", encoding="utf-8")
        import os

        later = path.stat().st_mtime + 10
        os.utime(path, (later, later))
        assert client.get("/api/v1/model/card", params={"model_id": "pm10_exceed_gbm_v1"}).status_code == 200


def test_declared_mart_without_files_fails_startup(marts_copy):
    for p in (marts_copy / "mart_model_card" / "latest").glob("*.parquet"):
        p.unlink()
    with pytest.raises(StartupError, match="mart_model_card"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_declared_mart_with_missing_columns_fails_startup(marts_copy):
    import duckdb

    target = marts_copy / "mart_aq_risk_daily" / "latest" / "mart_aq_risk_daily.parquet"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * EXCLUDE (probability) FROM read_parquet('{target}')) TO '{target}.new' (FORMAT PARQUET)")
    con.close()
    target.with_name(target.name + ".new").replace(target)
    with pytest.raises(StartupError, match="probability"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_mart_glob_outside_the_marts_directory_fails_startup(marts_copy):
    edit_manifest(marts_copy, lambda m: m["marts"]["mart_model_card"].update(glob="../outside/*.parquet"))
    with pytest.raises(StartupError, match="outside"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_manifest_missing_the_dependency_of_a_declared_mart_fails_startup(marts_copy):
    edit_manifest(marts_copy, lambda m: m["marts"].pop("mart_model_card"))
    with pytest.raises(StartupError, match="manifest"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_station_marts_without_a_station_catalogue_fail_startup(marts_copy):
    edit_manifest(marts_copy, lambda m: m.pop("stations"))
    with pytest.raises(StartupError, match="manifest"):
        with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)):
            pass


def test_new_endpoints_are_read_only_get(client):
    for path, params in NEW_ENDPOINTS:
        assert client.post(path, params=params).status_code == 405
