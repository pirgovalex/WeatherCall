"""Shared fixtures for the API tests."""

import json
import os
import shutil
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from src.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "contracts" / "fixtures"

HOURLY_GLOB = str(FIXTURES / "mart_weather_aq_hourly" / "latest" / "*.parquet")
DAILY_GLOB = str(FIXTURES / "mart_aq_daily" / "latest" / "*.parquet")

ORIGIN = "http://localhost:5173"


@pytest.fixture
def manifest() -> dict:
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def client():
    app = create_app(allowed_origins=[ORIGIN], marts_dir=FIXTURES)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def lenient_client():
    """Client that returns 500 responses instead of re-raising server errors."""
    app = create_app(allowed_origins=[ORIGIN], marts_dir=FIXTURES)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def marts_copy(tmp_path) -> Path:
    """A writable copy of the fixture marts for tests that edit the manifest."""
    dest = tmp_path / "marts"
    shutil.copytree(FIXTURES, dest)
    return dest


def query(sql: str, *params):
    """Independent expected values, computed straight from the fixture Parquet files."""
    con = duckdb.connect()
    try:
        return con.execute(sql, list(params)).fetchall()
    finally:
        con.close()


def edit_manifest(marts_dir: Path, change) -> None:
    """Apply `change(manifest_dict)` to manifest.json and make sure its mtime moves forward."""
    path = Path(marts_dir) / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    later = path.stat().st_mtime + 10
    os.utime(path, (later, later))


def mart_glob(name: str) -> str:
    return str(FIXTURES / name / "latest" / "*.parquet")


def assert_envelope(res, status, code):
    assert res.status_code == status, res.text
    body = res.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
