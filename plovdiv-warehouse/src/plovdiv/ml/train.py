"""Train and evaluate the next-day PM10 risk model.

    python -m plovdiv.ml.train

Reads the landed EEA daily files and the landed ERA5 archive response, builds the time-ordered
model table (see plovdiv.ml.dataset and plovdiv.ml.features), fits a standardised, L2
regularised logistic regression per variant on the training years only, chooses the
regularisation strength and the decision threshold on the validation years only, and writes
the model, its readable weights, the metrics and the daily predictions to `models/`.

There is no random split anywhere; all randomness (the bootstrap) is seeded.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from plovdiv.ml import dataset, features, metrics

REPO_ROOT = Path(__file__).resolve().parents[3]
LANDING = REPO_ROOT / "data" / "landing"
DEFAULT_OUT_DIR = REPO_ROOT / "models"

MODEL_NAME = "pm10_risk_logreg"
METRICS_NAME = "pm10_risk_metrics.json"
PREDICTIONS_NAME = "pm10_risk_daily_predictions.parquet"
METRICS_SCHEMA_VERSION = 1

C_GRID = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]
MAX_ITER = 10000

# Parameters of the archive request that produced the landed weather file.
WEATHER_REQUEST = {
    "endpoint": "https://archive-api.open-meteo.com/v1/archive",
    "latitude": 42.15,
    "longitude": 24.75,
    "start_date": "2013-01-01",
    "end_date": "2025-12-31",
    "timezone": "Europe/Sofia",
    "wind_speed_unit": "ms",
    "daily": list(dataset.WEATHER_COLUMNS),
}

DAY_ALIGNMENT_NOTE = (
    "ERA5 days are Europe/Sofia local calendar days; EEA daily values are the daily means as "
    "reported by Bulgaria. Both are joined on the calendar date, so the day alignment is "
    "approximate."
)
NEXT_DAY_WEATHER_NOTE = (
    "Features ending in _t1 are ERA5 actuals of the target day, used as a stand-in for a "
    "perfect weather forecast. Real forecasts are imperfect, so scores of a model that uses "
    "them are an optimistic upper bound."
)


# ---------------------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------------------


def latest_partition(root: Path, pattern: str) -> Path:
    """Last partition directory by name, which is the latest fetch date."""
    found = sorted(root.glob(pattern))
    if not found:
        raise FileNotFoundError(f"no landed data under {root} matching {pattern}")
    return found[-1]


def default_eea_dir() -> Path:
    return latest_partition(LANDING, "source=eea/endpoint=e1a_daily/fetch_date=*")


def default_weather_file() -> Path:
    directory = latest_partition(LANDING, "source=openmeteo/endpoint=archive_daily_era5/fetch_date=*")
    for pattern in ("archive_*.json.gz", "archive_*.json"):
        found = sorted(directory.glob(pattern))
        if found:
            return found[-1]
    raise FileNotFoundError(f"no archive response in {directory}")


def file_record(role: str, path: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "role": role,
        "file": path.name,
        "partition": path.parent.name,
        "size_bytes": path.stat().st_size,
        "sha256": digest,
    }


# ---------------------------------------------------------------------------------------
# Modelling
# ---------------------------------------------------------------------------------------


def make_pipeline(c: float) -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    C=c,
                    class_weight="balanced",
                    solver="lbfgs",
                    max_iter=MAX_ITER,
                    random_state=0,
                ),
            ),
        ]
    )


def fit_variant(frames: dict, columns: list[str]) -> dict:
    """Fit one pipeline per C on the training rows and keep the C with the best validation AUC.

    On equal validation AUC the smaller C (stronger regularisation) wins.
    """
    x_train = frames["train"][columns].to_numpy(dtype=float)
    y_train = frames["train"]["label"].to_numpy(dtype=int)
    x_val = frames["validation"][columns].to_numpy(dtype=float)
    y_val = frames["validation"]["label"].to_numpy(dtype=int)

    grid = []
    best = None
    for c in C_GRID:
        pipeline = make_pipeline(c).fit(x_train, y_train)
        auc = float(roc_auc_score(y_val, pipeline.predict_proba(x_val)[:, 1]))
        grid.append({"C": c, "validation_roc_auc": auc})
        if best is None or auc > best[0]:
            best = (auc, c, pipeline)
    return {"C": best[1], "pipeline": best[2], "grid": grid}


def probabilities(pipeline: Pipeline, frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    return pipeline.predict_proba(frame[columns].to_numpy(dtype=float))[:, 1]


def coefficient_ranking(pipeline: Pipeline, columns: list[str]) -> list[dict]:
    """Standardised coefficients ordered by absolute size (rank 1 is the largest)."""
    coef = pipeline.named_steps["clf"].coef_[0]
    order = np.argsort(-np.abs(coef), kind="stable")
    return [
        {
            "rank": rank + 1,
            "feature": columns[i],
            "coefficient": float(coef[i]),
            "abs_coefficient": float(abs(coef[i])),
            "effect": "raises risk" if coef[i] > 0 else "lowers risk",
        }
        for rank, i in enumerate(order)
    ]


def evaluate_variant(
    name: str, columns: list[str], fit: dict, frames: dict, with_bootstrap: bool = True
) -> dict:
    pipeline = fit["pipeline"]
    p_val = probabilities(pipeline, frames["validation"], columns)
    p_test = probabilities(pipeline, frames["test"], columns)
    y_val = frames["validation"]["label"].to_numpy(dtype=int)
    y_test = frames["test"]["label"].to_numpy(dtype=int)
    threshold = metrics.choose_threshold_max_f1(y_val, p_val)
    return {
        "variant": name,
        "features": columns,
        "chosen_C": fit["C"],
        "C_grid_validation": fit["grid"],
        "threshold": threshold,
        "threshold_rule": "maximise F1 on the validation rows over thresholds 0.01..0.99",
        "validation": metrics.evaluate(y_val, p_val, threshold),
        "test": metrics.evaluate(y_test, p_test, threshold, with_bootstrap=with_bootstrap),
        "standardised_coefficient_ranking": coefficient_ranking(pipeline, columns),
        "intercept": float(pipeline.named_steps["clf"].intercept_[0]),
    }


def evaluate_baselines(frames: dict) -> dict:
    """Climatology and persistence on the same validation and test rows."""
    out = {}

    p_val = frames["validation"]["climatology_rate"].to_numpy(dtype=float)
    p_test = frames["test"]["climatology_rate"].to_numpy(dtype=float)
    y_val = frames["validation"]["label"].to_numpy(dtype=int)
    y_test = frames["test"]["label"].to_numpy(dtype=int)
    threshold = metrics.choose_threshold_max_f1(y_val, p_val)
    out["monthly_climatology"] = {
        "description": (
            "Probability is the share of days in the same calendar month of the previous year "
            "that exceed the threshold of the target month (the label threshold)."
        ),
        "threshold": threshold,
        "threshold_rule": "maximise F1 on the validation rows over thresholds 0.01..0.99",
        "validation": metrics.evaluate(y_val, p_val, threshold),
        "test": metrics.evaluate(y_test, p_test, threshold, with_bootstrap=True),
    }

    flag_val = frames["validation"]["bad_today"].to_numpy(dtype=float)
    flag_test = frames["test"]["bad_today"].to_numpy(dtype=float)
    out["persistence"] = {
        "description": (
            "Predicts a bad day tomorrow exactly when today is a bad day by the same label "
            "definition (PM10 of day t above the threshold of its own month)."
        ),
        "threshold": 0.5,
        "validation": metrics.evaluate(y_val, flag_val, 0.5, with_calibration=False),
        "test": metrics.evaluate(y_test, flag_test, 0.5, with_calibration=False, with_bootstrap=True),
    }
    return out


def secondary_label_report(frames: dict, pipeline: Pipeline, columns: list[str]) -> dict:
    """Primary-label model scored against the EU limit exceedance (PM10 > 50 ug/m3).

    For reporting only: nothing is refitted or tuned on this label.
    """
    out = {"definition": f"PM10 of day t+1 above {dataset.EU_DAILY_LIMIT:g} ug/m3 (EU daily limit)"}
    for split in ("validation", "test"):
        y = frames[split]["label_gt50"].to_numpy(dtype=int)
        p = probabilities(pipeline, frames[split], columns)
        out[split] = {
            "class_balance": metrics.class_balance(y),
            "roc_auc": float(roc_auc_score(y, p)),
            "pr_auc": float(average_precision_score(y, p)),
        }
    return out


# ---------------------------------------------------------------------------------------
# Model files
# ---------------------------------------------------------------------------------------


def raw_scale_weights(pipeline: Pipeline) -> tuple[np.ndarray, float]:
    """Coefficients and intercept expressed on the unscaled feature values."""
    scaler = pipeline.named_steps["scaler"]
    clf = pipeline.named_steps["clf"]
    coef = clf.coef_[0] / scaler.scale_
    intercept = float(clf.intercept_[0] - np.sum(clf.coef_[0] * scaler.mean_ / scaler.scale_))
    return coef, intercept


def weights_document(
    variant: str,
    columns: list[str],
    fit: dict,
    threshold: float,
    provenance: dict,
    split_summary: dict,
    trained_at: str,
) -> dict:
    pipeline = fit["pipeline"]
    scaler = pipeline.named_steps["scaler"]
    clf = pipeline.named_steps["clf"]
    raw_coef, raw_intercept = raw_scale_weights(pipeline)
    uses_next_day = any(c.endswith("_t1") for c in columns)
    return {
        "model": MODEL_NAME if variant == features.PRIMARY_VARIANT else f"{MODEL_NAME}_{variant}",
        "variant": variant,
        "station_id": dataset.STATION_ID,
        "task": "P(label = 1 for day t+1 | data known at the end of day t)",
        "label": {
            "id": dataset.LABEL_ID,
            "text": dataset.LABEL_TEXT,
            "quantile": dataset.LABEL_QUANTILE,
            "history_years": dataset.HISTORY_YEARS,
        },
        "secondary_label_reporting_only": f"PM10 daily mean above {dataset.EU_DAILY_LIMIT:g} ug/m3",
        "feature_names": columns,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coefficients_standardised": clf.coef_[0].tolist(),
        "intercept_standardised": float(clf.intercept_[0]),
        "coefficients_raw_scale": raw_coef.tolist(),
        "intercept_raw_scale": raw_intercept,
        "scoring": (
            "probability = 1 / (1 + exp(-(intercept + sum_i coefficient_i * "
            "(x_i - scaler_mean_i) / scaler_scale_i)))"
        ),
        "estimator": {
            "penalty": "l2",
            "C": fit["C"],
            "class_weight": "balanced",
            "solver": "lbfgs",
            "max_iter": MAX_ITER,
            "n_iter": int(clf.n_iter_[0]),
        },
        "threshold": {
            "value": threshold,
            "rule": "maximise F1 on the validation rows over thresholds 0.01..0.99",
            "positive_if": "probability >= value",
        },
        "periods_by_target_date": {k: list(v) for k, v in dataset.SPLITS.items()},
        "rows": split_summary,
        "uses_next_day_weather": uses_next_day,
        "notes": ([NEXT_DAY_WEATHER_NOTE] if uses_next_day else []) + [DAY_ALIGNMENT_NOTE],
        "sklearn_version": sklearn.__version__,
        "trained_at": trained_at,
        "provenance": provenance,
    }


def save_model(path: Path, variant: str, columns: list[str], fit: dict, threshold: float, trained_at: str):
    joblib.dump(
        {
            "pipeline": fit["pipeline"],
            "variant": variant,
            "feature_names": columns,
            "threshold": threshold,
            "label_id": dataset.LABEL_ID,
            "sklearn_version": sklearn.__version__,
            "trained_at": trained_at,
        },
        path,
    )


def load_model(path: Path | str) -> dict:
    """Read a saved model file: a dict with the fitted pipeline and its metadata."""
    return joblib.load(path)


def predict_from_weights(document: dict, x: np.ndarray) -> np.ndarray:
    """Probabilities from the readable weights alone (no scikit-learn needed)."""
    x = np.asarray(x, dtype=float)
    z = (x - np.asarray(document["scaler_mean"])) / np.asarray(document["scaler_scale"])
    logit = document["intercept_standardised"] + z @ np.asarray(document["coefficients_standardised"])
    return 1.0 / (1.0 + np.exp(-logit))


def write_json(path: Path, document: dict) -> None:
    text = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------


def split_frames(table: pd.DataFrame, columns: list[str]) -> tuple[dict, pd.Series]:
    usable = features.usable_rows(table, columns)
    frames = {name: table[usable & (table["split"] == name)] for name in dataset.SPLITS}
    return frames, usable


def run(eea_dir: Path, weather_file: Path, out_dir: Path, trained_at: str) -> dict:
    pm10_path = eea_dir / f"SPO-{dataset.STATION_ID}_{dataset.PM10_CODE}_100.parquet"
    pm25_path = eea_dir / f"SPO-{dataset.STATION_ID}_{dataset.PM25_CODE}_100.parquet"

    pm10 = dataset.load_eea_daily(pm10_path)
    pm25 = dataset.load_eea_daily(pm25_path)
    weather = dataset.load_weather(weather_file)
    daily = dataset.build_daily_table(pm10, pm25, weather)
    table = features.build_model_table(daily)

    all_columns = features.VARIANTS[features.PRIMARY_VARIANT]
    frames, usable = split_frames(table, all_columns)
    for split, frame in frames.items():
        if frame.empty:
            raise ValueError(f"no usable rows in split {split}")
        if split == "train":
            continue
        for column in ("climatology_rate", "bad_today"):
            if frame[column].isna().any():
                raise ValueError(f"{column} missing on {split} rows")

    # Dropped rows, by split: a target day exists and has a label but a feature is missing.
    labelled = table["label"].notna() & table["split"].notna()
    dropped = {
        name: int((labelled & (table["split"] == name) & ~usable).sum()) for name in dataset.SPLITS
    }

    provenance = {
        "input_files": [
            file_record("pm10_kamenitsa_daily", pm10_path),
            file_record("pm25_kamenitsa_daily", pm25_path),
            file_record("era5_archive_daily", weather_file),
        ],
        "landed_but_not_used": ["SPO-BG0078A_00005_100.parquet (Trakia PM10, secondary station)"],
        "weather_request": WEATHER_REQUEST,
        "weather_response": _weather_response_meta(weather_file),
        "station_filter": "Validity == 1 and Value >= 0; Value cast with pd.to_numeric",
    }

    split_summary = {
        name: {
            "n_rows": int(len(frame)),
            "first_target_date": frame["target_date"].min().strftime("%Y-%m-%d"),
            "last_target_date": frame["target_date"].max().strftime("%Y-%m-%d"),
            "dropped_incomplete_feature_rows": dropped[name],
        }
        for name, frame in frames.items()
    }

    fits, evaluations = {}, {}
    for name, columns in features.VARIANTS.items():
        fits[name] = fit_variant(frames, columns)
        evaluations[name] = evaluate_variant(name, columns, fits[name], frames)
    baselines = evaluate_baselines(frames)

    primary = features.PRIMARY_VARIANT
    deployable = features.DEPLOYABLE_VARIANT
    primary_columns = features.VARIANTS[primary]

    class_balance = {
        split: {
            "primary_label": metrics.class_balance(frame["label"].to_numpy()),
            "secondary_label_gt50": metrics.class_balance(frame["label_gt50"].to_numpy()),
        }
        for split, frame in frames.items()
    }

    document = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "trained_at": trained_at,
        "station_id": dataset.STATION_ID,
        "label": {"id": dataset.LABEL_ID, "text": dataset.LABEL_TEXT},
        "periods_by_target_date": {k: list(v) for k, v in dataset.SPLITS.items()},
        "primary_model": primary,
        "deployable_model": deployable,
        "primary_model_note": NEXT_DAY_WEATHER_NOTE,
        "day_alignment_note": DAY_ALIGNMENT_NOTE,
        "class_balance": class_balance,
        "rows": split_summary,
        "data_summary": _data_summary(daily, table),
        "label_thresholds_ugm3_by_year_month": _threshold_table(daily["pm10"]),
        "model": evaluations[primary],
        "ablation": {
            "with_next_day_weather": evaluations["with_next_day_weather"],
            "no_next_day_weather": evaluations["no_next_day_weather"],
            "no_weather": evaluations["no_weather"],
            "live": evaluations["live"],
            "note": NEXT_DAY_WEATHER_NOTE,
        },
        "baselines": {"pm10_only_logreg": evaluations["pm10_only"], **baselines},
        "secondary_label_report": {
            "with_next_day_weather": secondary_label_report(
                frames, fits[primary]["pipeline"], primary_columns
            ),
            "no_next_day_weather": secondary_label_report(
                frames, fits[deployable]["pipeline"], features.VARIANTS[deployable]
            ),
            "note": "Models are trained on the primary label; this only reports how their scores rank days above 50 ug/m3.",
        },
        "provenance": provenance,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / METRICS_NAME, document)
    live = features.LIVE_VARIANT
    persisted = (
        (primary, MODEL_NAME),
        (deployable, f"{MODEL_NAME}_{deployable}"),
        (live, f"{MODEL_NAME}_{live}"),
    )
    for name, stem in persisted:
        columns = features.VARIANTS[name]
        threshold = evaluations[name]["threshold"]
        save_model(out_dir / f"{stem}.joblib", name, columns, fits[name], threshold, trained_at)
        write_json(
            out_dir / f"{stem}.json",
            weights_document(name, columns, fits[name], threshold, provenance, split_summary, trained_at),
        )

    predictions = _predictions_frame(frames, fits, evaluations)
    predictions.to_parquet(out_dir / PREDICTIONS_NAME, index=False)
    return document


def _weather_response_meta(path: Path) -> dict:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        payload = json.loads(handle.read())
    return {
        "latitude": payload["latitude"],
        "longitude": payload["longitude"],
        "elevation_m": payload["elevation"],
        "timezone": payload["timezone"],
        "utc_offset_seconds": payload["utc_offset_seconds"],
        "daily_units": payload["daily_units"],
        "n_days": len(payload["daily"]["time"]),
    }


def _data_summary(daily: pd.DataFrame, table: pd.DataFrame) -> dict:
    return {
        "first_day": daily.index.min().strftime("%Y-%m-%d"),
        "last_day": daily.index.max().strftime("%Y-%m-%d"),
        "n_days": int(len(daily)),
        "pm10_valid_days": int(daily["pm10"].notna().sum()),
        "pm25_valid_days": int(daily["pm25"].notna().sum()),
        "weather_days": int(daily["temperature_2m_mean"].notna().sum()),
        "rows_with_label_and_split": int((table["label"].notna() & table["split"].notna()).sum()),
        "feature_transforms": (
            "PM10 and PM2.5 enter as natural logs of the daily mean (ug/m3); precipitation as "
            "log(1 + mm); temperature range is daily max minus min."
        ),
    }


def _threshold_table(pm10: pd.Series) -> dict:
    thresholds = dataset.monthly_thresholds(pm10)
    thresholds = thresholds[thresholds["threshold"].notna()]
    out: dict[str, dict[str, float]] = {}
    for row in thresholds.itertuples(index=False):
        out.setdefault(str(int(row.year)), {})[f"{int(row.month):02d}"] = round(float(row.threshold), 3)
    return out


def _predictions_frame(frames: dict, fits: dict, evaluations: dict) -> pd.DataFrame:
    primary = features.PRIMARY_VARIANT
    deployable = features.DEPLOYABLE_VARIANT
    live = features.LIVE_VARIANT
    parts = []
    for split in ("validation", "test"):
        frame = frames[split]
        parts.append(
            pd.DataFrame(
                {
                    "date": frame["target_date"].to_numpy(dtype="datetime64[ns]"),
                    "asof_date": frame.index.to_numpy(dtype="datetime64[ns]"),
                    "station_id": dataset.STATION_ID,
                    "probability": probabilities(
                        fits[primary]["pipeline"], frame, features.VARIANTS[primary]
                    ),
                    "probability_no_next_day_weather": probabilities(
                        fits[deployable]["pipeline"], frame, features.VARIANTS[deployable]
                    ),
                    "probability_live": probabilities(
                        fits[live]["pipeline"], frame, features.VARIANTS[live]
                    ),
                    "label": frame["label"].to_numpy(dtype="int8"),
                    "label_pm10_gt_50": frame["label_gt50"].to_numpy(dtype="int8"),
                    "pm10_ugm3": frame["pm10_target"].to_numpy(dtype=float),
                    "split": split,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def format_summary(document: dict) -> str:
    rows = [
        ("model: with next-day weather", document["model"]),
        ("ablation: no next-day weather", document["ablation"]["no_next_day_weather"]),
        ("ablation: no weather", document["ablation"]["no_weather"]),
        ("baseline: PM10-only logreg", document["baselines"]["pm10_only_logreg"]),
        ("baseline: monthly climatology", document["baselines"]["monthly_climatology"]),
        ("baseline: persistence", document["baselines"]["persistence"]),
    ]
    lines = [f"{'test rows':34s} {'ROC-AUC':>8s} {'PR-AUC':>7s} {'Brier':>7s} {'Prec':>6s} {'Rec':>6s}"]
    for label, entry in rows:
        t = entry["test"]
        lines.append(
            f"{label:34s} {t['roc_auc']:8.3f} {t['pr_auc']:7.3f} {t['brier']:7.3f} "
            f"{t['precision']:6.3f} {t['recall']:6.3f}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m plovdiv.ml.train",
        description="Train the next-day PM10 risk model for station BG0051A.",
    )
    parser.add_argument("--eea-dir", type=Path, help="landed EEA E1a partition (default: latest)")
    parser.add_argument("--weather-file", type=Path, help="landed ERA5 archive response (default: latest)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="output folder")
    parser.add_argument(
        "--trained-at",
        default=None,
        help="training date written to the files, YYYY-MM-DD (default: today)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trained_at = args.trained_at or date.today().isoformat()
    date.fromisoformat(trained_at)
    document = run(
        eea_dir=args.eea_dir or default_eea_dir(),
        weather_file=args.weather_file or default_weather_file(),
        out_dir=args.out_dir,
        trained_at=trained_at,
    )
    print(format_summary(document))
    print(f"files written to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
