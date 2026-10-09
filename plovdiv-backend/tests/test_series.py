"""GET /api/v1/series"""

import json

from .conftest import HOURLY_GLOB, query

FULL = {
    "variable": "pm2_5",
    "location": "plovdiv",
    "from": "2026-10-14T00:00:00Z",
    "to": "2026-10-27T23:00:00Z",
    "resolution": "hourly",
    "source_type": "analysis",
}


def get(client, **overrides):
    params = {**FULL, **overrides}
    return client.get("/api/v1/series", params={k: v for k, v in params.items() if v is not None})


def expected_hourly(column: str):
    rows = query(
        f'select epoch(valid_at_utc)::BIGINT, "{column}" from read_parquet(?) '
        "where location_id = 'plovdiv' and source_type = 'analysis' order by 1",
        HOURLY_GLOB,
    )
    return [r[0] for r in rows], [r[1] for r in rows]


def test_full_range_matches_fixture_values_and_keeps_gaps(client, manifest):
    res = get(client)
    assert res.status_code == 200
    body = res.json()

    exp_ts, exp_values = expected_hourly("pm2_5")
    assert body["timestamps"] == exp_ts
    assert body["values"] == exp_values
    assert len(body["timestamps"]) == 336

    # The fixture contains a six-hour gap: gaps must be null, never zero.
    assert body["values"].count(None) >= 6
    assert all(v is None or v > 0 for v in body["values"])

    assert body["variable"] == "pm2_5"
    assert body["unit"] == manifest["variables"]["pm2_5"]["unit"]
    assert body["location_id"] == "plovdiv"
    assert body["resolution"] == "hourly"
    assert body["source_type"] == "analysis"
    assert body["attribution"] == manifest["attribution"]
    assert body["warehouse"] == {
        "run_id": manifest["run_id"],
        "schema_version": manifest["schema_version"],
        "generated_at_utc": manifest["generated_at_utc"],
    }


def test_timestamps_are_unique_increasing_integers(client):
    ts = get(client).json()["timestamps"]
    assert all(isinstance(t, int) for t in ts)
    assert ts == sorted(set(ts))
    assert all(b - a == 3600 for a, b in zip(ts, ts[1:]))


def test_to_is_inclusive(client):
    body = get(client, **{"from": "2026-10-20T00:00:00Z", "to": "2026-10-20T05:00:00Z"}).json()
    assert len(body["timestamps"]) == 6
    assert body["timestamps"][-1] - body["timestamps"][0] == 5 * 3600


def test_other_variables_use_their_own_unit(client, manifest):
    body = get(client, variable="temperature_2m").json()
    assert body["unit"] == "degC"
    exp_ts, exp_values = expected_hourly("temperature_2m")
    assert body["values"] == exp_values


def test_true_zero_is_preserved(client):
    body = get(client, variable="precipitation").json()
    _, exp_values = expected_hourly("precipitation")
    assert body["values"] == exp_values
    assert 0.0 in body["values"]


def test_source_type_defaults_to_analysis(client):
    body = get(client, source_type=None).json()
    assert body["source_type"] == "analysis"
    assert len(body["timestamps"]) == 336


def test_source_type_without_data_is_empty_not_an_error(client):
    res = get(client, source_type="forecast")
    assert res.status_code == 200
    body = res.json()
    assert body["timestamps"] == [] and body["values"] == []
    assert body["source_type"] == "forecast"


def test_daily_resolution_is_the_mean_of_valid_hours_per_utc_day(client):
    body = get(client, resolution="daily").json()
    rows = query(
        "select epoch(date_trunc('day', valid_at_utc))::BIGINT, avg(pm2_5) from read_parquet(?) "
        "where location_id = 'plovdiv' and source_type = 'analysis' group by 1 order by 1",
        HOURLY_GLOB,
    )
    assert body["resolution"] == "daily"
    assert body["timestamps"] == [r[0] for r in rows]
    assert body["values"] == [r[1] for r in rows]


def test_columnar_payload_is_smaller_than_row_oriented(client):
    res = get(client)
    columnar = len(res.content)
    body = res.json()
    rows = [{"timestamp": t, "value": v} for t, v in zip(body["timestamps"], body["values"])]
    row_oriented = len(json.dumps({**{k: v for k, v in body.items() if k not in ("timestamps", "values")}, "rows": rows}))
    assert columnar < row_oriented
