"""Training run on synthetic landed files: outputs, determinism, no use of later rows."""

import json

import numpy as np
import pandas as pd
import pytest
from ml_helpers import write_landed_inputs

from plovdiv.ml import dataset, features, metrics, train


@pytest.fixture(autouse=True)
def fast_bootstrap(monkeypatch):
    monkeypatch.setattr(metrics, "BOOTSTRAP_REPLICATES", 20)


@pytest.fixture(scope="module")
def landed(tmp_path_factory):
    return write_landed_inputs(tmp_path_factory.mktemp("landed"))


@pytest.fixture(scope="module")
def outputs(landed, tmp_path_factory):
    eea_dir, weather_file = landed
    out = tmp_path_factory.mktemp("models")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(metrics, "BOOTSTRAP_REPLICATES", 20)
        document = train.run(eea_dir, weather_file, out, "2026-01-02")
    return out, document


def test_all_output_files_are_written(outputs):
    out, _ = outputs
    names = {p.name for p in out.iterdir()}
    assert {
        "pm10_risk_logreg.joblib",
        "pm10_risk_logreg.json",
        "pm10_risk_logreg_no_next_day_weather.joblib",
        "pm10_risk_logreg_no_next_day_weather.json",
        "pm10_risk_logreg_live.joblib",
        "pm10_risk_logreg_live.json",
        "pm10_risk_metrics.json",
        "pm10_risk_daily_predictions.parquet",
    } <= names


def test_predictions_cover_validation_and_test_target_days_only(outputs):
    out, _ = outputs
    frame = pd.read_parquet(out / "pm10_risk_daily_predictions.parquet")
    assert set(frame["split"]) == {"validation", "test"}
    assert (frame["station_id"] == "BG0051A").all()
    assert frame["probability"].between(0, 1).all()
    assert frame["label"].isin([0, 1]).all()
    assert frame["date"].min() >= pd.Timestamp("2021-01-01")
    assert frame["date"].max() <= pd.Timestamp("2025-12-31")
    assert (frame["date"] == frame["asof_date"] + pd.Timedelta(days=1)).all()
    assert frame[frame["split"] == "validation"]["date"].max() <= pd.Timestamp("2022-12-31")
    assert frame[frame["split"] == "test"]["date"].min() >= pd.Timestamp("2023-01-01")
    assert frame["date"].is_unique


def test_weights_file_contains_the_documented_fields(outputs):
    out, _ = outputs
    weights = json.loads((out / "pm10_risk_logreg.json").read_text(encoding="utf-8"))
    n = len(weights["feature_names"])
    assert weights["feature_names"] == features.VARIANTS[features.PRIMARY_VARIANT]
    assert len(weights["scaler_mean"]) == len(weights["scaler_scale"]) == n
    assert len(weights["coefficients_standardised"]) == n
    assert weights["label"]["id"] == dataset.LABEL_ID
    assert weights["estimator"]["C"] in train.C_GRID
    assert weights["estimator"]["class_weight"] == "balanced"
    assert weights["trained_at"] == "2026-01-02"
    assert weights["periods_by_target_date"]["test"] == ["2023-01-01", "2025-12-31"]
    assert weights["uses_next_day_weather"] is True
    files = weights["provenance"]["input_files"]
    assert [f["role"] for f in files] == [
        "pm10_kamenitsa_daily",
        "pm25_kamenitsa_daily",
        "era5_archive_daily",
    ]
    assert all(f["size_bytes"] > 0 and len(f["sha256"]) == 64 for f in files)


def test_scaler_is_fitted_on_training_rows_only(landed, outputs):
    out, _ = outputs
    eea_dir, weather_file = landed
    daily = dataset.build_daily_table(
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_00005_100.parquet"),
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_06001_100.parquet"),
        dataset.load_weather(weather_file),
    )
    table = features.build_model_table(daily)
    columns = features.VARIANTS[features.PRIMARY_VARIANT]
    frames, _ = train.split_frames(table, columns)
    weights = json.loads((out / "pm10_risk_logreg.json").read_text(encoding="utf-8"))
    expected = frames["train"][columns].to_numpy(dtype=float).mean(axis=0)
    np.testing.assert_allclose(weights["scaler_mean"], expected, rtol=1e-9)


