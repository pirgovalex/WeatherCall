"""Fixture invariants: determinism, temporal/DST behaviour and NULL preservation.

Covers:
1. Fixture generator determinism across clean runs (SHA-256 byte identity).
2. Temporal invariants and the Europe/Sofia DST transition on 2026-10-25.
3. NULL preservation across the 6-hour gap, and the distinction from a true 0.0 precipitation value.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.make_fixtures import write_fixtures

MEASURE_COLUMNS = [
    "pm2_5",
    "pm10",
    "no2",
    "temperature_2m",
    "wind_gusts_10m",
    "precipitation",
]


def sha256_of_file(path: Path) -> str:
    """Return the hexadecimal SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_directory_files(directory: Path) -> dict[str, str]:
    """Return a mapping of relative POSIX path to SHA-256 for all files in a directory."""
    result = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            rel = p.relative_to(directory).as_posix()
            result[rel] = sha256_of_file(p)
    return result


def content_hash(path: Path) -> str:
    """SHA-256 of what a reader sees: Parquet files by schema and rows, other files by bytes.

    Parquet files record the version of the library that wrote them, so the bytes of an identical
    table change with the pyarrow version. Column types, names, nullability and values do not.
    """
    if path.suffix != ".parquet":
        return sha256_of_file(path)
    table = pq.read_table(path).replace_schema_metadata(None)
    hasher = hashlib.sha256(str(table.schema).encode("utf-8"))
    hasher.update(table.to_pandas().to_json(orient="split", date_format="iso", double_precision=15).encode("utf-8"))
    return hasher.hexdigest()


