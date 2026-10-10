"""Live PM10 risk models of the second station, Trakia (BG0078A).

    python -m plovdiv.ml.stations --station BG0078A

The models of `python -m plovdiv.ml.train` are trained on Kamenitsa (BG0051A). Trakia is a traffic
station with its own level and its own label thresholds (75th percentile of the same month in the three
previous years of Trakia's own record). This module trains the `live` variant for such a station on its
own E1a history, with the same feature set, splits, regularisation grid and threshold rule as the
Kamenitsa model, and writes `models/pm10_risk_logreg_live_<name>.json` and `.joblib`.

The readable file carries its own evaluation (validation and test metrics, calibration bins, the
persistence and climatology baselines) because `models/pm10_risk_metrics.json` describes Kamenitsa only.
It also records how the Kamenitsa live model scores on the same test rows, which is the reason a
separate model exists: on Trakia 2023-2025 the Kamenitsa model ranks days almost as well but its
probabilities are worse calibrated (higher Brier score).

Trakia has no PM2.5 series and its record starts in 2017, so the training years 2016-2020 hold fewer
rows than Kamenitsa's.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from plovdiv.ml import dataset, features, metrics, train

# Secondary station -> short name used in the model id and file name.
STATION_MODELS = {"BG0078A": "trakia"}
LIVE_STEM = f"{train.MODEL_NAME}_{features.LIVE_VARIANT}"


def live_model_stem(station_id: str) -> str:
    """File stem and model id of the live model scored for a station."""
    if station_id == dataset.STATION_ID:
        return LIVE_STEM
    if station_id not in STATION_MODELS:
        raise KeyError(f"station {station_id} has no live model")
    return f"{LIVE_STEM}_{STATION_MODELS[station_id]}"


def live_stations() -> list[str]:
    """Stations that have a live model, the reference station first."""
    return [dataset.STATION_ID, *sorted(STATION_MODELS)]


def station_table(station_id: str, eea_dir: Path, weather_file: Path) -> pd.DataFrame:
    """Model table of one station from its E1a PM10 record and the ERA5 archive (no PM2.5)."""
    pm10 = dataset.load_eea_daily(Path(eea_dir) / f"SPO-{station_id}_{dataset.PM10_CODE}_100.parquet")
    pm25 = pd.Series(dtype=float, index=pd.DatetimeIndex([], dtype="datetime64[ns]"))
    weather = dataset.load_weather(weather_file)
    return features.build_model_table(dataset.build_daily_table(pm10, pm25, weather))


def _baseline_auc(frame: pd.DataFrame, column: str) -> dict:
    rows = frame[frame[column].notna()]
    y = rows["label"].to_numpy(dtype=int)
    if len(rows) == 0 or len(set(y)) < 2:
        return {"n": int(len(rows)), "roc_auc": None}
    return {"n": int(len(rows)), "roc_auc": float(roc_auc_score(y, rows[column].to_numpy(dtype=float)))}


def train_station(
    station_id: str,
    eea_dir: Path,
    weather_file: Path,
    out_dir: Path,
    trained_at: str,
    reference_document: dict | None = None,
) -> dict:
    """Train, evaluate and write the live model of one secondary station. Returns the readable document."""
    if station_id not in STATION_MODELS:
        raise KeyError(f"station {station_id} is not in STATION_MODELS")
    columns = features.VARIANTS[features.LIVE_VARIANT]
    table = station_table(station_id, eea_dir, weather_file)
    frames, _ = train.split_frames(table, columns)
    for split, frame in frames.items():
        if frame["label"].nunique() < 2:
            raise ValueError(f"{station_id}: split {split} needs both classes, has {len(frame)} rows")

    fit = train.fit_variant(frames, columns)
    evaluation = train.evaluate_variant(features.LIVE_VARIANT, columns, fit, frames)
    threshold = evaluation["threshold"]

    pm10_path = Path(eea_dir) / f"SPO-{station_id}_{dataset.PM10_CODE}_100.parquet"
    provenance = {
        "input_files": [
            train.file_record(f"pm10_{STATION_MODELS[station_id]}_daily", pm10_path),
            train.file_record("era5_archive_daily", Path(weather_file)),
        ],
        "weather_request": train.WEATHER_REQUEST,
        "station_filter": "Validity == 1 and Value >= 0; Value cast with pd.to_numeric",
    }
    split_summary = {
        name: {
            "n_rows": int(len(frame)),
            "first_target_date": frame["target_date"].min().strftime("%Y-%m-%d"),
            "last_target_date": frame["target_date"].max().strftime("%Y-%m-%d"),
        }
        for name, frame in frames.items()
    }
    stem = live_model_stem(station_id)
    document = train.weights_document(
        features.LIVE_VARIANT, columns, fit, threshold, provenance, split_summary, trained_at
    )
    document["model"] = stem
    document["station_id"] = station_id
    document["evaluation"] = {
        "validation": evaluation["validation"],
        "test": evaluation["test"],
        "chosen_C": evaluation["chosen_C"],
        "baselines": {
            "persistence": {"test": _baseline_auc(frames["test"], "bad_today")},
            "monthly_climatology": {"test": _baseline_auc(frames["test"], "climatology_rate")},
        },
    }
    if reference_document is not None:
        x = frames["test"][columns].to_numpy(dtype=float)
        y = frames["test"]["label"].to_numpy(dtype=int)
        p = train.predict_from_weights(reference_document, x)
        ref_threshold = float(reference_document["threshold"]["value"])
        reference = metrics.evaluate(y, p, ref_threshold, with_bootstrap=False)
        document["reference_model_on_this_station"] = {
            "model": reference_document["model"],
            "station_id": reference_document.get("station_id"),
            "test": {k: reference[k] for k in ("n", "roc_auc", "pr_auc", "brier", "precision", "recall")},
            "note": "The reference station's live model scored on this station's test rows, for comparison.",
        }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train.save_model(out_dir / f"{stem}.joblib", features.LIVE_VARIANT, columns, fit, threshold, trained_at)
    train.write_json(out_dir / f"{stem}.json", document)
    return document


def evaluation_rows(document: dict, table: pd.DataFrame, splits: tuple[str, ...] = ("validation", "test")) -> pd.DataFrame:
    """Validation and test predictions of a station model, rebuilt from its weights and the model table.

    Columns: asof_date, date (target), station_id, probability, label, split.
    """
    names = list(document["feature_names"])
    usable = features.usable_rows(table, names)
    rows = table[usable & table["split"].isin(splits)].sort_values("target_date")
    probability = train.predict_from_weights(document, rows[names].to_numpy(dtype=float))
    return pd.DataFrame(
        {
            "asof_date": rows.index.to_numpy(dtype="datetime64[ns]"),
            "date": rows["target_date"].to_numpy(dtype="datetime64[ns]"),
            "station_id": document["station_id"],
            "probability": np.asarray(probability, dtype=float),
            "label": rows["label"].to_numpy(dtype="int8"),
            "split": rows["split"].astype(str).to_numpy(),
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plovdiv.ml.stations", description=__doc__.splitlines()[0])
    parser.add_argument("--station", action="append", choices=sorted(STATION_MODELS), help="default: every station")
    parser.add_argument("--eea-dir", type=Path, default=None)
    parser.add_argument("--weather-file", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=train.DEFAULT_OUT_DIR)
    parser.add_argument("--trained-at", default=None, help="YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    trained_at = args.trained_at or date.today().isoformat()
    date.fromisoformat(trained_at)
    eea_dir = args.eea_dir or train.default_eea_dir()
    weather_file = args.weather_file or train.default_weather_file()
    reference_path = Path(args.out_dir) / f"{LIVE_STEM}.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else None
    for station_id in args.station or sorted(STATION_MODELS):
        doc = train_station(station_id, eea_dir, weather_file, args.out_dir, trained_at, reference)
        test = doc["evaluation"]["test"]
        print(
            f"{doc['model']}: C {doc['evaluation']['chosen_C']} threshold {doc['threshold']['value']:.2f} "
            f"test ROC-AUC {test['roc_auc']:.3f} Brier {test['brier']:.3f} "
            f"precision {test['precision']:.3f} recall {test['recall']:.3f}"
        )
        ref = doc.get("reference_model_on_this_station")
        if ref:
            print(f"  {ref['model']} on the same rows: ROC-AUC {ref['test']['roc_auc']:.3f} Brier {ref['test']['brier']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
