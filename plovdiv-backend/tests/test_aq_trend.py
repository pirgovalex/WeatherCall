"""GET /api/v1/aq/trend"""

from datetime import date, timedelta

import pytest

from .conftest import assert_envelope, edit_manifest, mart_glob, query
from .test_contract_conformance import validate

FULL = {"station_id": "BG0051A", "from": "2025-05-01", "to": "2026-10-31", "pollutant": "pm10"}


def get(client, **overrides):
    params = {**FULL, **overrides}
    return client.get("/api/v1/aq/trend", params={k: v for k, v in params.items() if v is not None})


def expected(station, pollutant, first="2025-05-01", last="2026-10-31"):
    return query(
        "select strftime(month, '%Y-%m-%d'), observed_mean_ugm3, weather_adjusted_mean_ugm3, adjusted_lo_ugm3, "
        "adjusted_hi_ugm3, n_days from read_parquet(?) where station_id = ? and pollutant = ? "
        "and month between ? and ? order by month",
        mart_glob("mart_aq_trend_monthly"), station, pollutant, first, last,
    )


def test_full_range_matches_the_mart_and_validates(client, manifest):
    res = get(client)
    assert res.status_code == 200
    body = res.json()
    validate(body, "AqTrendResponse")
    rows = expected("BG0051A", "pm10")
    assert len(rows) == 18
    assert body["months"] == [r[0] for r in rows]
    assert body["observed_mean"] == [r[1] for r in rows]
    assert body["weather_adjusted_mean"] == [r[2] for r in rows]
    assert body["adjusted_lo"] == [r[3] for r in rows]
    assert body["adjusted_hi"] == [r[4] for r in rows]
    assert body["n_days"] == [r[5] for r in rows]
    assert body["station_id"] == "BG0051A"
    assert body["pollutant"] == "pm10"
    assert body["unit"] == manifest["variables"]["pm10"]["unit"]
    assert body["attribution"] == manifest["attribution"]
    assert body["warehouse"]["run_id"] == manifest["run_id"]
    assert body["months"] == sorted(set(body["months"]))


def test_pollutant_defaults_to_pm10(client):
    assert get(client, pollutant=None).json() == get(client, pollutant="pm10").json()


def test_other_pollutant_is_served(client):
    body = get(client, pollutant="pm2_5").json()
    assert body["pollutant"] == "pm2_5"
    assert body["months"] == [r[0] for r in expected("BG0051A", "pm2_5")]


def test_month_is_included_when_any_part_overlaps_the_range(client):
    body = get(client, **{"from": "2026-09-30", "to": "2026-10-02"}).json()
    assert body["months"] == ["2026-09-01", "2026-10-01"]
    body = get(client, **{"from": "2026-09-15", "to": "2026-09-15"}).json()
    assert body["months"] == ["2026-09-01"]


def test_to_is_inclusive_on_the_first_day_of_a_month(client):
    body = get(client, **{"from": "2026-08-31", "to": "2026-10-01"}).json()
    assert body["months"] == ["2026-08-01", "2026-09-01", "2026-10-01"]


def test_null_means_and_zero_days_are_kept(client):
    body = get(client, station_id="BG0078A", **{"from": "2026-03-01", "to": "2026-03-31"}).json()
    validate(body, "AqTrendResponse")
    assert body["months"] == ["2026-03-01"]
    assert body["n_days"] == [0]
    for key in ("observed_mean", "weather_adjusted_mean", "adjusted_lo", "adjusted_hi"):
        assert body[key] == [None]


def test_nan_becomes_null(marts_copy):
    # Write a trend mart whose adjusted columns hold NaN rather than NULL.
    import duckdb
    from fastapi.testclient import TestClient

    from src.main import create_app

    target = marts_copy / "mart_aq_trend_monthly" / "latest" / "mart_aq_trend_monthly.parquet"
    con = duckdb.connect()
    con.execute(
        "COPY (SELECT * REPLACE ("
        "CASE WHEN month = DATE '2026-10-01' THEN 'NaN'::DOUBLE ELSE weather_adjusted_mean_ugm3 END AS weather_adjusted_mean_ugm3, "
        "CASE WHEN month = DATE '2026-10-01' THEN 'NaN'::DOUBLE ELSE adjusted_lo_ugm3 END AS adjusted_lo_ugm3) "
        f"FROM read_parquet('{target}')) TO '{target}.new' (FORMAT PARQUET)"
    )
    con.close()
    target.with_name(target.name + ".new").replace(target)
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        body = get(client, **{"from": "2026-10-01", "to": "2026-10-31"}).json()
    assert body["weather_adjusted_mean"] == [None]
    assert body["adjusted_lo"] == [None]
    assert body["observed_mean"][0] is not None


def test_known_station_without_rows_returns_empty_arrays(client):
    body = get(client, station_id="BG0078A", pollutant="no2").json()
    validate(body, "AqTrendResponse")
    for key in ("months", "observed_mean", "weather_adjusted_mean", "adjusted_lo", "adjusted_hi", "n_days"):
        assert body[key] == []
    assert get(client, **{"from": "2020-01-01", "to": "2020-02-01"}).json()["months"] == []


def test_unknown_station_is_404(client):
    assert_envelope(get(client, station_id="BG9999A"), 404, "NOT_FOUND")


@pytest.mark.parametrize(
    "overrides",
    [
        {"station_id": None},
        {"station_id": "BG0051A'; DROP TABLE x"},
        {"station_id": "../x"},
        {"station_id": "A" * 100},
        {"from": None},
        {"to": None},
        {"from": "2025-13-01"},
        {"from": "2025-02-30"},
        {"from": "2025-05-01T00:00:00Z"},
        {"to": "yesterday"},
        {"from": "2026-10-31", "to": "2026-10-01"},
        {"pollutant": "o3"},
        {"pollutant": "pm10; select 1"},
    ],
)
def test_invalid_parameters_are_422(client, overrides):
    assert_envelope(get(client, **overrides), 422, "VALIDATION_ERROR")


def test_range_limit_is_7320_days(client):
    last = date(2026, 10, 31)
    assert get(client, **{"from": str(last - timedelta(days=7320)), "to": str(last)}).status_code == 200
    too_long = get(client, **{"from": str(last - timedelta(days=7321)), "to": str(last)})
    assert_envelope(too_long, 422, "VALIDATION_ERROR")


def test_missing_mart_is_503_and_other_endpoints_still_work(marts_copy):
    from fastapi.testclient import TestClient

    from src.main import create_app

    edit_manifest(marts_copy, lambda m: m["marts"].pop("mart_aq_trend_monthly"))
    with TestClient(create_app(allowed_origins=[], marts_dir=marts_copy)) as client:
        res = get(client)
        assert_envelope(res, 503, "SERVICE_UNAVAILABLE")
        assert "mart_aq_trend_monthly" in res.json()["error"]["message"]
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/meta").status_code == 200
        assert client.get("/api/v1/model/card", params={"model_id": "pm10_exceed_gbm_v1"}).status_code == 200
