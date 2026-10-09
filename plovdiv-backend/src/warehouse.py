"""Access to the published marts: manifest loading, startup checks and read-only queries."""

from __future__ import annotations

import glob
import importlib.util
import json
import logging
import os
import math
import tempfile
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import jsonschema

from .errors import unavailable
from .validation import IDENTIFIER

log = logging.getLogger("plovdiv.api")

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
DEFAULT_MARTS_DIR = CONTRACTS_DIR / "fixtures"

HOURLY_MART = "mart_weather_aq_hourly"
DAILY_MART = "mart_aq_daily"
TREND_MART = "mart_aq_trend_monthly"
RISK_MART = "mart_aq_risk_daily"
DRIVERS_MART = "mart_aq_risk_drivers"
CARD_MART = "mart_model_card"
CALIBRATION_MART = "mart_model_calibration"

# Columns each optional mart must have when the manifest declares it.
OPTIONAL_MART_COLUMNS = {
    TREND_MART: {
        "station_id", "pollutant", "month", "observed_mean_ugm3", "weather_adjusted_mean_ugm3",
        "adjusted_lo_ugm3", "adjusted_hi_ugm3", "n_days",
    },
    RISK_MART: {"station_id", "target_date", "issued_at", "probability", "label_id", "split", "actual_label", "model_id"},
    DRIVERS_MART: {"station_id", "target_date", "model_id", "feature", "contribution", "rank"},
    CARD_MART: {
        "model_id", "algorithm", "trained_at", "train_start", "train_end", "validation_start", "validation_end",
        "test_start", "test_end", "label_id", "label_text", "features", "roc_auc_test", "pr_auc_test", "brier_test",
        "baseline_roc_auc_test_climatology", "baseline_roc_auc_test_persistence", "threshold", "precision_test",
        "recall_test", "uses_forecast_weather", "limitations",
    },
    CALIBRATION_MART: {"model_id", "bin_lo", "bin_hi", "predicted_mean", "observed_rate", "n"},
}

# Hard caps for parts of a response that the contract does not bound by a date range.
MAX_DRIVERS = 50
MAX_CALIBRATION_BINS = 1000


class StartupError(RuntimeError):
    """The warehouse cannot be served; the application must not start."""