def content_hashes(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): content_hash(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


class TestFixtureDeterminism:
    """Challenge 1: Determinism across multiple clean directories and CLI invocations."""

    def test_multi_clean_directory_byte_identity(self):
        """Generate fixtures in 3 isolated clean directories and verify 100% SHA-256 identity."""
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            dirs = [base_path / f"run_{i}" for i in range(1, 4)]

            run_hashes = []
            for d in dirs:
                out_valid = d / "fixtures"
                out_invalid = d / "fixtures_invalid"
                write_fixtures(out_dir=out_valid, invalid_dir=out_invalid, seed=42)

                valid_hashes = hash_directory_files(out_valid)
                invalid_hashes = hash_directory_files(out_invalid)
                combined = {
                    **{f"valid/{k}": v for k, v in valid_hashes.items()},
                    **{f"invalid/{k}": v for k, v in invalid_hashes.items()},
                }
                run_hashes.append(combined)

            # Assert all 3 runs produced the exact same file keys
            keys_0 = set(run_hashes[0].keys())
            assert keys_0, "No files generated in run 0"
            for idx, r_hashes in enumerate(run_hashes[1:], start=1):
                assert set(r_hashes.keys()) == keys_0, f"File set mismatch in run {idx}"

            # Assert exact byte-for-byte SHA-256 equality across all runs
            for key in keys_0:
                h0 = run_hashes[0][key]
                h1 = run_hashes[1][key]
                h2 = run_hashes[2][key]
                assert h0 == h1 == h2, (
                    f"Determinism failure for {key}: run0={h0}, run1={h1}, run2={h2}"
                )

    def test_checked_in_fixtures_match_fresh_generator(self):
        """Verify repository checked-in fixtures match fresh generation with seed=42.

        Parquet files are compared by content, not bytes: the checked-in files may have been written
        by another pyarrow version. Byte identity within one environment is covered above.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            out_valid = tmp_path / "fixtures"
            out_invalid = tmp_path / "fixtures_invalid"
            write_fixtures(out_dir=out_valid, invalid_dir=out_invalid, seed=42)

            fresh_valid_hashes = content_hashes(out_valid)
            repo_valid_hashes = content_hashes(REPO_ROOT / "contracts" / "fixtures")

            fresh_invalid_hashes = content_hashes(out_invalid)
            repo_invalid_hashes = content_hashes(REPO_ROOT / "contracts" / "fixtures_invalid")

            assert fresh_valid_hashes == repo_valid_hashes, (
                f"Repository fixtures differ from fresh seed=42 generation: "
                f"{set(fresh_valid_hashes.items()) ^ set(repo_valid_hashes.items())}"
            )
            assert fresh_invalid_hashes == repo_invalid_hashes, (
                f"Repository invalid fixtures differ from fresh seed=42 generation: "
                f"{set(fresh_invalid_hashes.items()) ^ set(repo_invalid_hashes.items())}"
            )

    def test_subprocess_cli_determinism(self):
        """Execute make_fixtures.py as a standalone CLI subprocess across 2 runs."""
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            out1 = Path(tmp1) / "fixtures"
            inv1 = Path(tmp1) / "fixtures_invalid"
            out2 = Path(tmp2) / "fixtures"
            inv2 = Path(tmp2) / "fixtures_invalid"

            cmd1 = [
                sys.executable,
                str(REPO_ROOT / "contracts" / "make_fixtures.py"),
                "--out-dir",
                str(out1),
                "--invalid-dir",
                str(inv1),
                "--seed",
                "42",
            ]
            res1 = subprocess.run(cmd1, capture_output=True, text=True, check=True)
            assert res1.returncode == 0

            cmd2 = [
                sys.executable,
                str(REPO_ROOT / "contracts" / "make_fixtures.py"),
                "--out-dir",
                str(out2),
                "--invalid-dir",
                str(inv2),
                "--seed",
                "42",
            ]
            res2 = subprocess.run(cmd2, capture_output=True, text=True, check=True)
            assert res2.returncode == 0

            hashes1 = hash_directory_files(out1)
            hashes2 = hash_directory_files(out2)
            assert hashes1 == hashes2, "Subprocess CLI runs produced different SHA-256 hashes"


class TestTemporalInvariantsAndDST:
    """Challenge 2: Temporal invariants and Europe/Sofia DST transition on 2026-10-25."""

    @pytest.fixture
    def hourly_df(self) -> pd.DataFrame:
        parquet_path = REPO_ROOT / "contracts" / "fixtures" / "mart_weather_aq_hourly" / "latest" / "mart_weather_aq_hourly.parquet"
        assert parquet_path.exists(), f"Hourly parquet missing at {parquet_path}"
        return pd.read_parquet(parquet_path)

    def test_utc_alignment_and_precision(self, hourly_df: pd.DataFrame):
        """Verify all timestamps have minute=0, second=0, microsecond=0, nanosecond=0 and UTC tz."""
        ts = pd.to_datetime(hourly_df["valid_at_utc"])

        # Check minute, second, microsecond
        assert (ts.dt.minute != 0).sum() == 0, "Timestamps with non-zero minute found"
        assert (ts.dt.second != 0).sum() == 0, "Timestamps with non-zero second found"
        assert (ts.dt.microsecond != 0).sum() == 0, "Timestamps with non-zero microsecond found"
        assert (ts.dt.nanosecond != 0).sum() == 0, "Timestamps with non-zero nanosecond found"

        # Check timezone awareness is UTC
        assert ts.dt.tz is not None, "Timestamps are timezone naive"
        tz_name = str(ts.dt.tz).upper()
        assert "UTC" in tz_name

    def test_utc_date_2026_10_25_row_count(self, hourly_df: pd.DataFrame):
        """Verify exactly 24 rows exist in UTC on 2026-10-25."""
        ts = pd.to_datetime(hourly_df["valid_at_utc"])
        mask_2026_10_25_utc = (ts >= "2026-10-25 00:00:00+00:00") & (ts <= "2026-10-25 23:00:00+00:00")
        rows_utc = hourly_df[mask_2026_10_25_utc]

        assert len(rows_utc) == 24, (
            f"Expected exactly 24 rows in UTC on 2026-10-25, found {len(rows_utc)}"
        )
        assert rows_utc["valid_at_utc"].nunique() == 24, (
            "Timestamps on 2026-10-25 UTC are not strictly unique"
        )

        # Check expected hours 0..23 are all present
        expected_hours = set(range(24))
        actual_hours = set(pd.to_datetime(rows_utc["valid_at_utc"]).dt.hour)
        assert actual_hours == expected_hours, f"Missing UTC hours on 2026-10-25: {expected_hours - actual_hours}"

    def test_europe_sofia_dst_repeated_hour_and_25_hour_day(self, hourly_df: pd.DataFrame):
        """Verify converting to Europe/Sofia yields the 25-hour local day across the 03:00 repeated hour."""
        ts_utc = pd.to_datetime(hourly_df["valid_at_utc"])
        sofia_tz = ZoneInfo("Europe/Sofia")

        # Convert entire series to Europe/Sofia
        ts_sofia = ts_utc.dt.tz_convert(sofia_tz)

        # 1. Inspect the transition across the repeated 03:00 hour:
        # In Sofia (UTC+3 summer / UTC+2 winter):
        # 2026-10-25 00:00:00Z -> 2026-10-25 03:00:00+03:00 (EEST)
        # 2026-10-25 01:00:00Z -> 2026-10-25 03:00:00+02:00 (EET)
        ts_00z = pd.Timestamp("2026-10-25 00:00:00+00:00").tz_convert(sofia_tz)
        ts_01z = pd.Timestamp("2026-10-25 01:00:00+00:00").tz_convert(sofia_tz)

        assert ts_00z.hour == 3, f"00:00Z should map to local hour 3, got {ts_00z.hour}"
        assert ts_00z.utcoffset().total_seconds() == 3 * 3600, (
            f"00:00Z offset should be +03:00 (EEST), got {ts_00z.utcoffset()}"
        )

        assert ts_01z.hour == 3, f"01:00Z should map to repeated local hour 3, got {ts_01z.hour}"
        assert ts_01z.utcoffset().total_seconds() == 2 * 3600, (
            f"01:00Z offset should be +02:00 (EET), got {ts_01z.utcoffset()}"
        )

        # Confirm repeated local time wall-clock string across the two consecutive UTC hours
        wall_00z = ts_00z.strftime("%Y-%m-%d %H:%M:%S")
        wall_01z = ts_01z.strftime("%Y-%m-%d %H:%M:%S")
        assert wall_00z == "2026-10-25 03:00:00"
        assert wall_01z == "2026-10-25 03:00:00"
        assert ts_00z != ts_01z, "Timestamps with different offsets must not be identical points in time"

        # 2. Verify the 25-hour local day:
        # In Europe/Sofia, local date 2026-10-25 spans from:
        # 2026-10-25 00:00:00+03:00 (which is 2026-10-24 21:00:00Z)
        # through 2026-10-25 23:00:00+02:00 (which is 2026-10-25 21:00:00Z).
        # This comprises exactly 25 continuous hourly points.
        sofia_dates = ts_sofia.dt.strftime("%Y-%m-%d")
        mask_local_2026_10_25 = sofia_dates == "2026-10-25"
        local_day_rows = hourly_df[mask_local_2026_10_25]

        assert len(local_day_rows) == 25, (
            f"Expected exactly 25 hourly rows for Europe/Sofia local date 2026-10-25, found {len(local_day_rows)}"
        )

        # Confirm the 25 hours consist of:
        # - 3 hours from 2026-10-24 UTC (21:00, 22:00, 23:00 UTC)
        # - 22 hours from 2026-10-25 UTC (00:00 through 21:00 UTC)
        utc_ts_in_local_day = pd.to_datetime(local_day_rows["valid_at_utc"])
        utc_dates_breakdown = utc_ts_in_local_day.dt.strftime("%Y-%m-%d").value_counts().to_dict()
        assert utc_dates_breakdown == {"2026-10-25": 22, "2026-10-24": 3}, (
            f"Unexpected UTC date breakdown for Sofia local day: {utc_dates_breakdown}"
        )


class TestNullPreservation:
    """Challenge 3: NULL preservation across 6-hour gap and true zero (0.0) distinction."""

    @pytest.fixture
    def hourly_df(self) -> pd.DataFrame:
        parquet_path = REPO_ROOT / "contracts" / "fixtures" / "mart_weather_aq_hourly" / "latest" / "mart_weather_aq_hourly.parquet"
        return pd.read_parquet(parquet_path)

    def test_six_hour_gap_has_nulls_not_zero(self, hourly_df: pd.DataFrame):
        """Verify the 6-hour gap has NULL values in measure columns, NOT 0.0."""
        ts = pd.to_datetime(hourly_df["valid_at_utc"])
        # Gap definition: 2026-10-20 from 06:00:00Z to 11:00:00Z inclusive
        gap_mask = (ts >= "2026-10-20 06:00:00+00:00") & (ts <= "2026-10-20 11:00:00+00:00")
        gap_rows = hourly_df[gap_mask]

        assert len(gap_rows) == 6, f"Expected 6 rows in gap window, found {len(gap_rows)}"

        # For every row and every measure column:
        for idx, row in gap_rows.iterrows():
            ts_str = row["valid_at_utc"].strftime("%Y-%m-%dT%H:%M:%SZ")
            for col in MEASURE_COLUMNS:
                val = row[col]
                # Value MUST be NA/None/NaN
                assert pd.isna(val), (
                    f"Expected NULL at {ts_str} for column '{col}', but got value: {val!r}"
                )
                # Value MUST NOT be 0.0
                assert val != 0.0, (
                    f"Violation of NULL contract: {col} is 0.0 instead of NULL at {ts_str}"
                )

    def test_precipitation_true_zero_is_preserved(self, hourly_df: pd.DataFrame):
        """Verify true zero (0.0 precipitation) is preserved across non-raining hours."""
        precip = hourly_df["precipitation"]

        zero_count = (precip == 0.0).sum()
        assert zero_count > 0, "No 0.0 precipitation values found in dataset"
        # There should be many dry hours (most hours in autumn are dry)
        assert zero_count > 250, f"Expected substantial dry hours with 0.0, got {zero_count}"

        # Verify that these 0.0 values are not NULL
        zero_rows = hourly_df[precip == 0.0]
        assert not zero_rows["precipitation"].isna().any(), (
            "Rows identified as 0.0 should not be marked as NA"
        )

    def test_precipitation_null_distinct_from_zero(self, hourly_df: pd.DataFrame):
        """Verify precipitation has both true 0.0 and explicit NULL rows to ensure distinction."""
        precip = hourly_df["precipitation"]
        has_zeros = (precip == 0.0).any()
        has_nulls = precip.isna().any()

        assert has_zeros, "Dataset lacks true 0.0 precipitation"
        assert has_nulls, "Dataset lacks NULL precipitation"

        # Check specific isolated NULL precipitation on 2026-10-17 12:00:00Z
        ts = pd.to_datetime(hourly_df["valid_at_utc"])
        isolated_null_mask = ts == "2026-10-17 12:00:00+00:00"
        isolated_row = hourly_df[isolated_null_mask]
        assert len(isolated_row) == 1
        assert pd.isna(isolated_row["precipitation"].iloc[0]), (
            "2026-10-17 12:00:00Z must have NULL precipitation"
        )
        assert isolated_row["precipitation"].iloc[0] != 0.0

    def test_column_types_support_nulls(self, hourly_df: pd.DataFrame):
        """Verify measure columns are floating point dtypes capable of holding IEEE NaN / NULLs."""
        for col in MEASURE_COLUMNS:
            assert pd.api.types.is_float_dtype(hourly_df[col]), (
                f"Column {col} is type {hourly_df[col].dtype}, expected float"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
