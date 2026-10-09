"""Tests for the raw forecast capture script (no network access)."""

import glob
import gzip
import json

import pytest
import requests

from plovdiv.extract import fetch_forecasts as ff


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def no_sleep_and_tmp_landing(monkeypatch, tmp_path):
    """Never sleep for real and never write into the real landing zone."""
    sleeps = []
    monkeypatch.setattr(ff.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(ff, "LANDING_DIR", str(tmp_path / "landing"))
    monkeypatch.setattr(ff, "OPS_DIR", str(tmp_path / "ops"))
    return sleeps


def landed_files(tmp_path):
    return sorted(glob.glob(str(tmp_path / "landing" / "**" / "*"), recursive=True))


def read_landed(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def weather_payload(unit="m/s"):
    return {
        "hourly_units": {"wind_speed_10m": unit, "wind_gusts_10m": unit},
        "hourly": {"time": ["2026-10-07T00:00"]},
    }


def test_429_exhaustion_raises_instead_of_returning_none(monkeypatch):
    monkeypatch.setattr(ff.requests, "get", lambda *a, **k: FakeResponse(429))
    with pytest.raises(RuntimeError, match="429"):
        ff.fetch_with_backoff("http://x", {}, max_retries=3)


def test_retry_after_http_date_does_not_crash_and_is_capped(monkeypatch, no_sleep_and_tmp_landing):
    responses = iter([
        FakeResponse(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        FakeResponse(429, headers={"Retry-After": "99999"}),
        FakeResponse(200, {"ok": True}),
    ])
    monkeypatch.setattr(ff.requests, "get", lambda *a, **k: next(responses))
    assert ff.fetch_with_backoff("http://x", {}, max_retries=5) == {"ok": True}
    assert no_sleep_and_tmp_landing == [1, ff.MAX_RETRY_DELAY_S]


def test_client_error_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(ff.requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(400))
    with pytest.raises(requests.exceptions.HTTPError):
        ff.fetch_with_backoff("http://x", {}, max_retries=5)
    assert len(calls) == 1


def test_server_error_is_retried_then_succeeds(monkeypatch):
    responses = iter([FakeResponse(503), FakeResponse(200, {"ok": 1})])
    monkeypatch.setattr(ff.requests, "get", lambda *a, **k: next(responses))
    assert ff.fetch_with_backoff("http://x", {}, max_retries=3) == {"ok": 1}


def test_params_not_mutated_and_api_key_never_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(ff.settings, "openmeteo_api_key", "SECRET-KEY")
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen["params"] = dict(params)
        return FakeResponse(200, {"hourly_units": {}})

    monkeypatch.setattr(ff.requests, "get", fake_get)
    params = {"latitude": 1}
    data = ff.fetch_with_backoff("http://x", params)
    assert params == {"latitude": 1}
    assert seen["params"]["apikey"] == "SECRET-KEY"

    path = ff.save_landing_data("s", "e", data, request_params={**seen["params"]})
    landed = read_landed(path)
    assert "apikey" not in landed["_audit"]["request_params"]
    assert "SECRET-KEY" not in json.dumps(landed)


def test_failed_write_leaves_no_partial_file(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ff.json, "dump", boom)
    with pytest.raises(OSError):
        ff.save_landing_data("s", "e", {"a": 1})
    assert [p for p in landed_files(tmp_path) if p.endswith((".gz", ".tmp"))] == []


def test_weather_failure_does_not_lose_cams(monkeypatch, tmp_path):
    def fake_get(url, params, headers, timeout):
        if "air-quality" in url:
            return FakeResponse(200, {"hourly_units": {}, "hourly": {"time": []}})
        return FakeResponse(400)

    monkeypatch.setattr(ff.requests, "get", fake_get)
    problems = ff.run_forecast_capture()
    files = [p for p in landed_files(tmp_path) if p.endswith(".json.gz")]
    assert len(files) == 1 and "source=cams" in files[0]
    assert len(problems) == 1 and "openmeteo/forecast" in problems[0]


def test_weather_requests_ms_and_unit_guard_passes(monkeypatch, tmp_path):
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen[url] = dict(params)
        if "air-quality" in url:
            return FakeResponse(200, {"hourly_units": {}, "hourly": {"time": []}})
        return FakeResponse(200, weather_payload("m/s"))

    monkeypatch.setattr(ff.requests, "get", fake_get)
    assert ff.run_forecast_capture() == []
    assert seen[ff.settings.weather_api_url]["wind_speed_unit"] == "ms"
    assert len([p for p in landed_files(tmp_path) if p.endswith(".json.gz")]) == 2


def test_weather_request_includes_the_inputs_the_risk_model_needs(monkeypatch, tmp_path):
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen[url] = dict(params)
        if "air-quality" in url:
            return FakeResponse(200, {"hourly_units": {}, "hourly": {"time": []}})
        return FakeResponse(200, weather_payload("m/s"))

    monkeypatch.setattr(ff.requests, "get", fake_get)
    ff.run_forecast_capture()
    requested = set(seen[ff.settings.weather_api_url]["hourly"].split(","))
    assert {"temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation",
            "surface_pressure", "shortwave_radiation"} <= requested


def test_unit_mismatch_keeps_raw_file_but_reports_failure(monkeypatch, tmp_path):
    def fake_get(url, params, headers, timeout):
        if "air-quality" in url:
            return FakeResponse(200, {"hourly_units": {}, "hourly": {"time": []}})
        return FakeResponse(200, weather_payload("km/h"))

    monkeypatch.setattr(ff.requests, "get", fake_get)
    problems = ff.run_forecast_capture()
    assert len([p for p in landed_files(tmp_path) if p.endswith(".json.gz")]) == 2
    assert problems and all("unit mismatch" in p for p in problems)


def read_run_log(tmp_path):
    path = tmp_path / "ops" / ff.RUN_LOG_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_attempt_is_written_to_the_run_log(monkeypatch, tmp_path):
    def fake_get(url, params, headers, timeout):
        if "air-quality" in url:
            return FakeResponse(200, {"hourly_units": {}, "hourly": {"time": []}})
        return FakeResponse(400)

    monkeypatch.setattr(ff.requests, "get", fake_get)
    ff.run_forecast_capture()

    entries = {(e["source"], e["endpoint"]): e for e in read_run_log(tmp_path)}
    assert set(entries) == {("cams", "air_quality"), ("openmeteo", "forecast")}

    ok = entries[("cams", "air_quality")]
    assert ok["status"] == "ok" and ok["landing_file"].endswith(".json.gz") and ok["error"] is None
    failed = entries[("openmeteo", "forecast")]
    assert failed["status"] == "failed" and failed["landing_file"] is None and failed["error"]
    for e in entries.values():
        assert e["started_at_utc"] <= e["finished_at_utc"]


def test_unit_mismatch_is_logged_with_the_landed_file(monkeypatch, tmp_path):
    def fake_get(url, params, headers, timeout):
        if "air-quality" in url:
            return FakeResponse(200, {"hourly_units": {}, "hourly": {"time": []}})
        return FakeResponse(200, weather_payload("km/h"))

    monkeypatch.setattr(ff.requests, "get", fake_get)
    ff.run_forecast_capture()
    weather = [e for e in read_run_log(tmp_path) if e["source"] == "openmeteo"][0]
    assert weather["status"] == "unit_mismatch" and weather["landing_file"]


def test_run_log_failure_never_breaks_the_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(ff, "OPS_DIR", str(tmp_path / "ops" / "blocked"))
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "blocked").write_text("a file where a directory is needed")
    monkeypatch.setattr(ff.requests, "get", lambda *a, **k: FakeResponse(200, weather_payload("m/s")))
    assert ff.run_forecast_capture() == []
    assert len([p for p in landed_files(tmp_path) if p.endswith(".json.gz")]) == 2


def test_display_path_falls_back_to_the_absolute_path_on_another_drive(monkeypatch):
    def other_drive(path, start):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(ff.os.path, "relpath", other_drive)
    shown = ff._display_path("/somewhere/else/file.json.gz")
    assert shown.endswith("somewhere/else/file.json.gz")
    assert "\\" not in shown