def contract_version() -> str:
    """SCHEMA_VERSION from the copied contract, loaded by path so the working directory does not matter."""
    path = CONTRACTS_DIR / "version.py"
    spec = importlib.util.spec_from_file_location("_contract_version", path)
    if spec is None or spec.loader is None:
        raise StartupError(f"Cannot load the contract version from {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.SCHEMA_VERSION)


def finite(value):
    """A float for JSON, or None for NULL, NaN and infinities (JSON has no representation for them)."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def utc_text(micros: int | None) -> str | None:
    """ISO-8601 UTC text with a trailing Z for a timestamp given as microseconds since the epoch."""
    if micros is None:
        return None
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(micros))
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if moment.microsecond:
        text += f".{moment.microsecond:06d}".rstrip("0")
    return text + "Z"


def quote_identifier(name: str) -> str:
    """Quote a catalogue name for use as a column. Only names that passed IDENTIFIER get here."""
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class Snapshot:
    """Everything derived from one manifest.json. Replaced as a whole when the pipeline publishes a new run."""

    manifest: dict
    hourly_glob: str
    daily_glob: str
    locations: frozenset
    mtime: float
    # Globs of the optional marts that the manifest declares, keyed by mart name.
    optional_globs: dict = field(default_factory=dict)

    def mart_glob(self, mart: str) -> str:
        """Glob of an optional mart. Raises 503 when the manifest does not declare it."""
        found = self.optional_globs.get(mart)
        if found is None:
            raise unavailable(f"{mart} is not declared in the current manifest.")
        return found

    def require_mart(self, mart: str) -> None:
        """Raise 503 unless the manifest declares the optional mart."""
        self.mart_glob(mart)

    @property
    def stations(self) -> dict:
        return self.manifest.get("stations") or {}

    @property
    def variables(self) -> dict:
        return self.manifest["variables"]

    @property
    def warehouse_info(self) -> dict:
        m = self.manifest
        return {
            "run_id": m["run_id"],
            "schema_version": m["schema_version"],
            "generated_at_utc": m["generated_at_utc"],
        }


class Warehouse:
    def __init__(self, marts_dir: Path | str) -> None:
        self.marts_dir = Path(marts_dir).resolve()
        # DuckDB cannot open an in-memory database read-only, so connections attach to an empty
        # catalog file. Data is read straight from Parquet with bound parameters.
        self._catalog_dir = tempfile.TemporaryDirectory(prefix="plovdiv-catalog-")
        self._catalog = os.path.join(self._catalog_dir.name, "catalog.duckdb")
        duckdb.connect(self._catalog).close()
        try:
            self._snapshot = self._load()
        except BaseException:
            self._catalog_dir.cleanup()
            raise

    def close(self) -> None:
        self._catalog_dir.cleanup()

    def connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self._catalog, read_only=True)

    @property
    def manifest_path(self) -> Path:
        return self.marts_dir / "manifest.json"

    # ------------------------------------------------------------------ loading

    def current(self) -> Snapshot:
        """The loaded snapshot, reloaded first if the pipeline has published a newer manifest."""
        try:
            mtime = self.manifest_path.stat().st_mtime
        except OSError:
            return self._snapshot
        if mtime != self._snapshot.mtime:
            try:
                self._snapshot = self._load()
                log.info("Loaded warehouse run %s", self._snapshot.manifest["run_id"])
            except StartupError as exc:
                log.error("Ignoring the new manifest and keeping run %s: %s", self._snapshot.manifest["run_id"], exc)
                self._snapshot = replace(self._snapshot, mtime=mtime)
        return self._snapshot

    def _load(self) -> Snapshot:
        path = self.manifest_path
        try:
            mtime = path.stat().st_mtime
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StartupError(f"manifest not found: {path}") from None
        except (OSError, ValueError) as exc:
            raise StartupError(f"manifest could not be read ({path}): {exc}") from None

        expected = contract_version()
        found = manifest.get("schema_version") if isinstance(manifest, dict) else None
        if found != expected:
            raise StartupError(
                f"manifest schema_version {found!r} does not match the copied contract version {expected!r}."
            )

        schema = json.loads((CONTRACTS_DIR / "manifest.schema.json").read_text(encoding="utf-8"))
        try:
            jsonschema.Draft7Validator(schema).validate(manifest)
        except jsonschema.ValidationError as exc:
            raise StartupError(f"manifest does not match manifest.schema.json: {exc.message}") from None

        for name in manifest["variables"]:
            if not IDENTIFIER.fullmatch(name):
                raise StartupError(f"manifest variable name {name!r} is not a plain identifier.")

        globs = {}
        for mart in (HOURLY_MART, DAILY_MART):
            globs[mart] = self._resolve_glob(mart, manifest["marts"][mart]["glob"])

        columns = self._columns(globs[HOURLY_MART])
        missing = [v for v in manifest["variables"] if v not in columns]
        if missing:
            raise StartupError(f"{HOURLY_MART} has no column for manifest variable(s): {', '.join(missing)}.")

        optional = {}
        for mart in OPTIONAL_MART_COLUMNS:
            if mart in manifest["marts"]:
                optional[mart] = self._resolve_glob(mart, manifest["marts"][mart]["glob"])
                mart_columns = self._columns(optional[mart])
                absent = sorted(OPTIONAL_MART_COLUMNS[mart] - mart_columns)
                if absent:
                    raise StartupError(f"{mart} has no column for: {', '.join(absent)}.")

        locations = self._locations(globs[HOURLY_MART])
        return Snapshot(manifest, globs[HOURLY_MART], globs[DAILY_MART], locations, mtime, optional)

    def _resolve_glob(self, mart: str, pattern: str) -> str:
        full = os.path.normpath(os.path.join(self.marts_dir, pattern))
        if os.path.commonpath([str(self.marts_dir), full]) != str(self.marts_dir):
            raise StartupError(f"{mart}: glob {pattern!r} points outside the marts directory.")
        if not glob.glob(full):
            raise StartupError(f"{mart}: no Parquet files match {pattern!r} in {self.marts_dir}.")
        return full

    def _columns(self, hourly_glob: str) -> set:
        con = self.connect()
        try:
            rows = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [hourly_glob]).fetchall()
        finally:
            con.close()
        return {r[0] for r in rows}

    def _locations(self, hourly_glob: str) -> frozenset:
        con = self.connect()
        try:
            rows = con.execute("SELECT DISTINCT location_id FROM read_parquet(?)", [hourly_glob]).fetchall()
        finally:
            con.close()
        return frozenset(r[0] for r in rows)

    # ------------------------------------------------------------------ queries

    def fetch_series(
        self,
        snapshot: Snapshot,
        variable: str,
        location: str,
        source_type: str,
        start_epoch: int,
        end_epoch: int,
        resolution: str,
    ) -> tuple:
        """Parallel (timestamps, values). `variable` must already be checked against the catalogue."""
        column = quote_identifier(variable)
        if resolution == "hourly":
            sql = (
                f"SELECT epoch(valid_at_utc)::BIGINT AS ts, {column} AS v FROM read_parquet(?) "
                "WHERE location_id = ? AND source_type = ? "
                "AND valid_at_utc BETWEEN to_timestamp(?) AND to_timestamp(?) ORDER BY ts"
            )
        else:
            sql = (
                f"SELECT epoch(date_trunc('day', valid_at_utc))::BIGINT AS ts, avg({column}) AS v "
                "FROM read_parquet(?) WHERE location_id = ? AND source_type = ? "
                "AND valid_at_utc BETWEEN to_timestamp(?) AND to_timestamp(?) GROUP BY ts ORDER BY ts"
            )
        rows = self._run(sql, [snapshot.hourly_glob, location, source_type, start_epoch, end_epoch])
        return [r[0] for r in rows], [r[1] for r in rows]

    def fetch_daily_air_quality(
        self, snapshot: Snapshot, pollutant: str, location: str, first_day: date, last_day: date
    ) -> list:
        sql = (
            "SELECT strftime(day, '%Y-%m-%d'), daily_mean, valid_hours, is_valid_day FROM read_parquet(?) "
            "WHERE location_id = ? AND pollutant = ? AND day BETWEEN ? AND ? ORDER BY day"
        )
        return self._run(sql, [snapshot.daily_glob, location, pollutant, first_day, last_day])

    def fetch_trend(
        self, snapshot: Snapshot, station: str, pollutant: str, first_day: date, last_day: date
    ) -> list:
        """Monthly rows for a station. A month is included when any part of it lies in first_day..last_day."""
        first_month = first_day.replace(day=1)
        sql = (
            "SELECT strftime(month, '%Y-%m-%d'), observed_mean_ugm3, weather_adjusted_mean_ugm3, "
            "adjusted_lo_ugm3, adjusted_hi_ugm3, n_days FROM read_parquet(?) "
            "WHERE station_id = ? AND pollutant = ? AND month BETWEEN ? AND ? ORDER BY month"
        )
        return self._run(sql, [snapshot.mart_glob(TREND_MART), station, pollutant, first_month, last_day])

    def model_ids(self, snapshot: Snapshot) -> set:
        """Known model ids: the model card mart when declared, otherwise the risk mart."""
        mart = CARD_MART if CARD_MART in snapshot.optional_globs else RISK_MART
        rows = self._run("SELECT DISTINCT model_id FROM read_parquet(?)", [snapshot.mart_glob(mart)])
        return {r[0] for r in rows}

    _RISK_COLUMNS = (
        "strftime(target_date, '%Y-%m-%d'), epoch_us(issued_at), probability, label_id, split, actual_label, model_id"
    )

    def fetch_risk_range(
        self, snapshot: Snapshot, station: str, first_day: date, last_day: date, split: str | None, model_id: str | None
    ) -> list:
        sql = f"SELECT {self._RISK_COLUMNS} FROM read_parquet(?) WHERE station_id = ? AND target_date BETWEEN ? AND ? "
        params = [snapshot.mart_glob(RISK_MART), station, first_day, last_day]
        sql, params = self._risk_filters(sql, params, split, model_id)
        return self._run(sql + "ORDER BY target_date, model_id, issued_at", params)

    def fetch_risk_latest(self, snapshot: Snapshot, station: str, split: str | None, model_id: str | None) -> list:
        """The most recently issued row; ties go to the latest target date, then to the lowest model id."""
        sql = f"SELECT {self._RISK_COLUMNS} FROM read_parquet(?) WHERE station_id = ? "
        params = [snapshot.mart_glob(RISK_MART), station]
        sql, params = self._risk_filters(sql, params, split, model_id)
        return self._run(sql + "ORDER BY issued_at DESC, target_date DESC, model_id LIMIT 1", params)

    @staticmethod
    def _risk_filters(sql: str, params: list, split: str | None, model_id: str | None) -> tuple:
        # Rows whose probability is not a finite number would not fit the response schema and are left out.
        sql += "AND isfinite(probability) "
        if split is not None:
            sql += "AND split = ? "
            params.append(split)
        if model_id is not None:
            sql += "AND model_id = ? "
            params.append(model_id)
        return sql, params

    def fetch_risk_drivers(self, snapshot: Snapshot, station: str, target_date: str, model_id: str) -> list:
        sql = (
            "SELECT feature, contribution, rank FROM read_parquet(?) "
            "WHERE station_id = ? AND target_date = CAST(? AS DATE) AND model_id = ? AND isfinite(contribution) "
            "ORDER BY rank, feature LIMIT ?"
        )
        return self._run(sql, [snapshot.mart_glob(DRIVERS_MART), station, target_date, model_id, MAX_DRIVERS])

    def fetch_model_card(self, snapshot: Snapshot, model_id: str) -> tuple | None:
        sql = (
            "SELECT model_id, algorithm, epoch_us(trained_at), strftime(train_start, '%Y-%m-%d'), "
            "strftime(train_end, '%Y-%m-%d'), strftime(validation_start, '%Y-%m-%d'), "
            "strftime(validation_end, '%Y-%m-%d'), strftime(test_start, '%Y-%m-%d'), strftime(test_end, '%Y-%m-%d'), "
            "label_id, label_text, features, uses_forecast_weather, threshold, roc_auc_test, pr_auc_test, "
            "brier_test, precision_test, recall_test, baseline_roc_auc_test_climatology, "
            "baseline_roc_auc_test_persistence, limitations FROM read_parquet(?) WHERE model_id = ? LIMIT 1"
        )
        rows = self._run(sql, [snapshot.mart_glob(CARD_MART), model_id])
        return rows[0] if rows else None

    def fetch_calibration(self, snapshot: Snapshot, model_id: str) -> list:
        sql = (
            "SELECT bin_lo, bin_hi, predicted_mean, observed_rate, n FROM read_parquet(?) "
            "WHERE model_id = ? ORDER BY bin_lo LIMIT ?"
        )
        return self._run(sql, [snapshot.mart_glob(CALIBRATION_MART), model_id, MAX_CALIBRATION_BINS])

    def _run(self, sql: str, params: list) -> list:
        con = self.connect()
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
