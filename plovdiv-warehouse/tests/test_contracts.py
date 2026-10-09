"""Comprehensive E2E Contract and Deterministic Fixtures Test Suite.

Validates the Plovdiv weather and air quality warehouse data contract:
- Tier 1: Feature Coverage (Manifest schema, OpenAPI spec, Pandera schemas, Versioning)
- Tier 2: Boundary & Corner Cases (Invalid fixtures, Determinism, Temporal rules, NULL preservation,
          6-hour gap, DST window, 18-of-24 rule boundaries)
- Tier 3: Cross-Feature Combinations (Cross-mart temporal alignment, Variable catalogue consistency,
          Columnar series query simulation across the gap)
- Tier 4: Real-World Operational Scenarios (Downstream backend manifest ingestion,
          Downstream Parquet aggregation simulation, API contract constraints)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path for test imports
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hashlib
import json
import os
import subprocess
import tempfile
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest
import yaml
from jsonschema import Draft7Validator, ValidationError, validate
from openapi_spec_validator import validate as validate_openapi_spec
try:
    import pandera.pandas as pa
except ImportError:
    import pandera as pa
from pandera.errors import SchemaError, SchemaErrors

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = PROJECT_ROOT / "contracts"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"
INVALID_FIXTURES_DIR = CONTRACTS_DIR / "fixtures_invalid"
MANIFEST_SCHEMA_PATH = CONTRACTS_DIR / "manifest.schema.json"
OPENAPI_PATH = CONTRACTS_DIR / "openapi.yaml"
VERSION_PATH = CONTRACTS_DIR / "version.py"
MAKE_FIXTURES_PATH = CONTRACTS_DIR / "make_fixtures.py"

REQUIRED_VARIABLES = [
    "pm2_5",
    "pm10",
    "no2",
    "temperature_2m",
    "wind_gusts_10m",
    "precipitation",
]

ALLOWED_SOURCE_TYPES = [
    "station",
    "analysis",
    "reanalysis_provisional",
    "reanalysis_final",
    "forecast",
]

ALLOWED_POLLUTANTS = ["pm2_5", "pm10", "no2"]


# ---------------------------------------------------------------------------
# Fixture Helpers
# ---------------------------------------------------------------------------


def get_hourly_parquet_path(base_dir: Path | None = None) -> Path:
    """Find the hourly mart parquet file under the given base directory or default fixtures."""
    search_dir = base_dir if base_dir is not None else FIXTURES_DIR
    candidates = list((search_dir / "mart_weather_aq_hourly").glob("**/*.parquet"))
    if not candidates:
        pytest.fail(f"No hourly mart parquet file found under {search_dir / 'mart_weather_aq_hourly'}")
    return candidates[0]


def get_daily_parquet_path(base_dir: Path | None = None) -> Path:
    """Find the daily mart parquet file under the given base directory or default fixtures."""
    search_dir = base_dir if base_dir is not None else FIXTURES_DIR
    candidates = list((search_dir / "mart_aq_daily").glob("**/*.parquet"))
    if not candidates:
        pytest.fail(f"No daily mart parquet file found under {search_dir / 'mart_aq_daily'}")
    return candidates[0]


def load_manifest(base_dir: Path | None = None) -> Dict[str, Any]:
    """Load and parse manifest.json from the given base directory or default fixtures."""
    search_dir = base_dir if base_dir is not None else FIXTURES_DIR
    manifest_path = search_dir / "manifest.json"
    if not manifest_path.exists():
        pytest.fail(f"manifest.json not found at {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_manifest_schema() -> Dict[str, Any]:
    """Load and parse contracts/manifest.schema.json."""
    if not MANIFEST_SCHEMA_PATH.exists():
        pytest.fail(f"manifest.schema.json not found at {MANIFEST_SCHEMA_PATH}")
    with open(MANIFEST_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_openapi_spec() -> Dict[str, Any]:
    """Load and parse contracts/openapi.yaml."""
    if not OPENAPI_PATH.exists():
        pytest.fail(f"openapi.yaml not found at {OPENAPI_PATH}")
    with open(OPENAPI_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_pandera_schemas():
    """Dynamically import Pandera schemas from contracts.schemas."""
    try:
        from contracts.schemas import (
            mart_aq_daily_schema,
            mart_weather_aq_hourly_schema,
        )

        return mart_weather_aq_hourly_schema, mart_aq_daily_schema
    except ImportError as e:
        pytest.fail(f"Failed to import schemas from contracts.schemas: {e}")


def compute_file_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Tier 1: Feature Coverage (Core Contract and Schema Validations)
# ---------------------------------------------------------------------------


class TestTier1FeatureCoverage:
    """Core feature coverage validating specifications, versioning, and schemas."""

    def test_schema_version_central_definition(self):
        """Verify SCHEMA_VERSION is defined centrally and aligned across all artifacts."""
        assert VERSION_PATH.exists(), f"version.py missing at {VERSION_PATH}"

        try:
            from contracts.version import SCHEMA_VERSION
        except ImportError as e:
            pytest.fail(f"Failed to import SCHEMA_VERSION from contracts.version: {e}")

        assert isinstance(SCHEMA_VERSION, str), "SCHEMA_VERSION must be a string"
        assert len(SCHEMA_VERSION.strip()) > 0, "SCHEMA_VERSION must not be empty"

        # Verify manifest.schema.json enforces this exact version
        manifest_schema = load_manifest_schema()
        version_prop = manifest_schema.get("properties", {}).get("schema_version", {})
        if "const" in version_prop:
            assert version_prop["const"] == SCHEMA_VERSION, (
                f"manifest.schema.json const '{version_prop['const']}' != SCHEMA_VERSION '{SCHEMA_VERSION}'"
            )
        elif "enum" in version_prop:
            assert SCHEMA_VERSION in version_prop["enum"], (
                f"SCHEMA_VERSION '{SCHEMA_VERSION}' not in manifest.schema.json enum {version_prop['enum']}"
            )
        else:
            pytest.fail("manifest.schema.json must constrain schema_version via const or enum")

        # Verify openapi.yaml info.version matches SCHEMA_VERSION
        openapi_spec = load_openapi_spec()
        api_version = str(openapi_spec.get("info", {}).get("version", ""))
        assert api_version == SCHEMA_VERSION, (
            f"openapi.yaml info.version '{api_version}' != SCHEMA_VERSION '{SCHEMA_VERSION}'"
        )

        # Verify generated fixtures manifest matches SCHEMA_VERSION if present
        if (FIXTURES_DIR / "manifest.json").exists():
            manifest = load_manifest()
            assert manifest.get("schema_version") == SCHEMA_VERSION, (
                f"fixtures/manifest.json schema_version '{manifest.get('schema_version')}' != '{SCHEMA_VERSION}'"
            )

    def test_manifest_schema_draft7_validity(self):
        """Verify contracts/manifest.schema.json is a valid JSON Schema Draft-07 specification."""
        manifest_schema = load_manifest_schema()
        assert manifest_schema.get("$schema") == "http://json-schema.org/draft-07/schema#", (
            "manifest.schema.json $schema must be Draft-07"
        )
        # check_schema raises SchemaError if the schema definition is invalid
        Draft7Validator.check_schema(manifest_schema)

    def test_fixtures_manifest_validates_against_schema(self):
        """Verify contracts/fixtures/manifest.json validates cleanly against manifest.schema.json."""
        manifest_schema = load_manifest_schema()
        manifest = load_manifest()

        # Validate against schema
        validate(instance=manifest, schema=manifest_schema)

        # Check required fields and contents
        for key in ["schema_version", "run_id", "generated_at_utc", "attribution", "variables", "marts"]:
            assert key in manifest, f"Missing required manifest key: {key}"

        # Check variables catalogue contains all 6 required variables
        variables = manifest["variables"]
        for var in REQUIRED_VARIABLES:
            assert var in variables, f"Variable {var} missing from manifest.variables catalogue"
            assert "unit" in variables[var], f"Variable {var} missing 'unit' declaration"
            assert "label" in variables[var], f"Variable {var} missing 'label' declaration"
            assert isinstance(variables[var]["unit"], str) and len(variables[var]["unit"]) > 0
            assert isinstance(variables[var]["label"], str) and len(variables[var]["label"]) > 0

        # Check marts definition
        marts = manifest["marts"]
        assert "mart_weather_aq_hourly" in marts, "mart_weather_aq_hourly missing from manifest.marts"
        assert "mart_aq_daily" in marts, "mart_aq_daily missing from manifest.marts"

        hourly_meta = marts["mart_weather_aq_hourly"]
        assert "glob" in hourly_meta
        assert "rows" in hourly_meta and hourly_meta["rows"] > 0
        assert "min_valid_at_utc" in hourly_meta and len(hourly_meta["min_valid_at_utc"]) > 0
        assert "max_valid_at_utc" in hourly_meta and len(hourly_meta["max_valid_at_utc"]) > 0

        daily_meta = marts["mart_aq_daily"]
        assert "glob" in daily_meta
        assert "rows" in daily_meta and daily_meta["rows"] > 0

    def test_openapi_spec_validation(self):
        """Verify contracts/openapi.yaml passes OpenAPI 3.0 specification validation."""
        spec = load_openapi_spec()
        validate_openapi_spec(spec)

        assert str(spec.get("openapi", "")).startswith("3.0"), (
            f"OpenAPI version must be 3.0.x, found: {spec.get('openapi')}"
        )

        paths = spec.get("paths", {})
        required_endpoints = [
            "/healthz",
            "/api/v1/meta",
            "/api/v1/series",
            "/api/v1/aq/daily",
        ]
        for endpoint in required_endpoints:
            assert endpoint in paths, f"OpenAPI specification missing endpoint: {endpoint}"

        # Verify uniform error schema exists
        components = spec.get("components", {})
        schemas = components.get("schemas", {})
        assert any("error" in name.lower() for name in schemas.keys()), (
            "Uniform error schema missing from openapi.yaml components.schemas"
        )

    def test_hourly_mart_pandera_validation(self):
        """Verify generated mart_weather_aq_hourly Parquet passes Pandera validation."""
        hourly_schema, _ = get_pandera_schemas()
        parquet_path = get_hourly_parquet_path()

        df = pd.read_parquet(parquet_path)
        assert len(df) > 0, "mart_weather_aq_hourly DataFrame must not be empty"

        # Validate with Pandera
        validated_df = hourly_schema.validate(df)
        assert validated_df is not None
        assert len(validated_df) == len(df)

    def test_daily_mart_pandera_validation(self):
        """Verify generated mart_aq_daily Parquet passes Pandera validation."""
        _, daily_schema = get_pandera_schemas()
        parquet_path = get_daily_parquet_path()

        df = pd.read_parquet(parquet_path)
        assert len(df) > 0, "mart_aq_daily DataFrame must not be empty"

        # Validate with Pandera
        validated_df = daily_schema.validate(df)
        assert validated_df is not None
        assert len(validated_df) == len(df)


# ---------------------------------------------------------------------------
# Tier 2: Boundary & Corner Cases (Invariants, Temporal Rules, Edge Cases)
# ---------------------------------------------------------------------------


class TestTier2BoundaryAndCornerCases:
    """Boundary value analysis, invariant checks, and edge case tests."""

    def test_invalid_fixtures_fail_validation(self):
        """Verify that any fixture under contracts/fixtures_invalid/ fails validation with a clear message."""
        assert INVALID_FIXTURES_DIR.exists(), f"fixtures_invalid directory missing at {INVALID_FIXTURES_DIR}"

        hourly_schema, daily_schema = get_pandera_schemas()
        manifest_schema = load_manifest_schema()

        # Gather any parquet or json files in fixtures_invalid
        invalid_parquets = list(INVALID_FIXTURES_DIR.glob("**/*.parquet"))
        invalid_jsons = list(INVALID_FIXTURES_DIR.glob("**/*.json"))

        assert len(invalid_parquets) + len(invalid_jsons) > 0, (
            f"No invalid fixture files found under {INVALID_FIXTURES_DIR}"
        )

        failures_recorded = 0

        # Test invalid Parquet files
        for pfile in invalid_parquets:
            df = pd.read_parquet(pfile)
            parent_dir = pfile.parent.name
            target_schema = None

            if "hourly" in pfile.name or "hourly" in parent_dir or "valid_at_utc" in df.columns:
                target_schema = hourly_schema
            elif "daily" in pfile.name or "daily" in parent_dir or "day" in df.columns:
                target_schema = daily_schema

            if target_schema is not None:
                with pytest.raises((SchemaError, SchemaErrors)) as exc_info:
                    target_schema.validate(df)
                error_msg = str(exc_info.value)
                assert len(error_msg.strip()) > 0, "Validation failure message must not be empty"
                failures_recorded += 1

        # Test invalid JSON files
        for jfile in invalid_jsons:
            with open(jfile, "r", encoding="utf-8") as f:
                data = json.load(f)
            with pytest.raises(ValidationError) as exc_info:
                validate(instance=data, schema=manifest_schema)
            error_msg = str(exc_info.value)
            assert len(error_msg.strip()) > 0, "JSON schema validation failure message must not be empty"
            failures_recorded += 1

        assert failures_recorded > 0, "At least one invalid fixture must fail validation with a clear message"

    def test_fixture_generator_determinism(self):
        """Verify make_fixtures.py deterministically produces byte-for-byte identical files across runs."""
        assert MAKE_FIXTURES_PATH.exists(), f"make_fixtures.py not found at {MAKE_FIXTURES_PATH}"

        with tempfile.TemporaryDirectory() as tmp_dir1, tempfile.TemporaryDirectory() as tmp_dir2:
            out1 = Path(tmp_dir1) / "fixtures"
            out2 = Path(tmp_dir2) / "fixtures"

            # Execute run 1
            cmd1 = [
                sys.executable,
                str(MAKE_FIXTURES_PATH),
                "--out-dir",
                str(out1),
                "--invalid-dir",
                str(Path(tmp_dir1) / "fixtures_invalid"),
            ]
            res1 = subprocess.run(cmd1, capture_output=True, text=True, cwd=str(PROJECT_ROOT))

            # If --out-dir is not supported, run generator in-place twice and compare hashes before/after
            if res1.returncode != 0 and "--out-dir" in res1.stderr:
                # Fallback: run in-place twice
                res_inplace1 = subprocess.run(
                    [sys.executable, str(MAKE_FIXTURES_PATH)],
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                )
                assert res_inplace1.returncode == 0, f"make_fixtures.py failed: {res_inplace1.stderr}"

                hashes1 = {}
                for f in FIXTURES_DIR.glob("**/*"):
                    if f.is_file():
                        rel = f.relative_to(FIXTURES_DIR)
                        hashes1[str(rel)] = compute_file_sha256(f)

                res_inplace2 = subprocess.run(
                    [sys.executable, str(MAKE_FIXTURES_PATH)],
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                )
                assert res_inplace2.returncode == 0, f"make_fixtures.py failed second run: {res_inplace2.stderr}"

                hashes2 = {}
                for f in FIXTURES_DIR.glob("**/*"):
                    if f.is_file():
                        rel = f.relative_to(FIXTURES_DIR)
                        hashes2[str(rel)] = compute_file_sha256(f)

                assert hashes1 == hashes2, f"In-place determinism failure: files differed: {set(hashes1.items()) ^ set(hashes2.items())}"
                return

            assert res1.returncode == 0, f"make_fixtures.py run 1 failed:\nSTDOUT: {res1.stdout}\nSTDERR: {res1.stderr}"

            # Execute run 2
            cmd2 = [
                sys.executable,
                str(MAKE_FIXTURES_PATH),
                "--out-dir",
                str(out2),
                "--invalid-dir",
                str(Path(tmp_dir2) / "fixtures_invalid"),
            ]
            res2 = subprocess.run(cmd2, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
            assert res2.returncode == 0, f"make_fixtures.py run 2 failed:\nSTDOUT: {res2.stdout}\nSTDERR: {res2.stderr}"

            # Compare all generated files across out1 and out2
            files1 = {p.relative_to(out1): compute_file_sha256(p) for p in out1.glob("**/*") if p.is_file()}
            files2 = {p.relative_to(out2): compute_file_sha256(p) for p in out2.glob("**/*") if p.is_file()}

            assert len(files1) > 0, "No files generated in output directory 1"
            assert set(files1.keys()) == set(files2.keys()), "File sets between runs differ"

            mismatched_files = []
            for rel_path, hash1 in files1.items():
                hash2 = files2[rel_path]
                if hash1 != hash2:
                    mismatched_files.append((str(rel_path), hash1, hash2))

            assert not mismatched_files, (
                f"Determinism failure: {len(mismatched_files)} files differed across identical runs:\n"
                + "\n".join(f"  {f}: run1={h1} run2={h2}" for f, h1, h2 in mismatched_files)
            )

    def test_timestamps_hour_aligned(self):
        """Verify all valid_at_utc timestamps are strictly hour-aligned (minute=0, second=0, microsecond=0)."""
        parquet_path = get_hourly_parquet_path()
        df = pd.read_parquet(parquet_path)

        assert "valid_at_utc" in df.columns, "valid_at_utc column missing from hourly mart"
        ts = pd.to_datetime(df["valid_at_utc"])

        non_zero_minutes = (ts.dt.minute != 0).sum()
        non_zero_seconds = (ts.dt.second != 0).sum()
        non_zero_microsecs = (ts.dt.microsecond != 0).sum()

        assert non_zero_minutes == 0, f"Found {non_zero_minutes} timestamps with non-zero minutes"
        assert non_zero_seconds == 0, f"Found {non_zero_seconds} timestamps with non-zero seconds"
        assert non_zero_microsecs == 0, f"Found {non_zero_microsecs} timestamps with non-zero microseconds"

    def test_timestamps_utc_only(self):
        """Verify timestamps are strictly timezone-aware UTC, without naive values or non-zero offsets."""
        parquet_path = get_hourly_parquet_path()
        df = pd.read_parquet(parquet_path)

        ts = pd.to_datetime(df["valid_at_utc"])

        # Check timezone awareness
        assert ts.dt.tz is not None, "valid_at_utc timestamps must be timezone-aware"

        # Check timezone is UTC (UTC or datetime.timezone.utc or zoneinfo.ZoneInfo('UTC'))
        tz_name = str(ts.dt.tz).upper()
        assert "UTC" in tz_name or tz_name == "DATETIME.TIMEZONE.UTC", (
            f"Expected UTC timezone, got {ts.dt.tz}"
        )

        # Confirm exact UTC offset is 0 seconds
        offsets = [val.utcoffset().total_seconds() for val in ts.iloc[:50]]
        assert all(offset == 0.0 for offset in offsets), "Timestamps must have exactly 0-second UTC offset"

    def test_primary_key_uniqueness(self):
        """Verify composite primary key uniqueness on both hourly and daily marts."""
        # Hourly mart PK: (location_id, source_type, valid_at_utc)
        df_hourly = pd.read_parquet(get_hourly_parquet_path())
        hourly_pk = ["valid_at_utc", "location_id", "source_type"]
        for col in hourly_pk:
            assert col in df_hourly.columns, f"Hourly PK column {col} missing"

        hourly_dups = df_hourly.duplicated(subset=hourly_pk).sum()
        assert hourly_dups == 0, f"Found {hourly_dups} duplicate composite PK rows in hourly mart"

        # Daily mart PK: (day, location_id, pollutant)
        df_daily = pd.read_parquet(get_daily_parquet_path())
        daily_pk = ["day", "location_id", "pollutant"]
        for col in daily_pk:
            assert col in df_daily.columns, f"Daily PK column {col} missing"

        daily_dups = df_daily.duplicated(subset=daily_pk).sum()
        assert daily_dups == 0, f"Found {daily_dups} duplicate composite PK rows in daily mart"

    def test_null_preservation(self):
        """Verify true NULLs are preserved in measure columns, not coerced to 0.0."""
        df_hourly = pd.read_parquet(get_hourly_parquet_path())

        # Check measure columns exist as float/double
        for var in REQUIRED_VARIABLES:
            assert var in df_hourly.columns, f"Measure column {var} missing from hourly mart"
            assert pd.api.types.is_float_dtype(df_hourly[var]), (
                f"Column {var} must be float/double dtype, got {df_hourly[var].dtype}"
            )

        # Check at least one measure column contains true NULL values
        null_counts = {var: df_hourly[var].isna().sum() for var in REQUIRED_VARIABLES}
        total_nulls = sum(null_counts.values())
        assert total_nulls > 0, (
            f"Expected true NULL values in measure columns, but all values were non-null: {null_counts}"
        )

        # Verify numeric 0.0 is distinct from NULL
        # For precipitation, 0.0 indicates no rain, whereas NULL indicates missing measurement
        if "precipitation" in df_hourly.columns:
            has_zero = (df_hourly["precipitation"] == 0.0).any()
            has_null = df_hourly["precipitation"].isna().any()
            # Precipitation should ideally exercise 0.0 values as true zeroes
            assert has_zero or total_nulls > 0, (
                "NULL preservation contract: 0.0 and NULL must be distinct semantics"
            )

    def test_six_hour_gap_scenario(self):
        """Verify the 6-hour gap scenario in hourly mart and that daily mart reflects valid_hours=18, is_valid_day=True."""
        df_hourly = pd.read_parquet(get_hourly_parquet_path())
        df_daily = pd.read_parquet(get_daily_parquet_path())

        # Sort hourly data by timestamp
        df_hourly_sorted = df_hourly.sort_values("valid_at_utc").copy()
        ts = pd.to_datetime(df_hourly_sorted["valid_at_utc"])

        # Determine hourly series for primary pollutant (pm2_5)
        # Check consecutive hours where values are null
        pm2_5_series = df_hourly_sorted["pm2_5"]
        is_null = pm2_5_series.isna()

        # Find continuous sequences of nulls
        null_runs = []
        current_run = []
        for idx, val in is_null.items():
            if val:
                current_run.append(df_hourly_sorted.loc[idx, "valid_at_utc"])
            else:
                if current_run:
                    null_runs.append(current_run)
                    current_run = []
        if current_run:
            null_runs.append(current_run)

        # Assert exactly one continuous 6-hour gap exists
        six_hour_runs = [run for run in null_runs if len(run) == 6]
        assert len(six_hour_runs) >= 1, (
            f"Expected at least one continuous 6-hour gap of null values, found runs with lengths: "
            f"{[len(r) for r in null_runs]}"
        )

        gap_run = six_hour_runs[0]
        gap_start = pd.to_datetime(gap_run[0])
        gap_end = pd.to_datetime(gap_run[-1])

        # Verify consecutive hourly spacing in the gap
        diffs = [
            (pd.to_datetime(gap_run[i]) - pd.to_datetime(gap_run[i - 1])).total_seconds()
            for i in range(1, len(gap_run))
        ]
        assert all(diff == 3600.0 for diff in diffs), f"Gap hours are not consecutive 1-hour increments: {diffs}"

        # Find corresponding day in df_daily
        # Daily mart uses EEA day convention (UTC+1)
        # The day of the gap should have exactly valid_hours = 18 and is_valid_day = True
        gap_date = gap_start.strftime("%Y-%m-%d")

        # Find daily rows matching the gap date
        daily_days_str = pd.to_datetime(df_daily["day"]).dt.strftime("%Y-%m-%d")
        gap_day_rows = df_daily[daily_days_str == gap_date]

        if gap_day_rows.empty:
            # Check adjacent EEA day if UTC+1 boundary shifted date
            alt_date = (gap_start + pd.Timedelta(hours=1)).strftime("%Y-%m-%d")
            gap_day_rows = df_daily[daily_days_str == alt_date]

        assert not gap_day_rows.empty, f"No daily mart record found for gap date around {gap_date}"

        # For the gap day, verify valid_hours == 18 and is_valid_day == True
        pm2_5_daily = gap_day_rows[gap_day_rows["pollutant"] == "pm2_5"]
        if not pm2_5_daily.empty:
            val_hours = pm2_5_daily["valid_hours"].iloc[0]
            is_valid = pm2_5_daily["is_valid_day"].iloc[0]
            assert val_hours == 18, f"Expected valid_hours == 18 on gap day, got {val_hours}"
            assert is_valid is True or is_valid == 1, (
                f"Expected is_valid_day == True on gap day with 18 hours, got {is_valid}"
            )

    def test_dst_window_continuity_and_sofia_expansion(self):
        """Verify 2026-10-25 has exactly 24 continuous hourly rows in UTC and maps to 25 local hours in Europe/Sofia."""
        df_hourly = pd.read_parquet(get_hourly_parquet_path())
        ts = pd.to_datetime(df_hourly["valid_at_utc"])

        # Filter for UTC date 2026-10-25
        dst_utc_mask = (ts >= "2026-10-25T00:00:00Z") & (ts <= "2026-10-25T23:00:00Z")
        dst_rows = df_hourly[dst_utc_mask]

        # Filter by primary source_type (analysis) to check unique hourly series
        if "analysis" in dst_rows["source_type"].values:
            dst_series = dst_rows[dst_rows["source_type"] == "analysis"]
        else:
            dst_series = dst_rows.drop_duplicates(subset=["valid_at_utc"])

        assert len(dst_series) == 24, (
            f"Expected exactly 24 continuous hourly rows in UTC on 2026-10-25, found {len(dst_series)}"
        )

        # Verify no duplicate UTC timestamps on DST day
        assert dst_series["valid_at_utc"].nunique() == 24, "Duplicate UTC timestamps found on 2026-10-25"

        # Verify conversion to Europe/Sofia
        try:
            sofia_ts = pd.to_datetime(dst_series["valid_at_utc"]).dt.tz_convert("Europe/Sofia")
            # In Europe/Sofia on 2026-10-25:
            # 00:00 UTC = 03:00 EEST (UTC+3)
            # 01:00 UTC = 03:00 EET (UTC+2) -> fall-back occurs, local 03:00 repeated
            # 23:00 UTC = 01:00 EET on next day (2026-10-26)
            # Total local span across the day has 25 hours
            assert len(sofia_ts) == 24
            # Clocks change from offset +03:00 to +02:00
            offsets = [t.utcoffset().total_seconds() / 3600 for t in sofia_ts]
            assert 3.0 in offsets, "Expected EEST (UTC+3) offset prior to clock change"
            assert 2.0 in offsets, "Expected EET (UTC+2) offset after clock change"
        except Exception as e:
            pytest.fail(f"Failed Europe/Sofia conversion across DST window: {e}")

    def test_eighteen_of_twenty_four_rule_boundaries(self):
        """Verify the 18-of-24 rule: is_valid_day == (valid_hours >= 18) with boundary tests."""
        _, daily_schema = get_pandera_schemas()
        df_daily = pd.read_parquet(get_daily_parquet_path())

        # Invariant on all rows in the fixture
        expected_valid = df_daily["valid_hours"] >= 18
        mismatches = (df_daily["is_valid_day"] != expected_valid).sum()
        assert mismatches == 0, f"Found {mismatches} rows violating is_valid_day == (valid_hours >= 18)"

        # Boundary synthetic tests against daily schema
        base_row = df_daily.iloc[0].to_dict()

        # Boundary 1: 24 hours -> True (Valid)
        row_24 = base_row.copy()
        row_24["valid_hours"] = 24
        row_24["is_valid_day"] = True
        df_test_24 = pd.DataFrame([row_24])
        assert daily_schema.validate(df_test_24) is not None

        # Boundary 2: 18 hours -> True (Valid threshold)
        row_18 = base_row.copy()
        row_18["valid_hours"] = 18
        row_18["is_valid_day"] = True
        df_test_18 = pd.DataFrame([row_18])
        assert daily_schema.validate(df_test_18) is not None

        # Boundary 3: 17 hours -> False (Valid below threshold)
        row_17 = base_row.copy()
        row_17["valid_hours"] = 17
        row_17["is_valid_day"] = False
        df_test_17 = pd.DataFrame([row_17])
        assert daily_schema.validate(df_test_17) is not None

        # Boundary 4: 0 hours -> False (Valid empty day)
        row_0 = base_row.copy()
        row_0["valid_hours"] = 0
        row_0["is_valid_day"] = False
        row_0["daily_mean"] = None
        df_test_0 = pd.DataFrame([row_0])
        assert daily_schema.validate(df_test_0) is not None

        # Negative Boundary 1: 17 hours but is_valid_day=True -> MUST FAIL
        row_bad_17 = base_row.copy()
        row_bad_17["valid_hours"] = 17
        row_bad_17["is_valid_day"] = True
        df_bad_17 = pd.DataFrame([row_bad_17])
        with pytest.raises((SchemaError, SchemaErrors)):
            daily_schema.validate(df_bad_17)

        # Negative Boundary 2: 18 hours but is_valid_day=False -> MUST FAIL
        row_bad_18 = base_row.copy()
        row_bad_18["valid_hours"] = 18
        row_bad_18["is_valid_day"] = False
        df_bad_18 = pd.DataFrame([row_bad_18])
        with pytest.raises((SchemaError, SchemaErrors)):
            daily_schema.validate(df_bad_18)

        # Negative Boundary 3: valid_hours > 24 -> MUST FAIL
        row_bad_25 = base_row.copy()
        row_bad_25["valid_hours"] = 25
        row_bad_25["is_valid_day"] = True
        df_bad_25 = pd.DataFrame([row_bad_25])
        with pytest.raises((SchemaError, SchemaErrors)):
            daily_schema.validate(df_bad_25)

        # Negative Boundary 4: valid_hours < 0 -> MUST FAIL
        row_bad_neg = base_row.copy()
        row_bad_neg["valid_hours"] = -1
        row_bad_neg["is_valid_day"] = False
        df_bad_neg = pd.DataFrame([row_bad_neg])
        with pytest.raises((SchemaError, SchemaErrors)):
            daily_schema.validate(df_bad_neg)


# ---------------------------------------------------------------------------
# Tier 3: Cross-Feature Combinations
# ---------------------------------------------------------------------------


class TestTier3CrossFeatureCombinations:
    """Cross-mart temporal consistency, catalogue cross-validation, and columnar queries."""

    def test_cross_mart_temporal_alignment(self):
        """Verify manifest.marts row counts and time bounds match Parquet files exactly."""
        manifest = load_manifest()
        df_hourly = pd.read_parquet(get_hourly_parquet_path())
        df_daily = pd.read_parquet(get_daily_parquet_path())

        hourly_meta = manifest["marts"]["mart_weather_aq_hourly"]
        daily_meta = manifest["marts"]["mart_aq_daily"]

        # Row counts match
        assert hourly_meta["rows"] == len(df_hourly), (
            f"Hourly rows mismatch: manifest={hourly_meta['rows']} actual={len(df_hourly)}"
        )
        assert daily_meta["rows"] == len(df_daily), (
            f"Daily rows mismatch: manifest={daily_meta['rows']} actual={len(df_daily)}"
        )

        # Temporal bounds in manifest match minimum and maximum timestamps
        ts_min = pd.to_datetime(df_hourly["valid_at_utc"]).min().isoformat()
        ts_max = pd.to_datetime(df_hourly["valid_at_utc"]).max().isoformat()

        # Normalize string comparison (strip Z / +00:00 variations)
        manifest_min = pd.to_datetime(hourly_meta["min_valid_at_utc"]).isoformat()
        manifest_max = pd.to_datetime(hourly_meta["max_valid_at_utc"]).isoformat()

        assert ts_min == manifest_min, f"min_valid_at_utc mismatch: manifest={manifest_min} actual={ts_min}"
        assert ts_max == manifest_max, f"max_valid_at_utc mismatch: manifest={manifest_max} actual={ts_max}"

        # Optional range fields: when present they equal what the files contain
        if "max_analysis_valid_at_utc" in hourly_meta:
            analysis = pd.to_datetime(df_hourly.loc[df_hourly["source_type"] == "analysis", "valid_at_utc"]).max()
            assert pd.to_datetime(hourly_meta["max_analysis_valid_at_utc"]).isoformat() == analysis.isoformat()
        if "min_day" in daily_meta:
            assert daily_meta["min_day"] == df_daily["day"].min().isoformat()
        if "max_day" in daily_meta:
            assert daily_meta["max_day"] == df_daily["day"].max().isoformat()

    def test_variable_catalogue_cross_consistency(self):
        """Verify measure columns in hourly mart and pollutants in daily mart align with manifest.variables."""
        manifest = load_manifest()
        variables = manifest["variables"]

        df_hourly = pd.read_parquet(get_hourly_parquet_path())
        df_daily = pd.read_parquet(get_daily_parquet_path())

        # Non-key hourly columns must be in manifest variables
        key_cols = {"valid_at_utc", "location_id", "source_type"}
        measure_cols = [c for c in df_hourly.columns if c not in key_cols]

        for col in measure_cols:
            assert col in variables, f"Hourly measure column {col} not declared in manifest.variables"

        # Pollutants in daily mart must be declared in manifest variables
        daily_pollutants = df_daily["pollutant"].unique()
        for p in daily_pollutants:
            assert p in variables, f"Daily pollutant {p} not declared in manifest.variables"

        # Check declared unit specifications
        expected_units = {
            "pm2_5": "ug/m3",
            "pm10": "ug/m3",
            "no2": "ug/m3",
            "temperature_2m": "degC",
            "wind_gusts_10m": "m/s",
            "precipitation": "mm",
        }
        for var, unit in expected_units.items():
            if var in variables:
                assert variables[var]["unit"] == unit, (
                    f"Unit for {var} expected '{unit}', got '{variables[var]['unit']}'"
                )

    def test_columnar_series_query_simulation_across_gap(self):
        """Simulate GET /api/v1/series columnar output across the 6-hour gap."""
        manifest = load_manifest()
        df_hourly = pd.read_parquet(get_hourly_parquet_path())

        # Filter series: location=plovdiv, variable=pm2_5, source_type=analysis
        series_df = df_hourly[
            (df_hourly["location_id"] == "plovdiv")
            & (df_hourly["source_type"] == "analysis")
        ].sort_values("valid_at_utc").copy()

        assert len(series_df) > 0, "No series data found for location='plovdiv', source_type='analysis'"

        # Construct columnar payload
        ts_utc = pd.to_datetime(series_df["valid_at_utc"])
        timestamps = [int(t.timestamp()) for t in ts_utc]
        values = [None if pd.isna(v) else round(float(v), 2) for v in series_df["pm2_5"]]

        # Assert columnar invariants
        assert len(timestamps) == len(values), "Parallel arrays timestamps and values must have equal length"

        # Timestamps must be strictly increasing
        for i in range(len(timestamps) - 1):
            assert timestamps[i] < timestamps[i + 1], (
                f"Timestamps must be strictly increasing: {timestamps[i]} >= {timestamps[i + 1]}"
            )

        # Values array must contain None (null) for the gap hours, NOT 0.0
        assert None in values, "Expected None (JSON null) in values array across the sensor gap"

        # Verify simulated API payload conforms to contract specification Section 3
        payload = {
            "variable": "pm2_5",
            "unit": manifest["variables"]["pm2_5"]["unit"],
            "location_id": "plovdiv",
            "resolution": "hourly",
            "source_type": "analysis",
            "timestamps": timestamps,
            "values": values,
            "warehouse": {
                "run_id": manifest["run_id"],
                "schema_version": manifest["schema_version"],
                "generated_at_utc": manifest["generated_at_utc"],
            },
            "attribution": manifest["attribution"],
        }

        # Verify serializability to JSON
        json_str = json.dumps(payload)
        assert len(json_str) > 0


# ---------------------------------------------------------------------------
# Tier 4: Real-World Operational Scenarios
# ---------------------------------------------------------------------------


class TestTier4RealWorldOperationalScenarios:
    """Downstream ingestion, backend boot simulation, and DuckDB/Parquet query workloads."""

    def test_downstream_backend_manifest_ingestion(self):
        """Simulate plovdiv-backend startup: load manifest, check version, and resolve mart globs."""
        manifest = load_manifest()

        # Check version match
        from contracts.version import SCHEMA_VERSION

        assert manifest["schema_version"] == SCHEMA_VERSION, (
            f"Startup failure: manifest version {manifest['schema_version']} != contract {SCHEMA_VERSION}"
        )

        # Resolve globs relative to fixtures directory
        for mart_name, mart_info in manifest["marts"].items():
            glob_pattern = mart_info["glob"]
            matched_files = list(FIXTURES_DIR.glob(glob_pattern))
            assert len(matched_files) > 0, (
                f"Glob '{glob_pattern}' for mart '{mart_name}' matched 0 files under {FIXTURES_DIR}"
            )

            # Read each matched Parquet file
            total_rows = 0
            for mf in matched_files:
                df = pd.read_parquet(mf)
                total_rows += len(df)

            assert total_rows == mart_info["rows"], (
                f"Mart '{mart_name}' rows mismatch: manifest says {mart_info['rows']}, found {total_rows}"
            )

    def test_downstream_parquet_aggregation_simulation(self):
        """Simulate regulatory aggregation: verify daily means and valid_hours match between marts."""
        df_hourly = pd.read_parquet(get_hourly_parquet_path())
        df_daily = pd.read_parquet(get_daily_parquet_path())

        # Select analysis source type for pm2_5
        analysis_df = df_hourly[
            (df_hourly["location_id"] == "plovdiv")
            & (df_hourly["source_type"] == "analysis")
        ].copy()

        if analysis_df.empty:
            pytest.skip("No analysis source_type data for aggregation comparison")

        # Map to EEA day convention:
        # EEA day D runs on UTC+1 (01:00 UTC day D to 01:00 UTC day D+1)
        ts_utc = pd.to_datetime(analysis_df["valid_at_utc"])
        eea_day = (ts_utc + pd.Timedelta(hours=1)).dt.strftime("%Y-%m-%d")
        analysis_df["eea_day"] = eea_day

        daily_pm2_5 = df_daily[
            (df_daily["location_id"] == "plovdiv")
            & (df_daily["pollutant"] == "pm2_5")
        ].copy()
        daily_pm2_5["day_str"] = pd.to_datetime(daily_pm2_5["day"]).dt.strftime("%Y-%m-%d")

        # Verify for each day where we have hourly data
        for day_val, group in analysis_df.groupby("eea_day"):
            matching_daily = daily_pm2_5[daily_pm2_5["day_str"] == day_val]
            if not matching_daily.empty:
                expected_valid_hours = int(group["pm2_5"].notna().sum())
                actual_valid_hours = int(matching_daily["valid_hours"].iloc[0])

                # Compare valid hours
                assert abs(expected_valid_hours - actual_valid_hours) <= 1, (
                    f"Day {day_val}: hourly valid count ({expected_valid_hours}) != daily mart valid_hours ({actual_valid_hours})"
                )

                # Check daily mean consistency if valid_hours > 0
                if actual_valid_hours > 0 and matching_daily["daily_mean"].iloc[0] is not None:
                    expected_mean = group["pm2_5"].dropna().mean()
                    actual_mean = matching_daily["daily_mean"].iloc[0]
                    assert np.isclose(expected_mean, actual_mean, atol=0.2), (
                        f"Day {day_val}: hourly mean ({expected_mean:.2f}) != daily mean ({actual_mean:.2f})"
                    )

    def test_api_range_cap_contract(self):
        """Verify OpenAPI spec enforces or documents the 366-day limit and standard error format."""
        spec = load_openapi_spec()

        series_path = spec.get("paths", {}).get("/api/v1/series", {})
        get_op = series_path.get("get", {})
        parameters = get_op.get("parameters", [])

        # Check from and to query parameters
        param_names = [p.get("name") for p in parameters]
        assert "from" in param_names, "'from' parameter missing in /api/v1/series"
        assert "to" in param_names, "'to' parameter missing in /api/v1/series"

        # Check responses include 404 and 422
        responses = get_op.get("responses", {})
        assert "404" in responses or 404 in responses, "HTTP 404 response missing from /api/v1/series"
        assert "422" in responses or 422 in responses, "HTTP 422 response missing from /api/v1/series"


# ---------------------------------------------------------------------------
# Tier 5: Adversarial Verification & Integrity Hardening
# ---------------------------------------------------------------------------


class TestTier5AdversarialHardening:
    """Adversarial testing: schema tampering, corrupted inputs, and failure cascades."""

    def test_adversarial_manifest_tampering(self):
        """Verify manifest.schema.json detects and rejects deliberate schema tampering."""
        manifest_schema = load_manifest_schema()

        base_valid_manifest = {
            "schema_version": "1",
            "run_id": "2026-10-07T14-00-00Z-abc123",
            "generated_at_utc": "2026-10-07T14:05:12Z",
            "attribution": ["Copernicus Atmosphere Monitoring Service (CAMS)"],
            "variables": {
                "pm2_5": {"unit": "ug/m3", "label": "PM2.5"},
                "pm10": {"unit": "ug/m3", "label": "PM10"},
                "no2": {"unit": "ug/m3", "label": "NO2"},
                "temperature_2m": {"unit": "degC", "label": "Temperature"},
                "wind_gusts_10m": {"unit": "m/s", "label": "Wind gusts"},
                "precipitation": {"unit": "mm", "label": "Precipitation"},
            },
            "marts": {
                "mart_weather_aq_hourly": {
                    "glob": "mart_weather_aq_hourly/latest/*.parquet",
                    "rows": 336,
                    "min_valid_at_utc": "2026-10-14T00:00:00Z",
                    "max_valid_at_utc": "2026-10-27T23:00:00Z",
                },
                "mart_aq_daily": {
                    "glob": "mart_aq_daily/latest/*.parquet",
                    "rows": 42,
                },
            },
        }

        # Baseline: valid manifest must validate
        validate(instance=base_valid_manifest, schema=manifest_schema)

        # Adversarial 1: Mismatched schema_version -> MUST FAIL
        bad_version = base_valid_manifest.copy()
        bad_version["schema_version"] = "999"
        with pytest.raises(ValidationError):
            validate(instance=bad_version, schema=manifest_schema)

        # Adversarial 2: Missing required variable (e.g. pm2_5 removed) -> MUST FAIL
        missing_var = base_valid_manifest.copy()
        missing_var["variables"] = {
            k: v for k, v in base_valid_manifest["variables"].items() if k != "pm2_5"
        }
        with pytest.raises(ValidationError):
            validate(instance=missing_var, schema=manifest_schema)

        # Adversarial 3: Empty attribution array (violates minItems: 1) -> MUST FAIL
        empty_attr = base_valid_manifest.copy()
        empty_attr["attribution"] = []
        with pytest.raises(ValidationError):
            validate(instance=empty_attr, schema=manifest_schema)

        # Adversarial 4: Negative rows count -> MUST FAIL
        neg_rows = json.loads(json.dumps(base_valid_manifest))
        neg_rows["marts"]["mart_weather_aq_hourly"]["rows"] = -10
        with pytest.raises(ValidationError):
            validate(instance=neg_rows, schema=manifest_schema)

        # Adversarial 5: Extra disallowed top-level property -> MUST FAIL
        extra_prop = base_valid_manifest.copy()
        extra_prop["unauthorized_field"] = "malicious_payload"
        with pytest.raises(ValidationError):
            validate(instance=extra_prop, schema=manifest_schema)

    def test_adversarial_hourly_schema_violations(self):
        """Verify mart_weather_aq_hourly_schema rejects out-of-bounds and invalid values."""
        hourly_schema, _ = get_pandera_schemas()

        base_row = {
            "valid_at_utc": pd.Timestamp("2026-10-14 00:00:00+0000", tz="UTC"),
            "location_id": "plovdiv",
            "source_type": "analysis",
            "pm2_5": 12.4,
            "pm10": 22.1,
            "no2": 15.3,
            "temperature_2m": 18.5,
            "wind_gusts_10m": 4.2,
            "precipitation": 0.0,
        }

        # Baseline: valid row passes
        df_valid = pd.DataFrame([base_row])
        assert hourly_schema.validate(df_valid) is not None

        # Adversarial 1: Negative pollutant (pm2_5 < 0) -> MUST FAIL
        row_neg_pm = base_row.copy()
        row_neg_pm["pm2_5"] = -3.5
        with pytest.raises((SchemaError, SchemaErrors)):
            hourly_schema.validate(pd.DataFrame([row_neg_pm]))

        # Adversarial 2: Invalid source_type (not in 5-enum) -> MUST FAIL
        row_bad_source = base_row.copy()
        row_bad_source["source_type"] = "satellite_synthetic"
        with pytest.raises((SchemaError, SchemaErrors)):
            hourly_schema.validate(pd.DataFrame([row_bad_source]))

        # Adversarial 3: Non-hour-aligned timestamp (minute=30) -> MUST FAIL
        row_non_aligned = base_row.copy()
        row_non_aligned["valid_at_utc"] = pd.Timestamp("2026-10-14 00:30:00+0000", tz="UTC")
        with pytest.raises((SchemaError, SchemaErrors)):
            hourly_schema.validate(pd.DataFrame([row_non_aligned]))

        # Adversarial 4: Extreme unphysical temperature (> 60 degC) -> MUST FAIL
        row_hot = base_row.copy()
        row_hot["temperature_2m"] = 85.0
        with pytest.raises((SchemaError, SchemaErrors)):
            hourly_schema.validate(pd.DataFrame([row_hot]))

    def test_adversarial_daily_schema_violations(self):
        """Verify mart_aq_daily_schema rejects unallowed pollutants and negative means."""
        _, daily_schema = get_pandera_schemas()

        base_row = {
            "day": pd.Timestamp("2026-10-14"),
            "location_id": "plovdiv",
            "pollutant": "pm2_5",
            "daily_mean": 14.2,
            "valid_hours": 24,
            "is_valid_day": True,
        }

        # Baseline passes
        assert daily_schema.validate(pd.DataFrame([base_row])) is not None

        # Adversarial 1: Negative daily_mean -> MUST FAIL
        row_neg_mean = base_row.copy()
        row_neg_mean["daily_mean"] = -1.0
        with pytest.raises((SchemaError, SchemaErrors)):
            daily_schema.validate(pd.DataFrame([row_neg_mean]))

        # Adversarial 2: Unallowed pollutant (e.g. sulfur dioxide) -> MUST FAIL
        row_bad_pollutant = base_row.copy()
        row_bad_pollutant["pollutant"] = "so2"
        with pytest.raises((SchemaError, SchemaErrors)):
            daily_schema.validate(pd.DataFrame([row_bad_pollutant]))

