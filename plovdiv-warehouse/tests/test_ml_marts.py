"""Model and trend marts: contract schemas, determinism, driver arithmetic and the manifest.

The end-to-end tests train the risk model on synthetic landed files (see ml_helpers) and build
the marts from them, so they run without the real landing data. Tests that need the real
landed files and the trained model in `models/` are skipped when those are absent.
"""

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

pytest.importorskip("sklearn")

from jsonschema import Draft7Validator  # noqa: E402
from ml_helpers import eea_frame, synthetic_days, synthetic_series, write_landed_inputs  # noqa: E402

from plovdiv.ml import dataset, marts, metrics, train  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "contracts" / "manifest.schema.json"
TRAINED_AT_OK = "2022-12-31"  # before the test window starts (2023-01-01)
REPLICATES = 20


@pytest.fixture(scope="module")
def landed(tmp_path_factory):
    eea_dir, weather_file = write_landed_inputs(tmp_path_factory.mktemp("landed"))
    days = synthetic_days("2017-01-01", "2025-12-31")
    data = synthetic_series(synthetic_days(), 5)
    second = data["pm10"][-len(days):] * 1.3
    eea_frame(days, second, dataset.PM10_CODE).to_parquet(
        eea_dir / f"SPO-BG0078A_{dataset.PM10_CODE}_100.parquet", index=False
    )
    return eea_dir, weather_file


@pytest.fixture(scope="module")
def models_dir(landed, tmp_path_factory):
    eea_dir, weather_file = landed
    out = tmp_path_factory.mktemp("models")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(metrics, "BOOTSTRAP_REPLICATES", 20)
        train.run(eea_dir, weather_file, out, TRAINED_AT_OK)
    return out


@pytest.fixture(scope="module")
def built(landed, models_dir):
    eea_dir, weather_file = landed
    return marts.build_all(models_dir, eea_dir, weather_file, n_replicates=REPLICATES)


@pytest.fixture(scope="module")
def documents(models_dir):
    return [
        marts.read_json(models_dir / marts.PRIMARY_MODEL_FILE),
        marts.read_json(models_dir / marts.SECOND_MODEL_FILE),
        marts.read_json(models_dir / marts.LIVE_MODEL_FILE),
    ]


@pytest.fixture(scope="module")
def predictions(models_dir):
    return pd.read_parquet(models_dir / marts.PREDICTIONS_FILE)


# ---------------------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", marts.MART_ORDER)
def test_every_mart_passes_its_contract_schema(built, name):
    frames, _ = built
    assert marts.validate(name, marts.to_table(name, frames[name])) == []


def test_row_counts_follow_the_inputs(built, predictions, documents):
    frames, _ = built
    assert len(frames["mart_model_card"]) == 3
    assert len(frames["mart_model_calibration"]) == 30
    assert len(frames["mart_aq_risk_daily"]) == 3 * len(predictions)
    tests = int((predictions["split"] == "test").sum())
    assert len(frames["mart_aq_risk_drivers"]) == marts.DRIVERS_PUBLISHED * tests
    trend = frames["mart_aq_trend_monthly"]
    assert set(trend["station_id"]) == {"BG0051A", "BG0078A"}
    assert (trend["pollutant"] == "pm10").all()
    assert len(trend[trend["station_id"] == "BG0051A"]) == 156
    assert len(trend[trend["station_id"] == "BG0078A"]) == 108


def test_model_ids_are_consistent_across_marts(built):
    frames, _ = built
    ids = set(frames["mart_model_card"]["model_id"])
    assert ids == {"pm10_risk_logreg", "pm10_risk_logreg_no_next_day_weather", "pm10_risk_logreg_live"}
    assert set(frames["mart_aq_risk_daily"]["model_id"]) == ids
    assert set(frames["mart_model_calibration"]["model_id"]) == ids
    assert set(frames["mart_aq_risk_drivers"]["model_id"]) == {"pm10_risk_logreg"}
    assert set(frames["mart_aq_risk_daily"]["label_id"]) == set(frames["mart_model_card"]["label_id"])


