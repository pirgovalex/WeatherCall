"""Contract tests for the optional air-quality trend, risk and model marts and their endpoints.

Covers:
- Manifest schema: the new marts and the station catalogue are optional, old manifests stay valid,
  and the coherence rules (stations, dependencies between marts) hold.
- Pandera schemas for the five marts, including adversarial rows that must be rejected.
- Fixture properties: row counts, referential integrity between marts, NULL cases, the station with
  fewer rows, and the 2026-10-25 clock change.
- OpenAPI: the four new endpoints, their parameters and error cases, and payloads built from the
  fixtures validated against the response schemas.
"""

from __future__ import annotations

import copy
import datetime
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd
import pytest
import yaml
from jsonschema import ValidationError, validate
from openapi_schema_validator import OAS30Validator
from openapi_spec_validator import validate as validate_openapi_spec
from pandera.errors import SchemaError, SchemaErrors

from contracts import schemas as S
from contracts.version import SCHEMA_VERSION

CONTRACTS_DIR = REPO_ROOT / "contracts"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"

OPTIONAL_MARTS = [
    "mart_aq_trend_monthly",
    "mart_aq_risk_daily",
    "mart_aq_risk_drivers",
    "mart_model_card",
    "mart_model_calibration",
]
NEW_PATHS = [
    "/api/v1/aq/trend",
    "/api/v1/aq/risk",
    "/api/v1/model/card",
    "/api/v1/model/calibration",
]
STATION_WITH_MORE_ROWS = "BG0051A"
STATION_WITH_FEWER_ROWS = "BG0078A"
CURRENT_MODEL = "pm10_exceed_gbm_v1"
PRIOR_MODEL = "pm10_exceed_logreg_v0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_mart(name: str, base: Path = FIXTURES_DIR) -> pd.DataFrame:
    files = list((base / name / "latest").glob("*.parquet"))
    assert len(files) == 1, f"expected one Parquet file for {name}, found {files}"
    return pd.read_parquet(files[0])


def load_manifest() -> dict:
    return json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))


def load_manifest_schema() -> dict:
    return json.loads((CONTRACTS_DIR / "manifest.schema.json").read_text(encoding="utf-8"))


def load_spec() -> dict:
    return yaml.safe_load((CONTRACTS_DIR / "openapi.yaml").read_text(encoding="utf-8"))


SPEC = load_spec()


def oas_errors(instance, schema_name: str) -> list[str]:
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": SPEC["components"]}
    errors = sorted(OAS30Validator(schema).iter_errors(instance), key=lambda e: list(e.path))
    return [f"{list(e.path)}: {e.message}" for e in errors]


def assert_conforms(instance, schema_name: str) -> None:
    assert not oas_errors(instance, schema_name), oas_errors(instance, schema_name)


def assert_rejected(df: pd.DataFrame, schema) -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        schema.validate(df)


def nullable(value):
    return None if pd.isna(value) else value


