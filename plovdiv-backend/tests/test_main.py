import os
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from src.main import app, create_app, parse_allowed_origins


@pytest.fixture
def client():
    return TestClient(app)


def test_healthz_endpoint_status_and_payload(client):
    """Verify /healthz returns HTTP 200 with {'status': 'ok'}."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    data = response.json()
    assert data == {"status": "ok"}


def test_cors_headers_with_allowed_origin(client):
    """Verify CORS headers are returned for allowed origin."""
    allowed_origin = "http://localhost:3000"
    response = client.get("/healthz", headers={"Origin": allowed_origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == allowed_origin
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_cors_headers_with_disallowed_origin(client):
    """Verify CORS headers are absent for an untrusted origin."""
    untrusted_origin = "http://malicious-site.example.com"
    response = client.get("/healthz", headers={"Origin": untrusted_origin})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_options_request(client):
    """Verify OPTIONS preflight request handling for allowed origin."""
    allowed_origin = "http://localhost:5173"
    response = client.options(
        "/healthz",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == allowed_origin
    assert "GET" in response.headers.get("access-control-allow-methods", "")


def test_custom_cors_origins():
    """Verify app created with custom origins enforces them."""
    custom_origins = ["https://custom-dashboard.org"]
    custom_app = create_app(allowed_origins=custom_origins)
    custom_client = TestClient(custom_app)

    # Allowed custom origin
    res = custom_client.get("/healthz", headers={"Origin": "https://custom-dashboard.org"})
    assert res.headers.get("access-control-allow-origin") == "https://custom-dashboard.org"

    # Default localhost should now be rejected in this instance
    res_default = custom_client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in res_default.headers


def test_cors_wildcard_origin():
    """Verify wildcard origin configuration disables credentials per spec."""
    wildcard_app = create_app(allowed_origins=["*"])
    wildcard_client = TestClient(wildcard_app)

    res = wildcard_client.get("/healthz", headers={"Origin": "https://random-origin.org"})
    assert res.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in res.headers


def test_parse_allowed_origins_helper(monkeypatch):
    """Verify origin string parsing logic."""
    assert parse_allowed_origins("http://a.com, http://b.com") == ["http://a.com", "http://b.com"]

    monkeypatch.setenv("ALLOWED_ORIGINS", "http://c.com, http://d.com")
    assert parse_allowed_origins() == ["http://c.com", "http://d.com"]


def test_contracts_directory_presence():
    """Verify contracts directory was copied and contains critical files."""
    repo_root = Path(__file__).resolve().parent.parent
    contracts_dir = repo_root / "contracts"

    assert contracts_dir.is_dir(), "contracts directory does not exist"
    assert (contracts_dir / "openapi.yaml").is_file(), "openapi.yaml missing"
    assert (contracts_dir / "manifest.schema.json").is_file(), "manifest.schema.json missing"
    assert (contracts_dir / "schemas.py").is_file(), "schemas.py missing"
    assert (contracts_dir / "version.py").is_file(), "version.py missing"
    assert (contracts_dir / "fixtures" / "manifest.json").is_file(), "fixtures/manifest.json missing"
    assert (contracts_dir / "fixtures_invalid" / "manifest.json").is_file(), "fixtures_invalid/manifest.json missing"


def test_pyproject_toml_dependencies():
    """Verify pyproject.toml defines all required dependencies."""
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_file = repo_root / "pyproject.toml"

    assert pyproject_file.is_file(), "pyproject.toml missing"
    content = pyproject_file.read_text(encoding="utf-8")

    required_deps = ["fastapi", "uvicorn", "duckdb", "pytest", "httpx"]
    for dep in required_deps:
        assert dep in content, f"Dependency '{dep}' missing in pyproject.toml"


def test_healthz_unsupported_methods(client):
    """Verify non-GET methods to /healthz return 405 Method Not Allowed per contract."""
    for method in ["post", "put", "delete", "head"]:
        client_fn = getattr(client, method)
        response = client_fn("/healthz")
        assert response.status_code == 405



def test_cors_origins_fallback_env(monkeypatch):
    """Verify CORS_ORIGINS environment variable works when ALLOWED_ORIGINS is absent."""
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "http://example.org, http://example.net")
    assert parse_allowed_origins() == ["http://example.org", "http://example.net"]


def test_openapi_json_schema(client):
    """Verify /openapi.json contains /healthz route definition."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/healthz" in schema["paths"]
    assert "get" in schema["paths"]["/healthz"]


@pytest.mark.anyio
async def test_healthz_async_httpx():
    """Verify async request using httpx.AsyncClient with ASGITransport."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


