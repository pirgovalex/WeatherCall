"""EEA UTD station capture: validation, atomic landing and same-day replacement."""

import io

import pandas as pd
import pytest

from plovdiv.extract import fetch_eea_utd


def _parquet(values):
    frame = pd.DataFrame(
        {
            "Start": pd.date_range("2026-10-08", periods=len(values), freq="h"),
            "Value": values,
            "Validity": [1] * len(values),
        }
    )
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    return buf.getvalue()


class _Response:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def landing(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_eea_utd, "record_run", lambda entry: None)
    return tmp_path / "landing"


def _serve(monkeypatch, content, status=200):
    monkeypatch.setattr(fetch_eea_utd.requests, "get", lambda *a, **k: _Response(content, status))


def _landed(landing):
    return list(landing.glob("source=eea/endpoint=utd_hourly/fetch_date=*/SPO-BG0051A_00005_100.parquet"))


def test_lands_a_valid_file_in_the_dated_partition(landing, monkeypatch):
    _serve(monkeypatch, _parquet([10.0, 12.0]))
    entry = fetch_eea_utd.land_one("BG0051A", "00005", str(landing))
    assert entry["status"] == "ok" and entry["rows"] == 2
    (path,) = _landed(landing)
    assert len(pd.read_parquet(path)) == 2
    assert not list(landing.rglob("*.tmp"))


def test_same_content_twice_is_reported_unchanged(landing, monkeypatch):
    _serve(monkeypatch, _parquet([10.0]))
    fetch_eea_utd.land_one("BG0051A", "00005", str(landing))
    assert fetch_eea_utd.land_one("BG0051A", "00005", str(landing))["status"] == "unchanged"


def test_newer_content_replaces_the_same_day_copy(landing, monkeypatch):
    _serve(monkeypatch, _parquet([10.0]))
    fetch_eea_utd.land_one("BG0051A", "00005", str(landing))
    _serve(monkeypatch, _parquet([10.0, 11.0, 12.0]))
    assert fetch_eea_utd.land_one("BG0051A", "00005", str(landing))["status"] == "ok"
    (path,) = _landed(landing)
    assert len(pd.read_parquet(path)) == 3


@pytest.mark.parametrize(
    "content, status",
    [(b"not a parquet file", 200), (_parquet([]), 200), (b"", 503)],
    ids=["garbage", "empty", "http-error"],
)
def test_bad_payload_fails_and_keeps_the_existing_copy(landing, monkeypatch, content, status):
    _serve(monkeypatch, _parquet([10.0]))
    fetch_eea_utd.land_one("BG0051A", "00005", str(landing))
    _serve(monkeypatch, content, status)
    assert fetch_eea_utd.land_one("BG0051A", "00005", str(landing))["status"] == "failed"
    (path,) = _landed(landing)
    assert len(pd.read_parquet(path)) == 1