def test_saved_weights_round_trip_to_the_same_probabilities(landed, outputs):
    out, _ = outputs
    eea_dir, weather_file = landed
    daily = dataset.build_daily_table(
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_00005_100.parquet"),
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_06001_100.parquet"),
        dataset.load_weather(weather_file),
    )
    table = features.build_model_table(daily)
    columns = features.VARIANTS[features.PRIMARY_VARIANT]
    frames, _ = train.split_frames(table, columns)
    test = frames["test"]
    x = test[columns].to_numpy(dtype=float)

    saved = train.load_model(out / "pm10_risk_logreg.joblib")
    assert saved["feature_names"] == columns
    from_pipeline = saved["pipeline"].predict_proba(x)[:, 1]

    weights = json.loads((out / "pm10_risk_logreg.json").read_text(encoding="utf-8"))
    from_json = train.predict_from_weights(weights, x)
    np.testing.assert_allclose(from_pipeline, from_json, rtol=1e-9, atol=1e-12)

    raw_logit = x @ np.asarray(weights["coefficients_raw_scale"]) + weights["intercept_raw_scale"]
    np.testing.assert_allclose(1 / (1 + np.exp(-raw_logit)), from_pipeline, rtol=1e-7, atol=1e-10)

    predictions = pd.read_parquet(out / "pm10_risk_daily_predictions.parquet")
    stored = predictions[predictions["split"] == "test"].sort_values("date")["probability"]
    np.testing.assert_allclose(stored.to_numpy(), from_pipeline, rtol=1e-12)


def test_live_model_is_saved_like_the_deployable_one(landed, outputs):
    """The live variant gets a joblib and a readable JSON file; both give the stored probabilities."""
    out, document = outputs
    eea_dir, weather_file = landed
    daily = dataset.build_daily_table(
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_00005_100.parquet"),
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_06001_100.parquet"),
        dataset.load_weather(weather_file),
    )
    table = features.build_model_table(daily)
    frames, _ = train.split_frames(table, features.VARIANTS[features.PRIMARY_VARIANT])
    columns = features.VARIANTS[features.LIVE_VARIANT]
    x = frames["test"][columns].to_numpy(dtype=float)

    saved = train.load_model(out / "pm10_risk_logreg_live.joblib")
    weights = json.loads((out / "pm10_risk_logreg_live.json").read_text(encoding="utf-8"))
    assert saved["variant"] == weights["variant"] == "live"
    assert weights["model"] == "pm10_risk_logreg_live"
    assert saved["feature_names"] == weights["feature_names"] == columns
    assert "log_pm25_t" not in columns and "radiation_mj_m2_t" not in columns
    assert weights["uses_next_day_weather"] is False
    assert weights["threshold"]["value"] == document["ablation"]["live"]["threshold"]
    from_pipeline = saved["pipeline"].predict_proba(x)[:, 1]
    np.testing.assert_allclose(train.predict_from_weights(weights, x), from_pipeline, rtol=1e-9, atol=1e-12)
    predictions = pd.read_parquet(out / "pm10_risk_daily_predictions.parquet")
    stored = predictions[predictions["split"] == "test"].sort_values("date")["probability_live"]
    np.testing.assert_allclose(stored.to_numpy(), from_pipeline, rtol=1e-12)