def test_model_card_content(built):
    card = built[0]["mart_model_card"].set_index("model_id")
    primary = card.loc["pm10_risk_logreg"]
    second = card.loc["pm10_risk_logreg_no_next_day_weather"]
    assert primary["uses_forecast_weather"] and not second["uses_forecast_weather"]
    assert "upper bound" in primary["limitations"]
    assert "one station" in primary["limitations"].lower()
    assert "2016" in primary["limitations"]
    assert "2023 to 2025" in primary["limitations"]
    assert "75th percentile" in primary["limitations"]
    assert "does not know the weather" in second["limitations"]
    assert primary["roc_auc_test"] > 0.5 and second["threshold"] == pytest.approx(second["threshold"])
    assert list(primary["features"])[0] == "log_pm10_t"
    assert len(primary["features"]) > len(second["features"])


def test_model_card_accepts_a_training_date_after_the_test_window(documents, models_dir):
    """trained_at records when the fitting ran; the windows already guarantee the row ordering."""
    metrics_doc = marts.read_json(models_dir / marts.METRICS_FILE)
    late = copy.deepcopy(documents)
    for doc in late:
        doc["trained_at"] = "2026-10-07"
    card = marts.build_model_card(metrics_doc, late)
    assert marts.validate("mart_model_card", marts.to_table("mart_model_card", card)) == []


def test_blocked_mart_is_not_written_and_is_reported(landed, models_dir, tmp_path):
    eea_dir, weather_file = landed
    frames, diagnostics = marts.build_all(models_dir, eea_dir, weather_file, n_replicates=5)
    broken = frames["mart_model_card"].copy()
    broken["roc_auc_test"] = 2.0  # outside the allowed 0..1 range
    frames = {**frames, "mart_model_card": broken}
    report = marts.write_marts(frames, tmp_path, diagnostics, "2026-10-07T00:00:00Z", "run")
    assert "mart_model_card" in report["marts_blocked"]
    assert not marts.mart_path(tmp_path, "mart_model_card").exists()
    assert marts.mart_path(tmp_path, "mart_aq_trend_monthly").exists()
    manifest = json.loads((tmp_path / "manifest.partial.json").read_text(encoding="utf-8"))
    assert manifest["blocked_marts"] == ["mart_model_card"]
    assert "mart_model_card" not in manifest["marts"]


# ---------------------------------------------------------------------------------------
# Risk and drivers
# ---------------------------------------------------------------------------------------


def test_risk_rows_follow_the_issue_time_convention(built, predictions):
    risk = built[0]["mart_aq_risk_daily"]
    assert set(risk["split"]) == {"validation", "test"}
    issued = pd.to_datetime(risk["issued_at"])
    target = pd.to_datetime(risk["target_date"]).dt.tz_localize("UTC")
    assert (issued == target).all()  # 00:00 UTC of the target day is the end of day t in UTC
    assert str(issued.dt.tz) == "UTC"
    assert risk["actual_label"].isin([0, 1]).all()
    primary = risk[risk["model_id"] == "pm10_risk_logreg"].sort_values("target_date")
    stored = predictions.sort_values("date")
    assert np.array_equal(primary["probability"].to_numpy(), stored["probability"].to_numpy())
    assert np.array_equal(primary["actual_label"].to_numpy(), stored["label"].to_numpy())


def test_risk_builder_rejects_training_rows_and_misaligned_days(predictions, documents):
    with_train = predictions.copy()
    with_train.loc[with_train.index[0], "split"] = "train"
    with pytest.raises(ValueError, match="unexpected splits"):
        marts.build_risk_daily(with_train, documents)
    shifted = predictions.copy()
    shifted["asof_date"] = shifted["asof_date"] - pd.Timedelta(days=1)
    with pytest.raises(ValueError, match="day after"):
        marts.build_risk_daily(shifted, documents)


def test_contributions_plus_intercept_reproduce_the_logit_on_synthetic_weights():
    rng = np.random.default_rng(0)
    doc = {
        "scaler_mean": rng.normal(size=6).tolist(),
        "scaler_scale": rng.uniform(0.5, 3.0, size=6).tolist(),
        "coefficients_standardised": rng.normal(size=6).tolist(),
        "intercept_standardised": -0.7,
    }
    x = rng.normal(size=(50, 6)) * 2
    contrib = marts.contributions(doc, x)
    logit = doc["intercept_standardised"] + contrib.sum(axis=1)
    probability = marts.reconstruct_probability(doc, x)
    assert np.allclose(np.log(probability / (1 - probability)), logit, atol=1e-9)
    z = (x - np.array(doc["scaler_mean"])) / np.array(doc["scaler_scale"])
    assert np.allclose(contrib, z * np.array(doc["coefficients_standardised"]))


