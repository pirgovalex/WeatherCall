"""Responses and the generated OpenAPI document must conform to contracts/openapi.yaml."""

from pathlib import Path

import pytest
import yaml
from openapi_schema_validator import OAS30Validator

CONTRACT = yaml.safe_load((Path(__file__).resolve().parent.parent / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))


def validate(instance, schema_name):
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": CONTRACT["components"]}
    errors = sorted(OAS30Validator(schema).iter_errors(instance), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


def resolve(schema, spec):
    while "$ref" in schema:
        node = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def enum_of(schema, spec):
    """Enum values of a parameter schema. OpenAPI 3.1 writes an optional enum as anyOf [enum, null]."""
    schema = resolve(schema, spec)
    for branch in schema.get("anyOf", [schema]):
        branch = resolve(branch, spec)
        if "enum" in branch:
            return branch["enum"]
    return None


def test_health_meta_series_and_daily_responses_validate(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    validate(client.get("/api/v1/meta").json(), "MetaResponse")
    series = client.get(
        "/api/v1/series",
        params={"variable": "pm2_5", "location": "plovdiv", "from": "2026-10-14T00:00:00Z", "to": "2026-10-27T23:00:00Z", "resolution": "hourly", "source_type": "analysis"},
    )
    validate(series.json(), "SeriesResponse")
    daily = client.get("/api/v1/aq/daily", params={"pollutant": "pm10", "location": "plovdiv", "from": "2026-10-14", "to": "2026-10-27"})
    validate(daily.json(), "DailyAqResponse")


def test_error_bodies_validate(client):
    not_found = client.get("/api/v1/series", params={"variable": "nope", "location": "plovdiv", "from": "2026-10-14T00:00:00Z", "to": "2026-10-15T00:00:00Z", "resolution": "hourly"})
    invalid = client.get("/api/v1/series", params={"variable": "pm2_5"})
    assert (not_found.status_code, invalid.status_code) == (404, 422)
    validate(not_found.json(), "ErrorEnvelope")
    validate(invalid.json(), "ErrorEnvelope")


@pytest.mark.parametrize("path", sorted(CONTRACT["paths"]))
def test_generated_openapi_covers_the_contract(client, path):
    generated = client.app.openapi()
    operations = CONTRACT["paths"][path]
    assert path in generated["paths"], f"{path} missing from the generated document"
    for method, contract_op in operations.items():
        op = generated["paths"][path][method]

        contract_params = {p["name"]: p for p in contract_op.get("parameters", [])}
        generated_params = {p["name"]: p for p in op.get("parameters", [])}
        assert set(generated_params) == set(contract_params), path
        for name, cp in contract_params.items():
            gp = generated_params[name]
            assert gp.get("required", False) == cp["required"], f"{path} {name} required"
            assert gp["in"] == cp["in"]
            if "enum" in cp["schema"]:
                assert enum_of(gp["schema"], generated) == cp["schema"]["enum"], f"{path} {name} enum"

        assert set(op["responses"]) == set(contract_op["responses"]), f"{path} {method} status codes"
        ok = contract_op["responses"]["200"]["content"]["application/json"]["schema"]
        got = op["responses"]["200"]["content"]["application/json"]["schema"]
        want_schema = resolve(ok, CONTRACT)
        got_schema = resolve(got, generated)
        assert set(got_schema.get("required", [])) >= set(want_schema.get("required", [])), f"{path} required fields"
        assert set(want_schema["properties"]) <= set(got_schema["properties"]), f"{path} properties"