def test_metrics_file_has_model_ablation_baselines_and_calibration(outputs):
    out, document = outputs
    on_disk = json.loads((out / "pm10_risk_metrics.json").read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == train.METRICS_SCHEMA_VERSION
    assert set(on_disk["baselines"]) == {"pm10_only_logreg", "monthly_climatology", "persistence"}
    assert set(on_disk["ablation"]) - {"note"} == {
        "with_next_day_weather",
        "no_next_day_weather",
        "no_weather",
        "live",
    }
    test = on_disk["model"]["test"]
    for key in ("roc_auc", "pr_auc", "brier", "precision", "recall", "tn", "fp", "fn", "tp"):
        assert key in test
    bins = test["calibration_bins"]
    assert len(bins) == 10
    assert sum(b["count"] for b in bins) == test["n"]
    assert "ci95_low" in test["roc_auc_bootstrap"]
    for split in ("train", "validation", "test"):
        assert on_disk["class_balance"][split]["primary_label"]["n"] > 0
    ranking = on_disk["model"]["standardised_coefficient_ranking"]
    assert [r["rank"] for r in ranking] == list(range(1, len(ranking) + 1))
    sizes = [r["abs_coefficient"] for r in ranking]
    assert sizes == sorted(sizes, reverse=True)
    assert "optimistic upper bound" in on_disk["ablation"]["note"]
    assert json.dumps(document)  # strictly serialisable, no NaN


def test_training_is_deterministic(landed, outputs, tmp_path):
    out, _ = outputs
    eea_dir, weather_file = landed
    train.run(eea_dir, weather_file, tmp_path, "2026-01-02")
    for name in (
        "pm10_risk_logreg.json",
        "pm10_risk_metrics.json",
        "pm10_risk_logreg.joblib",
    ):
        assert (out / name).read_bytes() == (tmp_path / name).read_bytes()
    pd.testing.assert_frame_equal(
        pd.read_parquet(out / "pm10_risk_daily_predictions.parquet"),
        pd.read_parquet(tmp_path / "pm10_risk_daily_predictions.parquet"),
    )


def test_validation_and_test_rows_do_not_change_the_fitted_weights_except_through_c(landed):
    eea_dir, weather_file = landed
    daily = dataset.build_daily_table(
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_00005_100.parquet"),
        dataset.load_eea_daily(eea_dir / "SPO-BG0051A_06001_100.parquet"),
        dataset.load_weather(weather_file),
    )
    table = features.build_model_table(daily)
    columns = features.VARIANTS["no_next_day_weather"]
    frames, _ = train.split_frames(table, columns)
    reference = train.fit_variant(frames, columns)

    altered = {k: v.copy() for k, v in frames.items()}
    altered["test"]["label"] = 1.0 - altered["test"]["label"]
    for column in columns:
        altered["test"][column] = altered["test"][column] * 3.0 + 1.0
    same = train.fit_variant(altered, columns)
    assert same["C"] == reference["C"]
    np.testing.assert_array_equal(
        same["pipeline"].named_steps["clf"].coef_, reference["pipeline"].named_steps["clf"].coef_
    )

    # Changing validation labels may change the chosen C, but the grid values come from train fits.
    assert reference["C"] in train.C_GRID


def test_chosen_threshold_comes_from_validation_rows(outputs):
    _, document = outputs
    model = document["model"]
    assert model["validation"]["threshold"] == model["threshold"] == model["test"]["threshold"]
    assert model["threshold"] in set(metrics.THRESHOLD_GRID.tolist())


def test_metric_helpers_on_known_values():
    y = np.array([0, 0, 1, 1, 1, 0])
    p = np.array([0.1, 0.4, 0.35, 0.8, 0.9, 0.2])
    result = metrics.evaluate(y, p, 0.5)
    assert result["tp"] == 2 and result["fn"] == 1 and result["fp"] == 0 and result["tn"] == 3
    assert result["precision"] == 1.0 and result["recall"] == pytest.approx(2 / 3)
    assert result["brier"] == pytest.approx(np.mean((p - y) ** 2))
    bins = metrics.calibration_bins(y, p)
    assert len(bins) == 10 and sum(b["count"] for b in bins) == 6
    assert bins[4]["count"] == 1 and bins[4]["observed_rate"] == 0.0  # 0.4 falls in [0.4, 0.5)
    assert metrics.class_balance(y) == {"n": 6, "positives": 3, "negatives": 3, "positive_rate": 0.5}


def test_bootstrap_interval_is_seeded_and_ordered():
    rng = np.random.default_rng(0)
    y = (rng.random(400) < 0.3).astype(int)
    p = np.clip(y * 0.3 + rng.random(400) * 0.7, 0, 1)
    one = metrics.block_bootstrap_auc(y, p, replicates=50)
    two = metrics.block_bootstrap_auc(y, p, replicates=50)
    assert one == two
    assert one["ci95_low"] <= one["ci95_high"]