def test_driver_contributions_reproduce_the_stored_probability(landed, documents, predictions):
    """Intercept plus all contributions gives the logit of the stored probability to 1e-6."""
    eea_dir, weather_file = landed
    table = marts.model_feature_table(eea_dir, weather_file)
    doc = documents[0]
    sample = predictions[predictions["split"] == "test"].iloc[::37]
    assert len(sample) > 10
    x = table.loc[pd.DatetimeIndex(sample["asof_date"]), doc["feature_names"]].to_numpy(dtype=float)
    logit = doc["intercept_standardised"] + marts.contributions(doc, x).sum(axis=1)
    p = sample["probability"].to_numpy()
    assert np.max(np.abs(logit - np.log(p / (1 - p)))) < 1e-6


def test_published_drivers_are_the_five_largest_and_ranked(built, landed, documents):
    frames, _ = built
    drivers = frames["mart_aq_risk_drivers"]
    eea_dir, weather_file = landed
    table = marts.model_feature_table(eea_dir, weather_file)
    doc = documents[0]
    names = doc["feature_names"]
    for target, group in list(drivers.groupby("target_date"))[::90]:
        asof = pd.Timestamp(target) - pd.Timedelta(days=1)
        full = marts.contributions(doc, table.loc[[asof], names].to_numpy(dtype=float))[0]
        top = np.argsort(-np.abs(full), kind="stable")[:5]
        ordered = group.sort_values("rank")
        assert list(ordered["rank"]) == [1, 2, 3, 4, 5]
        assert list(ordered["feature"]) == [names[j] for j in top]
        assert np.allclose(ordered["contribution"], full[top], atol=1e-8)
    assert set(pd.to_datetime(drivers["target_date"]).dt.year) == {2023, 2024, 2025}


def test_driver_and_risk_rows_match_one_to_one(built):
    frames, _ = built
    risk = frames["mart_aq_risk_daily"]
    keys = set(
        map(tuple, risk[(risk["model_id"] == "pm10_risk_logreg") & (risk["split"] == "test")][["station_id", "target_date"]].to_numpy())
    )
    driver_keys = set(map(tuple, frames["mart_aq_risk_drivers"][["station_id", "target_date"]].drop_duplicates().to_numpy()))
    assert keys == driver_keys


def test_inputs_that_do_not_reproduce_the_predictions_are_rejected(landed, documents, predictions):
    eea_dir, weather_file = landed
    table = marts.model_feature_table(eea_dir, weather_file)
    marts.check_model_inputs(predictions, table, documents)
    tampered = predictions.copy()
    tampered.loc[tampered.index[3], "probability"] += 0.01
    with pytest.raises(ValueError, match="differ"):
        marts.check_model_inputs(tampered, table, documents)


# ---------------------------------------------------------------------------------------
# Calibration and trend
# ---------------------------------------------------------------------------------------


def test_calibration_matches_the_test_predictions(built, predictions):
    calibration = built[0]["mart_model_calibration"]
    test = predictions[predictions["split"] == "test"]
    for model_id, column in (
        ("pm10_risk_logreg", "probability"),
        ("pm10_risk_logreg_no_next_day_weather", "probability_no_next_day_weather"),
        ("pm10_risk_logreg_live", "probability_live"),
    ):
        expected = metrics.calibration_bins(test["label"].to_numpy(), test[column].to_numpy())
        got = calibration[calibration["model_id"] == model_id].sort_values("bin_lo")
        assert list(got["n"]) == [b["count"] for b in expected]
        for row, b in zip(got.itertuples(), expected):
            if b["count"]:
                assert row.predicted_mean == pytest.approx(b["predicted_mean"], abs=1e-9)
                assert row.observed_rate == pytest.approx(b["observed_rate"], abs=1e-9)
            else:
                assert pd.isna(row.predicted_mean) and pd.isna(row.observed_rate)
        assert got["n"].sum() == len(test)