def iso_utc(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def warehouse_block(manifest: dict) -> dict:
    return {
        "run_id": manifest["run_id"],
        "schema_version": manifest["schema_version"],
        "generated_at_utc": manifest["generated_at_utc"],
    }


# ---------------------------------------------------------------------------
# Payload builders: the reference behaviour of the four endpoints, built straight from the fixtures
# ---------------------------------------------------------------------------


def trend_payload(station_id: str, pollutant: str, first: str, last: str) -> dict:
    manifest = load_manifest()
    df = read_mart("mart_aq_trend_monthly")
    month_start = pd.to_datetime(df["month"])
    month_end = month_start + pd.offsets.MonthEnd(0)
    mask = (
        (df["station_id"] == station_id)
        & (df["pollutant"] == pollutant)
        & (month_start <= pd.Timestamp(last))
        & (month_end >= pd.Timestamp(first))
    )
    rows = df[mask].sort_values("month")
    return {
        "station_id": station_id,
        "pollutant": pollutant,
        "unit": manifest["variables"][pollutant]["unit"],
        "months": [d.isoformat() for d in rows["month"]],
        "observed_mean": [nullable(v) for v in rows["observed_mean_ugm3"]],
        "weather_adjusted_mean": [nullable(v) for v in rows["weather_adjusted_mean_ugm3"]],
        "adjusted_lo": [nullable(v) for v in rows["adjusted_lo_ugm3"]],
        "adjusted_hi": [nullable(v) for v in rows["adjusted_hi_ugm3"]],
        "n_days": [int(v) for v in rows["n_days"]],
        "warehouse": warehouse_block(manifest),
        "attribution": manifest["attribution"],
    }


def risk_payload(station_id: str, *, first: str | None = None, last: str | None = None,
                 split: str | None = None, latest: bool = False) -> dict:
    manifest = load_manifest()
    df = read_mart("mart_aq_risk_daily")
    rows = df[df["station_id"] == station_id]
    if split:
        rows = rows[rows["split"] == split]
    if latest:
        rows = rows.sort_values(["issued_at", "target_date"]).tail(1)
    else:
        target = pd.to_datetime(rows["target_date"])
        rows = rows[(target >= pd.Timestamp(first)) & (target <= pd.Timestamp(last))]
        rows = rows.sort_values("target_date")
    payload = {
        "station_id": station_id,
        "target_dates": [d.isoformat() for d in rows["target_date"]],
        "issued_at": [iso_utc(t) for t in rows["issued_at"]],
        "probability": [float(v) for v in rows["probability"]],
        "label_id": list(rows["label_id"]),
        "split": list(rows["split"]),
        "actual_label": [None if pd.isna(v) else int(v) for v in rows["actual_label"]],
        "model_id": list(rows["model_id"]),
        "warehouse": warehouse_block(manifest),
        "attribution": manifest["attribution"],
    }
    if latest:
        drivers = read_mart("mart_aq_risk_drivers")
        keys = set(zip(rows["station_id"], rows["target_date"], rows["model_id"]))
        picked = drivers[[k in keys for k in zip(drivers["station_id"], drivers["target_date"], drivers["model_id"])]]
        picked = picked.sort_values("rank")
        payload["drivers"] = {
            "feature": list(picked["feature"]),
            "contribution": [float(v) for v in picked["contribution"]],
            "rank": [int(v) for v in picked["rank"]],
        }
    return payload


def card_payload(model_id: str) -> dict:
    manifest = load_manifest()
    row = read_mart("mart_model_card").set_index("model_id").loc[model_id]
    return {
        "model_id": model_id,
        "algorithm": row["algorithm"],
        "trained_at": iso_utc(row["trained_at"]),
        "windows": {k: row[k].isoformat() for k in
                    ["train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end"]},
        "label_id": row["label_id"],
        "label_text": row["label_text"],
        "features": [str(f) for f in row["features"]],
        "uses_forecast_weather": bool(row["uses_forecast_weather"]),
        "threshold": float(row["threshold"]),
        "metrics": {k: nullable(row[k]) for k in
                    ["roc_auc_test", "pr_auc_test", "brier_test", "precision_test", "recall_test"]},
        "baselines": {
            "roc_auc_test_climatology": nullable(row["baseline_roc_auc_test_climatology"]),
            "roc_auc_test_persistence": nullable(row["baseline_roc_auc_test_persistence"]),
        },
        "limitations": row["limitations"],
        "warehouse": warehouse_block(manifest),
        "attribution": manifest["attribution"],
    }


def calibration_payload(model_id: str) -> dict:
    manifest = load_manifest()
    df = read_mart("mart_model_calibration")
    rows = df[df["model_id"] == model_id].sort_values("bin_lo")
    return {
        "model_id": model_id,
        "bin_lo": [float(v) for v in rows["bin_lo"]],
        "bin_hi": [float(v) for v in rows["bin_hi"]],
        "predicted_mean": [nullable(v) for v in rows["predicted_mean"]],
        "observed_rate": [nullable(v) for v in rows["observed_rate"]],
        "n": [int(v) for v in rows["n"]],
        "warehouse": warehouse_block(manifest),
        "attribution": manifest["attribution"],
    }


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


class TestManifestSchema:
    def test_version_stays_1_and_old_manifest_is_valid(self):
        """The change is additive: an old two-mart manifest still validates under the same version."""
        assert SCHEMA_VERSION == "1"
        assert load_manifest_schema()["properties"]["schema_version"]["const"] == "1"
        old = copy.deepcopy(load_manifest())
        old.pop("stations")
        for name in OPTIONAL_MARTS:
            old["marts"].pop(name)
        validate(instance=old, schema=load_manifest_schema())

    def test_fixture_manifest_declares_every_optional_mart_and_stations(self):
        manifest = load_manifest()
        validate(instance=manifest, schema=load_manifest_schema())
        for name in OPTIONAL_MARTS:
            assert name in manifest["marts"]
        assert set(manifest["stations"]) == {STATION_WITH_MORE_ROWS, STATION_WITH_FEWER_ROWS}

    def test_original_marts_remain_required(self):
        required = load_manifest_schema()["properties"]["marts"]["required"]
        assert required == ["mart_weather_aq_hourly", "mart_aq_daily"]

    def test_stations_required_when_a_station_keyed_mart_is_declared(self):
        for mart in ("mart_aq_trend_monthly", "mart_aq_risk_daily", "mart_aq_risk_drivers"):
            manifest = copy.deepcopy(load_manifest())
            # Keep dependencies satisfied so that only the missing catalogue is wrong.
            manifest.pop("stations")
            for name in OPTIONAL_MARTS:
                if name not in (mart, "mart_aq_risk_daily", "mart_model_card"):
                    manifest["marts"].pop(name)
            with pytest.raises(ValidationError):
                validate(instance=manifest, schema=load_manifest_schema())

    def test_model_marts_alone_do_not_need_stations(self):
        manifest = copy.deepcopy(load_manifest())
        manifest.pop("stations")
        for name in ("mart_aq_trend_monthly", "mart_aq_risk_daily", "mart_aq_risk_drivers"):
            manifest["marts"].pop(name)
        validate(instance=manifest, schema=load_manifest_schema())

    @pytest.mark.parametrize(
        "removed",
        [
            ["mart_model_card"],  # risk rows reference model_id in the card
            ["mart_aq_risk_daily"],  # drivers reference risk rows
        ],
    )
    def test_dependencies_between_marts(self, removed):
        manifest = copy.deepcopy(load_manifest())
        for name in removed:
            manifest["marts"].pop(name)
        with pytest.raises(ValidationError):
            validate(instance=manifest, schema=load_manifest_schema())

    def test_calibration_requires_model_card(self):
        manifest = copy.deepcopy(load_manifest())
        for name in ("mart_model_card", "mart_aq_risk_daily", "mart_aq_risk_drivers"):
            manifest["marts"].pop(name)
        with pytest.raises(ValidationError):
            validate(instance=manifest, schema=load_manifest_schema())

    def test_unknown_mart_and_malformed_entries_are_rejected(self):
        base = load_manifest()

        unknown = copy.deepcopy(base)
        unknown["marts"]["mart_forecast_verification"] = {"glob": "x/*.parquet", "rows": 1}
        with pytest.raises(ValidationError):
            validate(instance=unknown, schema=load_manifest_schema())

        bad_month = copy.deepcopy(base)
        bad_month["marts"]["mart_aq_trend_monthly"]["min_month"] = "2025-05-15"
        with pytest.raises(ValidationError):
            validate(instance=bad_month, schema=load_manifest_schema())

        negative_rows = copy.deepcopy(base)
        negative_rows["marts"]["mart_model_card"]["rows"] = -1
        with pytest.raises(ValidationError):
            validate(instance=negative_rows, schema=load_manifest_schema())

        missing_range = copy.deepcopy(base)
        del missing_range["marts"]["mart_aq_risk_daily"]["max_target_date"]
        with pytest.raises(ValidationError):
            validate(instance=missing_range, schema=load_manifest_schema())

        bad_station = copy.deepcopy(base)
        del bad_station["stations"]["BG0051A"]["location_id"]
        with pytest.raises(ValidationError):
            validate(instance=bad_station, schema=load_manifest_schema())


# ---------------------------------------------------------------------------
# Pandera schemas
# ---------------------------------------------------------------------------


class TestPanderaSchemas:
    @pytest.mark.parametrize("name", OPTIONAL_MARTS)
    def test_fixture_validates(self, name):
        df = read_mart(name)
        assert len(df) > 0
        assert S.OPTIONAL_MART_SCHEMAS[name].validate(df) is not None

    def test_every_optional_mart_has_a_schema(self):
        assert set(S.OPTIONAL_MART_SCHEMAS) == set(OPTIONAL_MARTS)

    # trend -----------------------------------------------------------------

    def trend_row(self, **changes) -> dict:
        row = {
            "station_id": "BG0051A",
            "month": datetime.date(2026, 9, 1),
            "observed_mean_ugm3": 24.0,
            "weather_adjusted_mean_ugm3": 25.0,
            "adjusted_lo_ugm3": 23.0,
            "adjusted_hi_ugm3": 27.0,
            "n_days": 28,
            "pollutant": "pm10",
        }
        row.update(changes)
        return row

    def test_trend_baseline_row_is_valid(self):
        assert S.mart_aq_trend_monthly_schema.validate(pd.DataFrame([self.trend_row()])) is not None

    @pytest.mark.parametrize(
        "changes",
        [
            {"month": datetime.date(2026, 9, 2)},  # not the first day
            {"n_days": 31},  # September has 30 days
            {"n_days": 32},
            {"n_days": -1},
            {"observed_mean_ugm3": None},  # n_days > 0 needs an observed mean
            {"observed_mean_ugm3": -1.0},
            {"adjusted_lo_ugm3": 26.0},  # lo above the adjusted mean
            {"adjusted_hi_ugm3": 24.0},  # hi below the adjusted mean
            {"adjusted_lo_ugm3": None},  # interval bounds must be NULL together with the mean
            {"weather_adjusted_mean_ugm3": None},
            {"n_days": 0},  # no valid days, but an observed mean
            {"pollutant": "so2"},
            {"station_id": ""},
        ],
    )
    def test_trend_rejects(self, changes):
        assert_rejected(pd.DataFrame([self.trend_row(**changes)]), S.mart_aq_trend_monthly_schema)

    def test_trend_accepts_an_empty_month_with_all_nulls(self):
        row = self.trend_row(
            n_days=0, observed_mean_ugm3=None, weather_adjusted_mean_ugm3=None,
            adjusted_lo_ugm3=None, adjusted_hi_ugm3=None,
        )
        assert S.mart_aq_trend_monthly_schema.validate(pd.DataFrame([row])) is not None

    def test_trend_rejects_duplicate_key(self):
        assert_rejected(pd.DataFrame([self.trend_row(), self.trend_row()]), S.mart_aq_trend_monthly_schema)

    # risk ------------------------------------------------------------------

    def risk_row(self, **changes) -> dict:
        row = {
            "station_id": "BG0051A",
            "target_date": datetime.date(2026, 10, 14),
            "issued_at": pd.Timestamp("2026-10-13 06:00:00", tz="UTC"),
            "probability": 0.3,
            "label_id": "pm10_daily_mean_gt_50",
            "split": "live",
            "actual_label": 0,
            "model_id": "m1",
        }
        row.update(changes)
        return row

    def test_risk_baseline_row_is_valid(self):
        assert S.mart_aq_risk_daily_schema.validate(pd.DataFrame([self.risk_row()])) is not None

    def test_risk_accepts_null_actual_label(self):
        df = pd.DataFrame([self.risk_row(actual_label=None)])
        assert S.mart_aq_risk_daily_schema.validate(df) is not None

    @pytest.mark.parametrize(
        "changes",
        [
            {"probability": 1.2},
            {"probability": -0.01},
            {"probability": None},
            {"split": "holdout"},
            {"actual_label": 2},
            {"label_id": ""},
            {"model_id": ""},
            # issued after the EEA day 2026-10-14 ended (it ends 2026-10-14 23:00 UTC)
            {"issued_at": pd.Timestamp("2026-10-14 23:30:00", tz="UTC")},
            {"issued_at": pd.Timestamp("2026-10-13 06:00:00")},  # naive timestamp
            {"issued_at": pd.Timestamp("2026-10-13 09:00:00", tz="Europe/Sofia")},  # not UTC
        ],
    )
    def test_risk_rejects(self, changes):
        assert_rejected(pd.DataFrame([self.risk_row(**changes)]), S.mart_aq_risk_daily_schema)

    def test_risk_rejects_duplicate_key(self):
        df = pd.DataFrame([self.risk_row(), self.risk_row(probability=0.4)])
        assert_rejected(df, S.mart_aq_risk_daily_schema)

    def test_risk_has_no_label_text_column(self):
        """label_text lives in the model card only."""
        assert "label_text" not in S.mart_aq_risk_daily_schema.columns
        assert "label_text" not in read_mart("mart_aq_risk_daily").columns

    # drivers ---------------------------------------------------------------

    def drivers_rows(self) -> list[dict]:
        base = {"station_id": "BG0051A", "target_date": datetime.date(2026, 10, 14), "model_id": "m1"}
        return [
            {**base, "feature": "a", "contribution": -0.9, "rank": 1},
            {**base, "feature": "b", "contribution": 0.5, "rank": 2},
            {**base, "feature": "c", "contribution": 0.1, "rank": 3},
        ]

    def test_drivers_baseline_is_valid(self):
        assert S.mart_aq_risk_drivers_schema.validate(pd.DataFrame(self.drivers_rows())) is not None

    def test_drivers_reject_broken_rows(self):
        rows = self.drivers_rows()

        gap = copy.deepcopy(rows)
        gap[2]["rank"] = 4
        assert_rejected(pd.DataFrame(gap), S.mart_aq_risk_drivers_schema)

        unordered = copy.deepcopy(rows)
        unordered[2]["contribution"] = 2.0  # rank 3 with the largest magnitude
        assert_rejected(pd.DataFrame(unordered), S.mart_aq_risk_drivers_schema)

        infinite = copy.deepcopy(rows)
        infinite[0]["contribution"] = float("inf")
        assert_rejected(pd.DataFrame(infinite), S.mart_aq_risk_drivers_schema)

        missing = copy.deepcopy(rows)
        missing[1]["contribution"] = None
        assert_rejected(pd.DataFrame(missing), S.mart_aq_risk_drivers_schema)

        duplicate_feature = copy.deepcopy(rows)
        duplicate_feature[1]["feature"] = "a"
        assert_rejected(pd.DataFrame(duplicate_feature), S.mart_aq_risk_drivers_schema)

        zero_rank = copy.deepcopy(rows)
        for r in zero_rank:
            r["rank"] -= 1
        assert_rejected(pd.DataFrame(zero_rank), S.mart_aq_risk_drivers_schema)

    # model card --------------------------------------------------------------

    def card_row(self, **changes) -> dict:
        row = read_mart("mart_model_card").iloc[0].to_dict()
        row.update(changes)
        return row

    def test_card_baseline_is_valid(self):
        assert S.mart_model_card_schema.validate(pd.DataFrame([self.card_row()])) is not None

    @pytest.mark.parametrize(
        "changes",
        [
            {"roc_auc_test": 1.2},
            {"brier_test": -0.1},
            {"threshold": 1.5},
            {"features": []},
            {"features": ["a", "a"]},
            {"features": ["a", ""]},
            {"limitations": ""},
            {"label_text": ""},
            # validation window overlaps the training window
            {"validation_start": datetime.date(2025, 6, 1)},
            # test window starts before the validation window ends
            {"test_start": datetime.date(2025, 12, 31)},
            # trained before the training window ended
            {"trained_at": pd.Timestamp("2025-06-01 00:00:00", tz="UTC")},
            {"trained_at": pd.Timestamp("2025-12-31 21:15:00")},  # naive timestamp
        ],
    )
    def test_card_rejects(self, changes):
        assert_rejected(pd.DataFrame([self.card_row(**changes)]), S.mart_model_card_schema)

    def test_card_accepts_training_date_after_the_test_window(self):
        row = self.card_row(trained_at=pd.Timestamp("2026-10-07 12:00:00", tz="UTC"))
        assert S.mart_model_card_schema.validate(pd.DataFrame([row])) is not None

    def test_card_accepts_null_metrics(self):
        row = self.card_row(
            roc_auc_test=None, pr_auc_test=None, brier_test=None, precision_test=None,
            recall_test=None, baseline_roc_auc_test_climatology=None, baseline_roc_auc_test_persistence=None,
        )
        assert S.mart_model_card_schema.validate(pd.DataFrame([row])) is not None

    def test_card_rejects_duplicate_model_id(self):
        assert_rejected(pd.DataFrame([self.card_row(), self.card_row()]), S.mart_model_card_schema)

    # calibration -------------------------------------------------------------

    def calibration_rows(self) -> list[dict]:
        return [
            {"model_id": "m1", "bin_lo": 0.0, "bin_hi": 0.5, "predicted_mean": 0.2, "observed_rate": 0.25, "n": 40},
            {"model_id": "m1", "bin_lo": 0.5, "bin_hi": 1.0, "predicted_mean": 0.7, "observed_rate": 0.6, "n": 10},
        ]

    def test_calibration_baseline_is_valid(self):
        assert S.mart_model_calibration_schema.validate(pd.DataFrame(self.calibration_rows())) is not None

    def test_calibration_rejects_broken_rows(self):
        rows = self.calibration_rows()

        gap = copy.deepcopy(rows)
        gap[1]["bin_lo"] = 0.6
        assert_rejected(pd.DataFrame(gap), S.mart_model_calibration_schema)

        short = copy.deepcopy(rows)[:1]
        assert_rejected(pd.DataFrame(short), S.mart_model_calibration_schema)

        empty_with_values = copy.deepcopy(rows)
        empty_with_values[1]["n"] = 0
        assert_rejected(pd.DataFrame(empty_with_values), S.mart_model_calibration_schema)

        filled_with_nulls = copy.deepcopy(rows)
        filled_with_nulls[1]["observed_rate"] = None
        assert_rejected(pd.DataFrame(filled_with_nulls), S.mart_model_calibration_schema)

        outside = copy.deepcopy(rows)
        outside[0]["predicted_mean"] = 0.9
        assert_rejected(pd.DataFrame(outside), S.mart_model_calibration_schema)

        negative_n = copy.deepcopy(rows)
        negative_n[0]["n"] = -3
        assert_rejected(pd.DataFrame(negative_n), S.mart_model_calibration_schema)

        rate_above_one = copy.deepcopy(rows)
        rate_above_one[0]["observed_rate"] = 1.5
        assert_rejected(pd.DataFrame(rate_above_one), S.mart_model_calibration_schema)

    def test_calibration_accepts_an_empty_bin(self):
        rows = self.calibration_rows()
        rows[1].update({"n": 0, "predicted_mean": None, "observed_rate": None})
        assert S.mart_model_calibration_schema.validate(pd.DataFrame(rows)) is not None


# ---------------------------------------------------------------------------
# Fixture properties and cross-mart integrity
# ---------------------------------------------------------------------------


class TestFixtureProperties:
    def test_fixture_readme_says_synthetic(self):
        text = (FIXTURES_DIR / "README.md").read_text(encoding="utf-8")
        assert "synthetic" in text.lower()
        assert b"\r" not in (FIXTURES_DIR / "README.md").read_bytes()

    def test_manifest_matches_the_parquet_files(self):
        manifest = load_manifest()
        for name in OPTIONAL_MARTS:
            df = read_mart(name)
            assert manifest["marts"][name]["rows"] == len(df), name
        trend = read_mart("mart_aq_trend_monthly")
        assert manifest["marts"]["mart_aq_trend_monthly"]["min_month"] == trend["month"].min().isoformat()
        assert manifest["marts"]["mart_aq_trend_monthly"]["max_month"] == trend["month"].max().isoformat()
        risk = read_mart("mart_aq_risk_daily")
        assert manifest["marts"]["mart_aq_risk_daily"]["min_target_date"] == risk["target_date"].min().isoformat()
        assert manifest["marts"]["mart_aq_risk_daily"]["max_target_date"] == risk["target_date"].max().isoformat()

    def test_station_ids_are_declared(self):
        declared = set(load_manifest()["stations"])
        for name in ("mart_aq_trend_monthly", "mart_aq_risk_daily", "mart_aq_risk_drivers"):
            assert set(read_mart(name)["station_id"]) <= declared, name
        for entry in load_manifest()["stations"].values():
            assert entry["location_id"] == "plovdiv"

    def test_one_station_has_fewer_rows(self):
        trend = read_mart("mart_aq_trend_monthly")
        risk = read_mart("mart_aq_risk_daily")
        for df in (trend, risk):
            counts = df["station_id"].value_counts()
            assert counts[STATION_WITH_FEWER_ROWS] < counts[STATION_WITH_MORE_ROWS]
        assert set(risk[risk["station_id"] == STATION_WITH_FEWER_ROWS]["split"]) == {"test", "live"}
        assert set(risk[risk["station_id"] == STATION_WITH_MORE_ROWS]["split"]) == set(S.VALID_SPLITS)

    def test_null_cases_are_present(self):
        trend = read_mart("mart_aq_trend_monthly")
        empty_month = trend[trend["n_days"] == 0]
        assert len(empty_month) == 1
        assert empty_month[["observed_mean_ugm3", "weather_adjusted_mean_ugm3",
                            "adjusted_lo_ugm3", "adjusted_hi_ugm3"]].isna().all().all()
        low_coverage = trend[(trend["n_days"] > 0) & (trend["n_days"] < 15)]
        assert len(low_coverage) >= 2  # a low-coverage month and the partial current month
        width = (trend["adjusted_hi_ugm3"] - trend["adjusted_lo_ugm3"])
        assert width[trend["n_days"] == 9].iloc[0] > width[trend["n_days"] >= 24].median()

        risk = read_mart("mart_aq_risk_daily")
        assert risk["actual_label"].isna().any() and risk["actual_label"].notna().any()
        assert set(risk["actual_label"].dropna()) == {0, 1}

        card = read_mart("mart_model_card")
        assert card["baseline_roc_auc_test_persistence"].isna().any()

        calibration = read_mart("mart_model_calibration")
        empty_bins = calibration[calibration["n"] == 0]
        assert len(empty_bins) == 1 and empty_bins[["predicted_mean", "observed_rate"]].isna().all().all()

    def test_partial_current_month_is_marked_by_low_n_days(self):
        trend = read_mart("mart_aq_trend_monthly")
        last = trend[trend["month"] == trend["month"].max()]
        assert (last["n_days"] <= 14).all()

    def test_risk_rows_reference_the_model_card(self):
        card = read_mart("mart_model_card").set_index("model_id")
        risk = read_mart("mart_aq_risk_daily")
        assert set(risk["model_id"]) <= set(card.index)
        for model_id, group in risk.groupby("model_id"):
            assert set(group["label_id"]) == {card.loc[model_id, "label_id"]}

    def test_calibration_models_exist_in_the_card(self):
        card_models = set(read_mart("mart_model_card")["model_id"])
        assert set(read_mart("mart_model_calibration")["model_id"]) == card_models

    def test_model_card_has_one_row_per_model(self):
        card = read_mart("mart_model_card")
        assert card["model_id"].is_unique
        assert set(card["model_id"]) == {CURRENT_MODEL, PRIOR_MODEL}
        assert not (set(read_mart("mart_aq_risk_daily")["model_id"]) & {PRIOR_MODEL})

    def test_forecast_weather_flag_matches_the_features(self):
        card = read_mart("mart_model_card").set_index("model_id")
        for model_id, row in card.iterrows():
            uses_forecast = any("forecast" in f for f in row["features"])
            assert bool(row["uses_forecast_weather"]) == uses_forecast, model_id

    def test_risk_splits_fall_inside_the_model_windows(self):
        card = read_mart("mart_model_card").set_index("model_id")
        risk = read_mart("mart_aq_risk_daily")
        for _, row in risk.iterrows():
            c = card.loc[row["model_id"]]
            day = row["target_date"]
            window = {
                "train": (c["train_start"], c["train_end"]),
                "validation": (c["validation_start"], c["validation_end"]),
                "test": (c["test_start"], c["test_end"]),
            }.get(row["split"])
            if window is None:
                assert day > c["test_end"], f"live row {day} is not after the test window"
            else:
                assert window[0] <= day <= window[1], f"{row['split']} row {day} outside {window}"

    def test_probabilities_are_issued_day_ahead_in_utc(self):
        risk = read_mart("mart_aq_risk_daily")
        assert str(risk["issued_at"].dt.tz) == "UTC"
        target_midnight = pd.to_datetime(risk["target_date"]).dt.tz_localize("UTC")
        lead = target_midnight - risk["issued_at"]
        assert (lead == pd.Timedelta(hours=18)).all()  # issued at 06:00 UTC on the previous day

    def test_actual_label_is_unknown_for_undecidable_and_unfinished_days(self):
        """A day without a valid PM10 mean has no outcome, and neither do days that have not finished."""
        daily = read_mart("mart_aq_daily")
        invalid_pm10 = set(daily[(daily["pollutant"] == "pm10") & (~daily["is_valid_day"])]["day"])
        assert invalid_pm10 == {datetime.date(2026, 10, 23)}
        risk = read_mart("mart_aq_risk_daily")
        undecided = set(risk[risk["actual_label"].isna()]["target_date"])
        assert undecided == invalid_pm10 | {datetime.date(2026, 10, 27), datetime.date(2026, 10, 28)}

    def test_drivers_reference_risk_rows_and_card_features(self):
        card = read_mart("mart_model_card").set_index("model_id")
        risk = read_mart("mart_aq_risk_daily")
        drivers = read_mart("mart_aq_risk_drivers")

        risk_keys = set(zip(risk["station_id"], risk["target_date"], risk["model_id"]))
        driver_keys = set(zip(drivers["station_id"], drivers["target_date"], drivers["model_id"]))
        assert driver_keys <= risk_keys

        published = risk[risk["split"].isin(["test", "live"])]
        assert driver_keys == set(zip(published["station_id"], published["target_date"], published["model_id"]))

        for model_id, group in drivers.groupby("model_id"):
            assert set(group["feature"]) <= set(card.loc[model_id, "features"])
        counts = drivers.groupby(["station_id", "target_date", "model_id"]).size()
        assert (counts == 5).all()

    def test_drivers_use_both_signs(self):
        drivers = read_mart("mart_aq_risk_drivers")
        assert (drivers["contribution"] > 0).any() and (drivers["contribution"] < 0).any()

    def test_dst_day_is_one_ordinary_key_per_station(self):
        """Keys are EEA dates, so 2026-10-25 is a single row per station; only display times shift."""
        risk = read_mart("mart_aq_risk_daily")
        dst_day = risk[risk["target_date"] == datetime.date(2026, 10, 25)]
        assert sorted(dst_day["station_id"]) == [STATION_WITH_MORE_ROWS, STATION_WITH_FEWER_ROWS]
        assert not risk.duplicated(["station_id", "target_date", "model_id"]).any()

        sofia = ZoneInfo("Europe/Sofia")
        before = risk[(risk["station_id"] == STATION_WITH_MORE_ROWS)
                      & (risk["target_date"] == datetime.date(2026, 10, 25))]["issued_at"].iloc[0]
        after = risk[(risk["station_id"] == STATION_WITH_MORE_ROWS)
                     & (risk["target_date"] == datetime.date(2026, 10, 26))]["issued_at"].iloc[0]
        assert before.tz_convert(sofia).isoformat() == "2026-10-24T09:00:00+03:00"
        assert after.tz_convert(sofia).isoformat() == "2026-10-25T08:00:00+02:00"
        # 24 hours apart in UTC, but 23 local clock hours apart: the offset changed in between
        assert after - before == pd.Timedelta(hours=24)

    def test_trend_is_keyed_by_month_start_in_utc(self):
        trend = read_mart("mart_aq_trend_monthly")
        assert all(d.day == 1 for d in trend["month"])
        assert not trend.duplicated(["station_id", "pollutant", "month"]).any()

    def test_calibration_bins_cover_zero_to_one(self):
        calibration = read_mart("mart_model_calibration")
        for _, group in calibration.groupby("model_id"):
            group = group.sort_values("bin_lo")
            assert len(group) == 10
            assert group["bin_lo"].iloc[0] == 0.0 and group["bin_hi"].iloc[-1] == 1.0
            assert np.allclose(group["bin_lo"].iloc[1:].to_numpy(), group["bin_hi"].iloc[:-1].to_numpy())


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def operation(path: str) -> dict:
    return SPEC["paths"][path]["get"]


def params(path: str) -> dict:
    return {p["name"]: p for p in operation(path).get("parameters", [])}


class TestOpenApiNewEndpoints:
    def test_spec_is_valid_openapi_3_0(self):
        validate_openapi_spec(SPEC)
        assert str(SPEC["openapi"]).startswith("3.0")
        assert str(SPEC["info"]["version"]) == SCHEMA_VERSION

    def test_new_paths_exist_and_old_ones_remain(self):
        for path in NEW_PATHS + ["/healthz", "/api/v1/meta", "/api/v1/series", "/api/v1/aq/daily"]:
            assert path in SPEC["paths"], path
        ids = [SPEC["paths"][p]["get"]["operationId"] for p in SPEC["paths"]]
        assert len(ids) == len(set(ids))
        for path in NEW_PATHS:
            assert set(SPEC["paths"][path]) == {"get"}, f"{path} must be read-only"

    @pytest.mark.parametrize("path", NEW_PATHS)
    def test_error_cases_use_the_envelope(self, path):
        responses = operation(path)["responses"]
        assert set(responses) == {"200", "404", "422", "503"}
        for code in ("404", "422", "503"):
            ref = responses[code]["content"]["application/json"]["schema"]["$ref"]
            assert ref == "#/components/schemas/ErrorEnvelope"

    def test_validation_errors_stay_422_like_the_existing_endpoints(self):
        for path in ["/api/v1/series", "/api/v1/aq/daily"] + NEW_PATHS:
            assert "422" in operation(path)["responses"], path
            assert "400" not in operation(path)["responses"], path

    def test_trend_parameters(self):
        p = params("/api/v1/aq/trend")
        assert set(p) == {"station_id", "from", "to", "pollutant"}
        assert [n for n, v in p.items() if v["required"]] == ["station_id", "from", "to"]
        assert p["pollutant"]["schema"]["enum"] == S.VALID_POLLUTANTS
        assert p["pollutant"]["schema"]["default"] == "pm10"
        assert all(v["in"] == "query" for v in p.values())

    def test_risk_parameters(self):
        p = params("/api/v1/aq/risk")
        assert set(p) == {"station_id", "from", "to", "split", "model_id", "latest"}
        assert [n for n, v in p.items() if v["required"]] == ["station_id"]
        assert p["split"]["schema"]["enum"] == S.VALID_SPLITS
        assert p["latest"]["schema"]["type"] == "boolean"
        assert p["latest"]["schema"]["default"] is False
        assert "latest=true" in p["from"]["description"] and "latest=true" in p["to"]["description"]

    @pytest.mark.parametrize("path", ["/api/v1/model/card", "/api/v1/model/calibration"])
    def test_model_endpoints_take_a_required_model_id(self, path):
        p = params(path)
        assert set(p) == {"model_id"} and p["model_id"]["required"] is True

    def test_responses_point_at_the_expected_schemas(self):
        expected = {
            "/api/v1/aq/trend": "AqTrendResponse",
            "/api/v1/aq/risk": "AqRiskResponse",
            "/api/v1/model/card": "ModelCardResponse",
            "/api/v1/model/calibration": "ModelCalibrationResponse",
        }
        for path, name in expected.items():
            ref = operation(path)["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            assert ref == f"#/components/schemas/{name}"

    @pytest.mark.parametrize(
        "name", ["AqTrendResponse", "AqRiskResponse", "ModelCardResponse", "ModelCalibrationResponse"]
    )
    def test_responses_carry_warehouse_and_attribution_like_existing_ones(self, name):
        schema = SPEC["components"]["schemas"][name]
        assert {"warehouse", "attribution"} <= set(schema["required"])
        assert schema["properties"]["warehouse"]["$ref"] == "#/components/schemas/WarehouseMeta"

    def test_risk_response_has_no_label_text(self):
        assert "label_text" not in SPEC["components"]["schemas"]["AqRiskResponse"]["properties"]
        assert "label_text" in SPEC["components"]["schemas"]["ModelCardResponse"]["properties"]

    def test_meta_keeps_original_marts_required_and_lists_optional_ones(self):
        marts = SPEC["components"]["schemas"]["MartsCatalogue"]
        assert marts["required"] == ["mart_weather_aq_hourly", "mart_aq_daily"]
        assert set(OPTIONAL_MARTS) <= set(marts["properties"])
        meta = SPEC["components"]["schemas"]["MetaResponse"]
        assert "stations" in meta["properties"] and "stations" not in meta["required"]

    def test_existing_endpoints_state_that_to_is_inclusive(self):
        for path in ("/api/v1/series", "/api/v1/aq/daily"):
            assert "inclusive" in params(path)["to"]["description"]

    # payloads built from the fixtures ----------------------------------------------

    def test_meta_built_from_the_manifest_conforms(self):
        assert_conforms(load_manifest(), "MetaResponse")

    def test_old_meta_without_optional_parts_conforms(self):
        old = copy.deepcopy(load_manifest())
        old.pop("stations")
        for name in OPTIONAL_MARTS:
            old["marts"].pop(name)
        assert_conforms(old, "MetaResponse")

    def test_range_fields_are_optional_in_meta_and_in_the_manifest_schema(self):
        hourly = SPEC["components"]["schemas"]["MartRangeHourly"]
        daily = SPEC["components"]["schemas"]["MartRangeDaily"]
        assert "max_analysis_valid_at_utc" in hourly["properties"] and "max_analysis_valid_at_utc" not in hourly["required"]
        assert {"min_day", "max_day"} <= set(daily["properties"]) and not ({"min_day", "max_day"} & set(daily["required"]))
        marts = load_manifest_schema()["properties"]["marts"]["properties"]
        assert "max_analysis_valid_at_utc" in marts["mart_weather_aq_hourly"]["properties"]
        assert "max_analysis_valid_at_utc" not in marts["mart_weather_aq_hourly"]["required"]
        assert {"min_day", "max_day"} <= set(marts["mart_aq_daily"]["properties"])
        assert not ({"min_day", "max_day"} & set(marts["mart_aq_daily"]["required"]))

    def test_fixture_manifest_range_fields_equal_the_fixture_data(self):
        manifest = load_manifest()
        hourly = read_mart("mart_weather_aq_hourly")
        daily = read_mart("mart_aq_daily")
        analysis = hourly.loc[hourly["source_type"] == "analysis", "valid_at_utc"].max()
        entry = manifest["marts"]["mart_weather_aq_hourly"]
        assert entry["max_analysis_valid_at_utc"] == analysis.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert entry["max_analysis_valid_at_utc"] <= entry["max_valid_at_utc"]
        assert manifest["marts"]["mart_aq_daily"]["min_day"] == daily["day"].min().isoformat()
        assert manifest["marts"]["mart_aq_daily"]["max_day"] == daily["day"].max().isoformat()

    def test_meta_with_a_mismatching_range_field_does_not_equal_the_data(self):
        # The manifest schema only fixes the format; equality with the Parquet is a data check (core.validate_output).
        # This test fixes the invalid shapes the schema itself must refuse.
        schema = load_manifest_schema()
        for entry, key, bad in (
            ("mart_weather_aq_hourly", "max_analysis_valid_at_utc", "2026-10-07 14:00"),
            ("mart_aq_daily", "min_day", "2013-1-1"),
            ("mart_aq_daily", "max_day", "20251231"),
        ):
            broken = copy.deepcopy(load_manifest())
            broken["marts"][entry][key] = bad
            with pytest.raises(ValidationError):
                validate(instance=broken, schema=schema)
        extra = copy.deepcopy(load_manifest())
        extra["marts"]["mart_aq_daily"]["max_analysis_valid_at_utc"] = "2026-10-07T14:00:00Z"
        with pytest.raises(ValidationError):
            validate(instance=extra, schema=schema)

    def test_manifest_without_range_fields_stays_valid_and_conforms(self):
        schema = load_manifest_schema()
        old = copy.deepcopy(load_manifest())
        old["marts"]["mart_weather_aq_hourly"].pop("max_analysis_valid_at_utc")
        old["marts"]["mart_aq_daily"].pop("min_day")
        old["marts"]["mart_aq_daily"].pop("max_day")
        validate(instance=old, schema=schema)
        assert_conforms(old, "MetaResponse")

    def test_trend_payloads_conform(self):
        full = trend_payload("BG0051A", "pm10", "2025-05-01", "2026-10-31")
        assert_conforms(full, "AqTrendResponse")
        assert len(full["months"]) == 18 and full["unit"] == "ug/m3"

        fewer = trend_payload("BG0078A", "pm10", "2025-01-01", "2026-12-31")
        assert_conforms(fewer, "AqTrendResponse")
        assert len(fewer["months"]) == 14
        outage = fewer["months"].index("2026-03-01")
        assert fewer["observed_mean"][outage] is None and fewer["n_days"][outage] == 0

        # A range that touches a month only partly still returns that month
        partial = trend_payload("BG0051A", "pm10", "2026-09-15", "2026-10-02")
        assert partial["months"] == ["2026-09-01", "2026-10-01"]

        # A known station with no rows for the pollutant gives empty arrays
        empty = trend_payload("BG0078A", "pm2_5", "2025-01-01", "2026-12-31")
        assert empty["months"] == [] and empty["n_days"] == []
        assert_conforms(empty, "AqTrendResponse")

    def test_risk_range_payloads_conform(self):
        live = risk_payload("BG0078A", first="2026-10-14", last="2026-10-28", split="live")
        assert_conforms(live, "AqRiskResponse")
        assert "drivers" not in live
        assert live["target_dates"][0] == "2026-10-14" and live["target_dates"][-1] == "2026-10-28"
        assert live["actual_label"][-1] is None
        assert live["issued_at"][0] == "2026-10-13T06:00:00Z"
        assert live["target_dates"] == sorted(live["target_dates"])

        everything = risk_payload("BG0051A", first="2025-06-24", last="2026-10-28")
        assert_conforms(everything, "AqRiskResponse")
        assert set(everything["split"]) == set(S.VALID_SPLITS)

        empty = risk_payload("BG0051A", first="2024-01-01", last="2024-01-31")
        assert_conforms(empty, "AqRiskResponse")
        assert empty["target_dates"] == []

    def test_risk_latest_payload_conforms_and_carries_drivers(self):
        latest = risk_payload("BG0051A", latest=True)
        assert_conforms(latest, "AqRiskResponse")
        assert latest["target_dates"] == ["2026-10-28"]
        assert latest["issued_at"] == ["2026-10-27T06:00:00Z"]
        assert latest["model_id"] == [CURRENT_MODEL]
        drivers = latest["drivers"]
        assert drivers["rank"] == [1, 2, 3, 4, 5]
        magnitudes = [abs(c) for c in drivers["contribution"]]
        assert magnitudes == sorted(magnitudes, reverse=True)
        assert len(drivers["feature"]) == len(drivers["contribution"]) == len(drivers["rank"])

        with_split = risk_payload("BG0051A", latest=True, split="test")
        assert with_split["target_dates"] == ["2026-10-13"]
        assert_conforms(with_split, "AqRiskResponse")

    def test_model_payloads_conform(self):
        for model_id in (CURRENT_MODEL, PRIOR_MODEL):
            card = card_payload(model_id)
            assert_conforms(card, "ModelCardResponse")
            assert card["windows"]["train_end"] < card["windows"]["validation_start"]
            calibration = calibration_payload(model_id)
            assert_conforms(calibration, "ModelCalibrationResponse")
            assert len(calibration["n"]) == 10
        assert card_payload(PRIOR_MODEL)["baselines"]["roc_auc_test_persistence"] is None
        assert calibration_payload(CURRENT_MODEL)["predicted_mean"][-1] is None

    def test_error_envelope_for_503_conforms(self):
        assert_conforms(
            {"error": {"code": "SERVICE_UNAVAILABLE", "message": "mart_aq_trend_monthly is not published."}},
            "ErrorEnvelope",
        )

    def test_tampered_payloads_are_rejected(self):
        risk = risk_payload("BG0051A", first="2026-10-14", last="2026-10-20")

        bad_probability = copy.deepcopy(risk)
        bad_probability["probability"][0] = 1.5
        assert oas_errors(bad_probability, "AqRiskResponse")

        bad_split = copy.deepcopy(risk)
        bad_split["split"][0] = "holdout"
        assert oas_errors(bad_split, "AqRiskResponse")

        bad_label = copy.deepcopy(risk)
        bad_label["actual_label"][0] = 2
        assert oas_errors(bad_label, "AqRiskResponse")

        missing = copy.deepcopy(risk)
        del missing["issued_at"]
        assert oas_errors(missing, "AqRiskResponse")

        card = card_payload(CURRENT_MODEL)
        no_windows = copy.deepcopy(card)
        del no_windows["windows"]
        assert oas_errors(no_windows, "ModelCardResponse")
        bad_metric = copy.deepcopy(card)
        bad_metric["metrics"]["roc_auc_test"] = 1.4
        assert oas_errors(bad_metric, "ModelCardResponse")

        trend = trend_payload("BG0051A", "pm10", "2025-05-01", "2026-10-31")
        bad_pollutant = copy.deepcopy(trend)
        bad_pollutant["pollutant"] = "so2"
        assert oas_errors(bad_pollutant, "AqTrendResponse")
        bad_n_days = copy.deepcopy(trend)
        bad_n_days["n_days"][0] = 40
        assert oas_errors(bad_n_days, "AqTrendResponse")


# ---------------------------------------------------------------------------
# Placeholder for the forecast verification mart
# ---------------------------------------------------------------------------


class TestForecastVerificationPlaceholder:
    def test_placeholder_is_documented_but_not_defined(self):
        """The contract notes reserve the name; no schema, manifest entry, fixture or endpoint exists yet."""
        notes = (CONTRACTS_DIR / "README.md").read_text(encoding="utf-8")
        assert "mart_forecast_verification" in notes
        assert "placeholder" in notes.lower()
        assert "mart_forecast_verification" not in (CONTRACTS_DIR / "manifest.schema.json").read_text(encoding="utf-8")
        assert "mart_forecast_verification" not in (CONTRACTS_DIR / "openapi.yaml").read_text(encoding="utf-8")
        assert "mart_forecast_verification" not in (CONTRACTS_DIR / "schemas.py").read_text(encoding="utf-8")
        assert not (FIXTURES_DIR / "mart_forecast_verification").exists()
