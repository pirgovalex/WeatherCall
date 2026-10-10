"""GET /api/v1/aq/daily"""

import duckdb
from fastapi.testclient import TestClient

from src.main import create_app

from .conftest import DAILY_GLOB, edit_manifest, query


def get(client, **overrides):
    params = {"pollutant": "pm10", "location": "plovdiv", "from": "2026-10-14", "to": "2026-10-27", **overrides}
    return client.get("/api/v1/aq/daily", params=params)


def test_parallel_arrays_match_fixture(client, manifest):
    res = get(client)
    assert res.status_code == 200
    body = res.json()
    rows = query(
        "select strftime(day, '%Y-%m-%d'), daily_mean, valid_hours, is_valid_day from read_parquet(?) "
        "where location_id = 'plovdiv' and pollutant = 'pm10' order by day",
        DAILY_GLOB,
    )
    assert body["days"] == [r[0] for r in rows]
    assert body["mean"] == [r[1] for r in rows]
    assert body["valid_hours"] == [r[2] for r in rows]
    assert body["is_valid_day"] == [r[3] for r in rows]
    assert len(body["days"]) == 14
    assert body["pollutant"] == "pm10" and body["location_id"] == "plovdiv"
    assert body["attribution"] == manifest["attribution"]
    assert body["warehouse"]["run_id"] == manifest["run_id"]


def test_validity_flag_follows_the_eighteen_of_twenty_four_rule(client):
    body = get(client).json()
    assert all(flag == (hours >= 18) for hours, flag in zip(body["valid_hours"], body["is_valid_day"]))


def test_accepts_utc_datetimes_and_limits_the_range(client):
    body = get(client, **{"from": "2026-10-20T00:00:00Z", "to": "2026-10-22T00:00:00Z"}).json()
    assert body["days"] == ["2026-10-20", "2026-10-21", "2026-10-22"]


def test_unknown_pollutant_is_404(client):
    res = get(client, pollutant="o3")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_location_is_404(client):
    assert get(client, location="nowhere").status_code == 404


def test_a_location_of_the_daily_mart_only_is_served(marts_copy):
    """A station's own daily location (stations[].daily_location_id) is not in the hourly mart."""
    path = marts_copy / "mart_aq_daily" / "latest" / "mart_aq_daily.parquet"
    tmp = path.with_suffix(".tmp")
    con = duckdb.connect()
    try:
        con.execute(
            f"COPY (SELECT * FROM read_parquet('{path.as_posix()}') UNION ALL "
            f"SELECT * REPLACE ('plovdiv-trakia' AS location_id, daily_mean * 1.3 AS daily_mean) "
            f"FROM read_parquet('{path.as_posix()}') WHERE location_id = 'plovdiv') TO '{tmp.as_posix()}' (FORMAT parquet)"
        )
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{tmp.as_posix()}')").fetchone()[0]
    finally:
        con.close()
    tmp.replace(path)
    edit_manifest(
        marts_copy,
        lambda m: m["stations"]["BG0078A"].update(daily_location_id="plovdiv-trakia")
        or m["marts"]["mart_aq_daily"].update(rows=rows),
    )
    app = create_app(allowed_origins=["http://localhost:5173"], marts_dir=marts_copy)
    with TestClient(app) as client:
        res = get(client, location="plovdiv-trakia")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["location_id"] == "plovdiv-trakia" and len(body["days"]) == 14
        meta = client.get("/api/v1/meta").json()
        assert meta["stations"]["BG0078A"]["daily_location_id"] == "plovdiv-trakia"
        assert "daily_location_id" not in meta["stations"]["BG0051A"]


def test_range_over_366_days_is_422(client):
    res = get(client, **{"from": "2025-01-01", "to": "2026-10-27"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