def test_trend_rows_match_the_station_files(built, landed):
    frames, _ = built
    eea_dir, _ = landed
    trend = frames["mart_aq_trend_monthly"]
    series = dataset.load_eea_daily(eea_dir / "SPO-BG0078A_00005_100.parquet")
    month = pd.Timestamp("2022-02-01")
    expected = series[(series.index >= month) & (series.index < month + pd.offsets.MonthBegin(1))]
    row = trend[(trend["station_id"] == "BG0078A") & (pd.to_datetime(trend["month"]) == month)].iloc[0]
    assert row["n_days"] == len(expected)
    assert row["observed_mean_ugm3"] == pytest.approx(expected.mean(), abs=1e-4)
    short = trend[trend["n_days"] < 15]
    assert short["weather_adjusted_mean_ugm3"].isna().all()
    estimated = trend.dropna(subset=["weather_adjusted_mean_ugm3"])
    assert (estimated["adjusted_lo_ugm3"] <= estimated["weather_adjusted_mean_ugm3"]).all()
    assert (estimated["weather_adjusted_mean_ugm3"] <= estimated["adjusted_hi_ugm3"]).all()


# ---------------------------------------------------------------------------------------
# Files and manifest
# ---------------------------------------------------------------------------------------


def _write(built, out):
    frames, diagnostics = built
    return marts.write_marts(frames, out, diagnostics, "2026-10-07T00:00:00Z", "run-test")


def test_output_is_byte_identical_when_built_twice(landed, models_dir, tmp_path):
    eea_dir, weather_file = landed
    digests = []
    for i in range(2):
        built_again = marts.build_all(models_dir, eea_dir, weather_file, n_replicates=REPLICATES)
        out = tmp_path / f"run{i}"
        _write(built_again, out)
        digests.append(
            {
                str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(out.rglob("*"))
                if p.is_file()
            }
        )
    assert digests[0] == digests[1]
    assert any(name.endswith(".parquet") for name in digests[0])
    assert "manifest.partial.json" in digests[0]


def test_files_have_contract_column_types_and_no_pandas_metadata(built, tmp_path):
    _write(built, tmp_path)
    for name in marts.MART_ORDER:
        path = marts.mart_path(tmp_path, name)
        schema = pq.read_schema(path)
        assert schema.equals(marts.ARROW_SCHEMAS[name].remove_metadata(), check_metadata=False), name
        assert b"pandas" not in (schema.metadata or {})
        frame = pd.read_parquet(path)
        keys = {
            "mart_model_card": ["model_id"],
            "mart_model_calibration": ["model_id", "bin_lo"],
            "mart_aq_risk_daily": ["station_id", "target_date", "model_id"],
            "mart_aq_risk_drivers": ["station_id", "target_date", "model_id", "feature"],
            "mart_aq_trend_monthly": ["station_id", "pollutant", "month"],
        }[name]
        assert not frame.duplicated(keys).any()
        assert frame.equals(frame.sort_values(keys[:-1] + [keys[-1]]).reset_index(drop=True)) or name == "mart_aq_risk_drivers"


def test_partial_manifest_lacks_only_the_two_required_marts_and_the_variables(built, tmp_path):
    _write(built, tmp_path)
    manifest = json.loads((tmp_path / "manifest.partial.json").read_text(encoding="utf-8"))
    assert manifest["partial"] is True
    assert manifest["missing_required_marts"] == ["mart_weather_aq_hourly", "mart_aq_daily"]
    assert "mart_weather_aq_hourly" not in manifest["marts"] and "mart_aq_daily" not in manifest["marts"]
    candidate = {k: v for k, v in manifest.items() if k not in ("partial", "partial_reason", "missing_required_marts", "blocked_marts")}
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = list(Draft7Validator(schema).iter_errors(candidate))
    messages = sorted(e.message for e in errors)
    assert messages == sorted(
        [
            "'variables' is a required property",
            "'mart_weather_aq_hourly' is a required property",
            "'mart_aq_daily' is a required property",
        ]
    ), messages
    # Every entry that is present is valid on its own.
    for name, entry in candidate["marts"].items():
        sub = schema["properties"]["marts"]["properties"][name]
        assert not list(Draft7Validator(sub).iter_errors(entry)), name
    assert set(candidate["stations"]) == {"BG0051A", "BG0078A"}


