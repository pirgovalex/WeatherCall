"""Parameter validation, error envelope and failure handling."""

import pytest

from src.warehouse import Warehouse

BASE = {
    "variable": "pm2_5",
    "location": "plovdiv",
    "from": "2026-10-14T00:00:00Z",
    "to": "2026-10-27T23:00:00Z",
    "resolution": "hourly",
}


def series(client, **overrides):
    params = {**BASE, **overrides}
    return client.get("/api/v1/series", params={k: v for k, v in params.items() if v is not None})


def assert_envelope(res, status, code):
    assert res.status_code == status
    body = res.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


@pytest.fixture
def query_counter(monkeypatch):
    calls = []
    for name in ("fetch_series", "fetch_daily_air_quality"):
        original = getattr(Warehouse, name)

        def wrapper(self, *a, _orig=original, _name=name, **k):
            calls.append(_name)
            return _orig(self, *a, **k)

        monkeypatch.setattr(Warehouse, name, wrapper)
    return calls


def test_unknown_variable_is_404(client):
    assert_envelope(series(client, variable="humidity"), 404, "NOT_FOUND")


def test_unknown_location_is_404(client):
    assert_envelope(series(client, location="varna"), 404, "NOT_FOUND")


@pytest.mark.parametrize(
    "overrides",
    [
        {"variable": "pm2_5; DROP TABLE hourly"},
        {"variable": "pm2_5' OR '1'='1"},
        {"variable": '"pm2_5"'},
        {"variable": "../../etc/passwd"},
        {"variable": "a" * 200},
        {"location": "plovdiv' OR 1=1 --"},
        {"location": "plovdiv/../x"},
        {"from": "yesterday"},
        {"from": "2026-10-14T00:00:00"},
        {"from": "2026-10-14T00:00:00+02:00"},
        {"to": "2026-13-45T00:00:00Z"},
        {"to": "2026-10-13T00:00:00Z"},
        {"from": "2025-01-01T00:00:00Z", "to": "2026-10-27T23:00:00Z"},
        {"resolution": "weekly"},
        {"source_type": "measured"},
        {"source_type": "analysis' --"},
        {"resolution": None},
    ],
)
def test_bad_parameters_are_422_and_run_no_query(client, query_counter, overrides):
    res = series(client, **overrides)
    assert_envelope(res, 422, "VALIDATION_ERROR")
    assert query_counter == []


def test_missing_required_parameter_is_422(client, query_counter):
    res = client.get("/api/v1/series", params={"variable": "pm2_5"})
    assert_envelope(res, 422, "VALIDATION_ERROR")
    assert query_counter == []


def test_range_of_exactly_366_days_is_accepted_and_one_hour_more_is_not(client):
    ok = series(client, **{"from": "2025-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"})
    assert ok.status_code == 200
    too_long = series(client, **{"from": "2025-10-01T00:00:00Z", "to": "2026-10-02T01:00:00Z"})
    assert_envelope(too_long, 422, "VALIDATION_ERROR")


def test_unknown_route_uses_the_envelope(client):
    assert_envelope(client.get("/api/v1/nope"), 404, "NOT_FOUND")


def test_internal_errors_are_hidden_behind_the_envelope(lenient_client, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(Warehouse, "fetch_series", boom)
    res = series(lenient_client)
    assert_envelope(res, 500, "INTERNAL_ERROR")
    assert "secret" not in res.text


def test_cors_header_on_data_endpoint_for_allowed_origin_only(client):
    ok = client.get("/api/v1/meta", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = client.get("/api/v1/meta", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