def test_manifest_counts_and_ranges_match_the_files(built, tmp_path):
    _write(built, tmp_path)
    manifest = json.loads((tmp_path / "manifest.partial.json").read_text(encoding="utf-8"))
    for name, entry in manifest["marts"].items():
        frame = pd.read_parquet(marts.mart_path(tmp_path, name))
        assert entry["rows"] == len(frame)
    entry = manifest["marts"]["mart_aq_trend_monthly"]
    assert entry["min_month"] == "2013-01-01" and entry["max_month"] == "2025-12-01"
    risk = manifest["marts"]["mart_aq_risk_daily"]
    assert risk["min_target_date"] == "2021-01-01" and risk["max_target_date"] == "2025-12-31"


# ---------------------------------------------------------------------------------------
# Real landed files and trained model (skipped when absent)
# ---------------------------------------------------------------------------------------

REAL_NEEDED = [
    marts.DEFAULT_MODELS_DIR / marts.PRIMARY_MODEL_FILE,
    marts.DEFAULT_MODELS_DIR / marts.SECOND_MODEL_FILE,
    marts.DEFAULT_MODELS_DIR / marts.METRICS_FILE,
    marts.DEFAULT_MODELS_DIR / marts.PREDICTIONS_FILE,
]
real_data = pytest.mark.skipif(
    not all(p.exists() for p in REAL_NEEDED) or not list(marts.LANDING.glob("source=eea/endpoint=e1a_daily/fetch_date=*/SPO-BG0078A_00005_100.parquet")),
    reason="real landed files or trained model not present",
)


@real_data
def test_real_inputs_reproduce_the_stored_predictions_and_build_valid_marts():
    frames, diagnostics = marts.build_all(
        marts.DEFAULT_MODELS_DIR, marts.default_eea_dir(), marts.default_weather_file(), n_replicates=10
    )
    assert max(diagnostics["input_check_max_abs_probability_difference"].values()) < 1e-6
    for name in marts.MART_ORDER:
        problems = marts.validate(name, marts.to_table(name, frames[name]))
        assert problems == [], name
    risk = frames["mart_aq_risk_daily"]
    reference = risk[risk["station_id"] == "BG0051A"]
    assert len(reference) == 3 * 1770  # three Kamenitsa models, 1770 validation and test days
    trakia = risk[risk["station_id"] == "BG0078A"]
    trakia_doc = marts.read_json(marts.DEFAULT_MODELS_DIR / "pm10_risk_logreg_live_trakia.json")
    assert set(trakia["model_id"]) == {"pm10_risk_logreg_live_trakia"}
    assert len(trakia) == sum(trakia_doc["rows"][s]["n_rows"] for s in ("validation", "test"))
    drivers = frames["mart_aq_risk_drivers"]
    assert len(drivers[drivers["station_id"] == "BG0051A"]) == 5320
    assert len(drivers[drivers["station_id"] == "BG0078A"]) == 5 * trakia_doc["rows"]["test"]["n_rows"]
    assert len(frames["mart_aq_trend_monthly"]) == 264
    card = frames["mart_model_card"].set_index("model_id")
    assert card.loc["pm10_risk_logreg_live_trakia", "roc_auc_test"] == trakia_doc["evaluation"]["test"]["roc_auc"]


@real_data
def test_real_driver_contributions_reproduce_the_logit():
    doc = marts.read_json(marts.DEFAULT_MODELS_DIR / marts.PRIMARY_MODEL_FILE)
    predictions = pd.read_parquet(marts.DEFAULT_MODELS_DIR / marts.PREDICTIONS_FILE)
    table = marts.model_feature_table(marts.default_eea_dir(), marts.default_weather_file())
    sample = predictions.iloc[::25]
    x = table.loc[pd.DatetimeIndex(sample["asof_date"]), doc["feature_names"]].to_numpy(dtype=float)
    logit = doc["intercept_standardised"] + marts.contributions(doc, x).sum(axis=1)
    p = sample["probability"].to_numpy()
    assert len(sample) > 60
    assert np.max(np.abs(logit - np.log(p / (1 - p)))) < 1e-6
