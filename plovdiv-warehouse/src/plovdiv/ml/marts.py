"""Build the model and trend marts as Parquet files that follow the data contract.

    python -m plovdiv.ml.marts

Reads the trained model files in `models/` and the landed station and weather files in
`data/landing/`, builds the optional marts of the contract

    mart_model_card, mart_model_calibration, mart_aq_risk_daily, mart_aq_risk_drivers,
    mart_aq_trend_monthly

validates each one against its Pandera schema in `contracts/schemas.py` and writes
`<out-dir>/<mart>/latest/<mart>.parquet`. A mart that does not pass its schema is not written;
the reason is recorded in `<out-dir>/diagnostics/build_report.json` and printed.

The two required marts of the contract (`mart_weather_aq_hourly`, `mart_aq_daily`) are not built
here. They come from `python -m plovdiv.marts.core` (a direct landing-to-mart path until the bronze
and dbt layers exist), which also writes the authoritative `manifest.json`. This builder writes
only `manifest.partial.json` with the fields it can state; that file is not a valid manifest, is kept
for reference, and is superseded by `manifest.json`.

Output is deterministic: inputs are read in sorted order, all random draws use fixed seeds,
rows are sorted by the grain of each mart, floating point results are rounded, Parquet files are
written with fixed settings and without pandas metadata, and no wall-clock time is recorded.
Running the builder twice on the same inputs writes identical bytes.

Timing convention of `mart_aq_risk_daily.issued_at`: the model uses data of day t and
predicts day t + 1. Each validation and test row is stamped with the end of day t in UTC, which is
00:00 UTC on the target date. This is a convention of the offline evaluation, not the time a live
system ran. Rows with split `live` come from `python -m plovdiv.ml.live` (store
`data/live/pm10_risk_live.parquet`, read with `--live-store`) and carry the real issue time.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts import make_fixtures as contract_types  # noqa: E402
from contracts import schemas  # noqa: E402
from contracts.version import SCHEMA_VERSION  # noqa: E402

from plovdiv.ml import dataset, features, trend  # noqa: E402

LANDING = REPO_ROOT / "data" / "landing"
DEFAULT_MODELS_DIR = REPO_ROOT / "models"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "marts"

PRIMARY_MODEL_FILE = "pm10_risk_logreg.json"
SECOND_MODEL_FILE = "pm10_risk_logreg_no_next_day_weather.json"
LIVE_MODEL_FILE = "pm10_risk_logreg_live.json"
# Live predictions written by `python -m plovdiv.ml.live`: one row per station, target day and model.
DEFAULT_LIVE_STORE = REPO_ROOT / "data" / "live" / "pm10_risk_live.parquet"
# Out-of-sample replay of the days after the test period, written by `python -m plovdiv.ml.replay`.
DEFAULT_REPLAY_STORE = REPO_ROOT / "data" / "live" / "pm10_risk_replay.parquet"
METRICS_FILE = "pm10_risk_metrics.json"
PREDICTIONS_FILE = "pm10_risk_daily_predictions.parquet"

# Probability column of the predictions file for each model variant.
PROBABILITY_COLUMN = {
    "with_next_day_weather": "probability",
    "no_next_day_weather": "probability_no_next_day_weather",
    "live": "probability_live",
}

# Stations of the trend mart: id -> label, type, area. The id and label come from the EEA
# sampling point codes landed in data/landing; the types are those stated by the project owner.
STATIONS = {
    "BG0051A": {"label": "Kamenitsa", "station_type": "background", "area_type": "urban", "location_id": "plovdiv"},
    "BG0078A": {
        "label": "Trakia",
        "station_type": "traffic",
        "area_type": "urban",
        "location_id": "plovdiv",
        # mart_aq_daily keys its rows by location, so Trakia's daily means have a location of their own.
        "daily_location_id": "plovdiv-trakia",
    },
}
TREND_YEARS = {"BG0051A": (2013, 2025), "BG0078A": (2017, 2025)}

# Drivers are published for the splits the contract names; see contracts/README.md.
DRIVER_SPLITS = ("test",)
DRIVER_VARIANT = "with_next_day_weather"
DRIVERS_PUBLISHED = 5
CONTRIBUTION_DECIMALS = 8
CALIBRATION_DECIMALS = 10
PROBABILITY_TOLERANCE = 1e-9

ATTRIBUTION = [
    "Weather: Open-Meteo (https://open-meteo.com/), CC BY 4.0, ERA5 reanalysis (Copernicus Climate Change Service, ECMWF)",
    "Air quality stations: European Environment Agency, air quality data reported by Bulgaria",
]

MART_ORDER = [
    "mart_model_card",
    "mart_model_calibration",
    "mart_aq_risk_daily",
    "mart_aq_risk_drivers",
    "mart_aq_trend_monthly",
]
REQUIRED_MARTS = ["mart_weather_aq_hourly", "mart_aq_daily"]
PARTIAL_REASON = (
    "mart_weather_aq_hourly and mart_aq_daily are required by manifest.schema.json and are not built by "
    "plovdiv.ml.marts, so this file lists only the marts it builds and is not a valid manifest. The "
    "authoritative manifest is manifest.json, written by python -m plovdiv.marts.core; this file is kept "
    "for reference and consumers must not read it."
)

COMMON_LIMITATIONS = (
    "The label is relative: a bad day is a day above the 75th percentile of the same calendar month "
    "in the three previous years, so about a quarter of days are positive by construction and the "
    "meaning of a bad day moves as concentrations fall. "
    "The model covers one station, Kamenitsa (BG0051A). "
    "PM2.5 is missing for most of 2016, so training rows effectively start in 2017. "
    "The test period is 2023 to 2025 and was not used to tune anything. "
    "Weather is ERA5 reanalysis on a grid of about 25 km, not a station measurement. "
    "Class weighting makes the probabilities larger than the observed frequencies in the middle "
    "range, so read the calibration table before reading a probability as a frequency. "
    "The station day and the weather day can differ by an hour."
)
LIMITATIONS = {
    "with_next_day_weather": (
        "The scores are an upper bound. The inputs include the weather of the target day taken from ERA5 "
        "actuals, as a stand-in for a weather forecast; a real forecast is imperfect, so a live system "
        "would score lower. " + COMMON_LIMITATIONS
    ),
    "no_next_day_weather": (
        "This variant uses only information known at the end of the day before the target day: PM10 and "
        "PM2.5 history, the calendar and the weather of the day just ended. It does not know the weather "
        "of the target day, which is why its scores are lower than those of the variant that does. "
        + COMMON_LIMITATIONS
    ),
    "live": (
        "This variant is the one scored for live rows. It uses PM10 history, the calendar and the weather of "
        "the day just ended, without PM2.5 (not landed hourly for 2026) and without solar radiation. Live "
        "rows use unverified hourly station data (EEA up-to-date files, daily mean from at least 18 valid "
        "hours) and weather aggregated from Open-Meteo hourly model values, while training used verified "
        "daily means and ERA5 reanalysis, so live inputs differ somewhat from the training inputs. "
        + COMMON_LIMITATIONS
    ),
}
STATION_LIMITATIONS = (
    "This is the live variant trained on the station's own record: PM10 history, the calendar and the weather of the "
    "day just ended, without PM2.5 and without solar radiation. The station's record starts in 2017, so it has fewer "
    "training days than Kamenitsa. Live rows use unverified hourly station data (EEA up-to-date files, daily mean "
    "from at least 18 valid hours) and weather aggregated from Open-Meteo hourly model values, while training used "
    "verified daily means and ERA5 reanalysis. The label is relative: a bad day is a day above the 75th percentile of "
    "the same calendar month in the station's three previous years, so the threshold is the station's own and moves as "
    "concentrations fall. The test period is 2023 to 2025 and was not used to tune anything. Class weighting makes "
    "the probabilities larger than the observed frequencies in the middle range."
)
ALGORITHM = "Logistic regression, L2 penalty, standardised inputs, balanced class weights"
# Live models of the secondary stations, written by `python -m plovdiv.ml.stations`.
STATION_MODEL_GLOB = "pm10_risk_logreg_live_*.json"


def is_station_model(document: dict) -> bool:
    """A model trained by plovdiv.ml.stations: it carries its own evaluation."""
    return "evaluation" in document


# ---------------------------------------------------------------------------------------
# Input files
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


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------
# Model marts
# ---------------------------------------------------------------------------------------


def _utc_midnight(day: str) -> pd.Timestamp:
    return pd.Timestamp(day, tz="UTC")


def _date(value) -> datetime.date:
    return pd.Timestamp(value).date()


def build_model_card(metrics: dict, documents: list[dict]) -> pd.DataFrame:
    """One row per model from the metrics file and the readable model files."""
    rows = []
    for doc in documents:
        variant = doc["variant"]
        if is_station_model(doc):
            test = doc["evaluation"]["test"]
            baselines = doc["evaluation"]["baselines"]
            limitations = STATION_LIMITATIONS
        else:
            block = metrics["ablation"][variant]
            if block["features"] != doc["feature_names"]:
                raise ValueError(f"{variant}: feature lists of the metrics and the model file differ")
            if abs(block["threshold"] - doc["threshold"]["value"]) > 1e-12:
                raise ValueError(f"{variant}: threshold of the metrics and the model file differ")
            test = block["test"]
            baselines = metrics["baselines"]
            limitations = LIMITATIONS[variant]
        periods = doc["periods_by_target_date"]
        rows.append(
            {
                "model_id": doc["model"],
                "algorithm": ALGORITHM,
                "trained_at": _utc_midnight(doc["trained_at"]),
                "train_start": _date(periods["train"][0]),
                "train_end": _date(periods["train"][1]),
                "validation_start": _date(periods["validation"][0]),
                "validation_end": _date(periods["validation"][1]),
                "test_start": _date(periods["test"][0]),
                "test_end": _date(periods["test"][1]),
                "label_id": doc["label"]["id"],
                "label_text": doc["label"]["text"],
                "features": list(doc["feature_names"]),
                "roc_auc_test": float(test["roc_auc"]),
                "pr_auc_test": float(test["pr_auc"]),
                "brier_test": float(test["brier"]),
                "baseline_roc_auc_test_climatology": float(baselines["monthly_climatology"]["test"]["roc_auc"]),
                "baseline_roc_auc_test_persistence": float(baselines["persistence"]["test"]["roc_auc"]),
                "threshold": float(doc["threshold"]["value"]),
                "precision_test": float(test["precision"]),
                "recall_test": float(test["recall"]),
                "uses_forecast_weather": bool(doc["uses_next_day_weather"]),
                "limitations": limitations,
            }
        )
    return pd.DataFrame(rows).sort_values("model_id").reset_index(drop=True)


def build_calibration(metrics: dict, documents: list[dict]) -> pd.DataFrame:
    """Reliability bins on the test split for each model, from the metrics file."""
    rows = []
    for doc in documents:
        if is_station_model(doc):
            bins = doc["evaluation"]["test"]["calibration_bins"]
        else:
            bins = metrics["ablation"][doc["variant"]]["test"]["calibration_bins"]
        for b in bins:
            n = int(b["count"])
            rows.append(
                {
                    "model_id": doc["model"],
                    "bin_lo": round(float(b["lower"]), CALIBRATION_DECIMALS),
                    "bin_hi": round(float(b["upper"]), CALIBRATION_DECIMALS),
                    "predicted_mean": float(b["predicted_mean"]) if n else None,
                    "observed_rate": float(b["observed_rate"]) if n else None,
                    "n": n,
                }
            )
    return pd.DataFrame(rows).sort_values(["model_id", "bin_lo"]).reset_index(drop=True)


def build_risk_daily(predictions: pd.DataFrame, documents: list[dict]) -> pd.DataFrame:
    """One row per model and target day for the validation and test splits.

    Training rows are in-sample and are not published. Live rows are added separately from the
    live store (see `live_risk_rows`).
    """
    pred = predictions.copy()
    if not (pd.to_datetime(pred["asof_date"]) + pd.Timedelta(days=1) == pd.to_datetime(pred["date"])).all():
        raise ValueError("predictions: target date is not the day after the as-of date")
    unexpected = set(pred["split"]) - {"validation", "test"}
    if unexpected:
        raise ValueError(f"predictions: unexpected splits {sorted(unexpected)}")
    parts = []
    for doc in documents:
        column = PROBABILITY_COLUMN[doc["variant"]]
        target = pd.to_datetime(pred["date"])
        parts.append(
            pd.DataFrame(
                {
                    "station_id": pred["station_id"].astype(str).to_numpy(),
                    "target_date": [d.date() for d in target],
                    # End of day t in UTC is 00:00 UTC of the target date.
                    "issued_at": pd.DatetimeIndex(target).tz_localize("UTC").astype("datetime64[us, UTC]"),
                    "probability": pred[column].astype(float).to_numpy(),
                    "label_id": doc["label"]["id"],
                    "split": pred["split"].astype(str).to_numpy(),
                    "actual_label": pred["label"].astype("int64").to_numpy(),
                    "model_id": doc["model"],
                }
            )
        )
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values(["station_id", "target_date", "model_id"]).reset_index(drop=True)


LIVE_SPLIT = "live"


def live_risk_rows(store: pd.DataFrame, split: str = LIVE_SPLIT) -> pd.DataFrame:
    """Rows of mart_aq_risk_daily from the live store written by `python -m plovdiv.ml.live`.

    `issued_at` is the time the live run scored the row, not the end-of-day convention of the
    evaluation rows. The as-of day (target_date - 1) and the inputs stay in the store. The replay
    store of `python -m plovdiv.ml.replay` has the same layout and is read with split `replay`.
    """
    columns = ["station_id", "target_date", "issued_at", "probability", "label_id", "split", "actual_label", "model_id"]
    if store is None or not len(store):
        return pd.DataFrame(columns=columns)
    if set(store["split"]) != {split}:
        raise ValueError(f"{split} store: unexpected splits {sorted(set(store['split']))}")
    target = pd.to_datetime(store["target_date"])
    asof = pd.to_datetime(store["asof_date"])
    if not (asof + pd.Timedelta(days=1) == target).all():
        raise ValueError("live store: target date is not the day after the as-of date")
    return pd.DataFrame(
        {
            "station_id": store["station_id"].astype(str).to_numpy(),
            "target_date": [d.date() for d in target],
            "issued_at": pd.to_datetime(store["issued_at"], utc=True).astype("datetime64[us, UTC]").to_numpy(),
            "probability": store["probability"].astype(float).to_numpy(),
            "label_id": store["label_id"].astype(str).to_numpy(),
            "split": split,
            "actual_label": pd.array(store["actual_label"], dtype="Int64"),
            "model_id": store["model_id"].astype(str).to_numpy(),
        }
    )


def live_driver_rows(store: pd.DataFrame, documents: dict[str, dict], top_k: int = DRIVERS_PUBLISHED) -> pd.DataFrame:
    """Top contributions of each live row, from the feature values kept in the store.

    `documents` maps model id to the readable model file. The stored probability must be
    reproduced by the weights, otherwise the store and the model file disagree and this raises.
    """
    columns = ["station_id", "target_date", "model_id", "feature", "contribution", "rank"]
    if store is None or not len(store):
        return pd.DataFrame(columns=columns)
    rows = []
    for record in store.itertuples(index=False):
        document = documents[record.model_id]
        names = list(document["feature_names"])
        values = json.loads(record.features_json)
        x = np.array([[values[n] for n in names]], dtype=float)
        rebuilt = float(reconstruct_probability(document, x)[0])
        if abs(rebuilt - float(record.probability)) > PROBABILITY_TOLERANCE:
            raise ValueError(f"live store: {record.model_id} {record.target_date} does not reproduce its probability")
        contrib = contributions(document, x)[0]
        order = np.argsort(-np.abs(contrib), kind="stable")[:top_k]
        for rank, j in enumerate(order, start=1):
            rows.append(
                {
                    "station_id": str(record.station_id),
                    "target_date": pd.Timestamp(record.target_date).date(),
                    "model_id": record.model_id,
                    "feature": names[j],
                    "contribution": round(float(contrib[j]), CONTRIBUTION_DECIMALS),
                    "rank": rank,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def contributions(document: dict, x: np.ndarray) -> np.ndarray:
    """Contribution of each feature to the log-odds: coefficient times standardised value.

    The intercept plus the row sum of the returned matrix is the logit of the model.
    """
    x = np.asarray(x, dtype=float)
    z = (x - np.asarray(document["scaler_mean"])) / np.asarray(document["scaler_scale"])
    return z * np.asarray(document["coefficients_standardised"])


def reconstruct_probability(document: dict, x: np.ndarray) -> np.ndarray:
    logit = document["intercept_standardised"] + contributions(document, x).sum(axis=1)
    return 1.0 / (1.0 + np.exp(-logit))


def build_risk_drivers(
    predictions: pd.DataFrame,
    feature_values: pd.DataFrame,
    document: dict,
    splits: tuple[str, ...] = DRIVER_SPLITS,
    top_k: int = DRIVERS_PUBLISHED,
) -> pd.DataFrame:
    """Top contributions per prediction for the given splits, ranked by absolute value.

    `feature_values` is indexed by as-of day and has one column per model feature.
    """
    names = list(document["feature_names"])
    chosen = predictions[predictions["split"].isin(splits)].sort_values("date")
    x = feature_values.loc[pd.DatetimeIndex(chosen["asof_date"]), names].to_numpy(dtype=float)
    if np.isnan(x).any():
        raise ValueError("missing feature values for rows of the risk mart")
    contrib = contributions(document, x)
    rows = []
    for row, target in enumerate(pd.to_datetime(chosen["date"])):
        order = np.argsort(-np.abs(contrib[row]), kind="stable")[:top_k]
        for rank, j in enumerate(order, start=1):
            rows.append(
                {
                    "station_id": str(chosen["station_id"].iloc[row]),
                    "target_date": target.date(),
                    "model_id": document["model"],
                    "feature": names[j],
                    "contribution": round(float(contrib[row, j]), CONTRIBUTION_DECIMALS),
                    "rank": rank,
                }
            )
    out = pd.DataFrame(rows)
    return out.sort_values(["station_id", "target_date", "model_id", "rank"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------
# Trend mart
# ---------------------------------------------------------------------------------------


def build_trend(station_id: str, result: trend.TrendResult, pollutant: str = "pm10") -> pd.DataFrame:
    m = result.monthly
    return pd.DataFrame(
        {
            "station_id": station_id,
            "month": [d.date() for d in pd.DatetimeIndex(m["month"])],
            "observed_mean_ugm3": m["observed_mean"].to_numpy(dtype=float),
            "weather_adjusted_mean_ugm3": m["adjusted_mean"].to_numpy(dtype=float),
            "adjusted_lo_ugm3": m["adjusted_lo"].to_numpy(dtype=float),
            "adjusted_hi_ugm3": m["adjusted_hi"].to_numpy(dtype=float),
            "n_days": m["n_days"].to_numpy(dtype="int64"),
            "pollutant": pollutant,
        }
    )


# ---------------------------------------------------------------------------------------
# Validation and writing
# ---------------------------------------------------------------------------------------

ARROW_SCHEMAS = {
    "mart_model_card": contract_types.MODEL_CARD_PYARROW_SCHEMA,
    "mart_model_calibration": contract_types.CALIBRATION_PYARROW_SCHEMA,
    "mart_aq_risk_daily": contract_types.RISK_PYARROW_SCHEMA,
    "mart_aq_risk_drivers": contract_types.DRIVERS_PYARROW_SCHEMA,
    "mart_aq_trend_monthly": contract_types.TREND_PYARROW_SCHEMA,
}


def to_table(name: str, df: pd.DataFrame) -> pa.Table:
    """Arrow table with the column types of the contract fixtures and no pandas metadata."""
    schema = ARROW_SCHEMAS[name]
    table = pa.Table.from_pandas(df[schema.names], schema=schema, preserve_index=False)
    return table.replace_schema_metadata(None)


def validate(name: str, table: pa.Table) -> list[str]:
    """Run the contract schema on what a reader of the file would see. Returns problems."""
    import pandera.errors

    frame = table.to_pandas()
    try:
        schemas.OPTIONAL_MART_SCHEMAS[name].validate(frame, lazy=True)
    except pandera.errors.SchemaErrors as exc:
        cases = exc.failure_cases
        problems = sorted({f"{row.check}" for row in cases.itertuples()})
        return problems or [str(exc)]
    except pandera.errors.SchemaError as exc:
        return [str(exc)]
    return []


def write_parquet(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table.replace_schema_metadata(None),
        path,
        compression="snappy",
        use_dictionary=True,
        row_group_size=65536,
        write_statistics=True,
    )


def mart_path(out_dir: Path, name: str) -> Path:
    return Path(out_dir) / name / "latest" / f"{name}.parquet"


def manifest_entry(name: str, df: pd.DataFrame) -> dict:
    entry = {"glob": f"{name}/latest/*.parquet", "rows": int(len(df))}
    if name == "mart_aq_trend_monthly":
        months = pd.to_datetime(df["month"])
        entry["min_month"] = months.min().strftime("%Y-%m-%d")
        entry["max_month"] = months.max().strftime("%Y-%m-%d")
    if name == "mart_aq_risk_daily":
        days = pd.to_datetime(df["target_date"])
        entry["min_target_date"] = days.min().strftime("%Y-%m-%d")
        entry["max_target_date"] = days.max().strftime("%Y-%m-%d")
    return entry


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")


# ---------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------


def model_feature_table(eea_dir: Path, weather_file: Path) -> pd.DataFrame:
    """Feature table of the risk model, rebuilt from the landed files (indexed by as-of day)."""
    pm10 = dataset.load_eea_daily(eea_dir / f"SPO-{dataset.STATION_ID}_{dataset.PM10_CODE}_100.parquet")
    pm25 = dataset.load_eea_daily(eea_dir / f"SPO-{dataset.STATION_ID}_{dataset.PM25_CODE}_100.parquet")
    weather = dataset.load_weather(weather_file)
    return features.build_model_table(dataset.build_daily_table(pm10, pm25, weather))


def check_model_inputs(
    predictions: pd.DataFrame, table: pd.DataFrame, documents: list[dict]
) -> dict:
    """Confirm the landed files reproduce the stored predictions and labels.

    Returns the largest probability difference per model. Raises when the weights, the rebuilt
    features and the predictions file do not agree.
    """
    asof = pd.DatetimeIndex(predictions["asof_date"])
    report = {}
    labels = table.loc[asof, "label"].to_numpy()
    if not np.array_equal(labels, predictions["label"].to_numpy(dtype=float)):
        raise ValueError("labels rebuilt from the landed files differ from the predictions file")
    for doc in documents:
        x = table.loc[asof, doc["feature_names"]].to_numpy(dtype=float)
        if np.isnan(x).any():
            raise ValueError(f"{doc['variant']}: missing features for rows of the predictions file")
        rebuilt = reconstruct_probability(doc, x)
        stored = predictions[PROBABILITY_COLUMN[doc["variant"]]].to_numpy(dtype=float)
        gap = float(np.max(np.abs(rebuilt - stored)))
        if gap > PROBABILITY_TOLERANCE:
            raise ValueError(f"{doc['variant']}: rebuilt probabilities differ by {gap:.3e}")
        report[doc["variant"]] = gap
    return report


def model_documents(models_dir: Path) -> list[dict]:
    """Readable model files: the primary and the second model, plus the live model when it exists."""
    documents = [read_json(models_dir / PRIMARY_MODEL_FILE), read_json(models_dir / SECOND_MODEL_FILE)]
    if (models_dir / LIVE_MODEL_FILE).exists():
        documents.append(read_json(models_dir / LIVE_MODEL_FILE))
    for path in sorted(Path(models_dir).glob(STATION_MODEL_GLOB)):
        document = read_json(path)
        if not is_station_model(document):
            raise ValueError(f"{path.name}: a station model file must carry its own evaluation")
        if document["station_id"] not in STATIONS:
            raise ValueError(f"{path.name}: station {document['station_id']} is not in STATIONS")
        documents.append(document)
    return documents


def station_risk_rows(document: dict, eea_dir: Path, weather_file: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validation and test rows of a station model for mart_aq_risk_daily, and its test drivers.

    The probabilities are rebuilt from the readable weights and the landed files, so the mart always
    matches the model file. Issue time follows the evaluation convention (end of day t).
    """
    from plovdiv.ml import stations  # local import: stations imports train, which is heavier

    station_id = document["station_id"]
    table = stations.station_table(station_id, eea_dir, weather_file)
    rows = stations.evaluation_rows(document, table)
    target = pd.to_datetime(rows["date"])
    risk = pd.DataFrame(
        {
            "station_id": station_id,
            "target_date": [d.date() for d in target],
            "issued_at": pd.DatetimeIndex(target).tz_localize("UTC").astype("datetime64[us, UTC]"),
            "probability": rows["probability"].astype(float).to_numpy(),
            "label_id": document["label"]["id"],
            "split": rows["split"].to_numpy(),
            "actual_label": rows["label"].astype("int64").to_numpy(),
            "model_id": document["model"],
        }
    )
    features_by_asof = table[list(document["feature_names"])]
    drivers = build_risk_drivers(rows, features_by_asof, document)
    return risk, drivers


def build_all(
    models_dir: Path,
    eea_dir: Path,
    weather_file: Path,
    n_replicates: int = trend.N_REPLICATES,
    live_store: Path | None = None,
    replay_store: Path | None = None,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """Build every mart in memory. Returns the frames and the diagnostics.

    `live_store` is the parquet file of `python -m plovdiv.ml.live`; its rows are added to the risk
    and drivers marts as split `live`. A missing file means no live rows.
    """
    metrics = read_json(models_dir / METRICS_FILE)
    documents = model_documents(models_dir)
    reference_documents = [d for d in documents if not is_station_model(d)]
    station_documents = [d for d in documents if is_station_model(d)]
    predictions = pd.read_parquet(models_dir / PREDICTIONS_FILE)

    table = model_feature_table(eea_dir, weather_file)
    input_check = check_model_inputs(predictions, table, reference_documents)
    primary = next(d for d in documents if d["variant"] == DRIVER_VARIANT)
    station_parts = [station_risk_rows(d, eea_dir, weather_file) for d in station_documents]

    store = pd.read_parquet(live_store) if live_store is not None and Path(live_store).exists() else None
    replay = pd.read_parquet(replay_store) if replay_store is not None and Path(replay_store).exists() else None
    by_id = {d["model"]: d for d in documents}
    for name, frame in (("live", store), ("replay", replay)):
        if frame is not None and len(frame):
            unknown = sorted(set(frame["model_id"]) - set(by_id))
            if unknown:
                raise ValueError(f"{name} store: models {unknown} have no model file in {models_dir}")
    risk = pd.concat(
        [
            build_risk_daily(predictions, reference_documents),
            *[p[0] for p in station_parts],
            live_risk_rows(replay, split="replay"),
            live_risk_rows(store),
        ],
        ignore_index=True,
    )
    drivers = pd.concat(
        [
            build_risk_drivers(predictions, table, primary),
            *[p[1] for p in station_parts],
            live_driver_rows(store, by_id),
        ],
        ignore_index=True,
    )

    frames = {
        "mart_model_card": build_model_card(metrics, documents),
        "mart_model_calibration": build_calibration(metrics, documents),
        "mart_aq_risk_daily": risk.sort_values(["station_id", "target_date", "model_id"]).reset_index(drop=True),
        "mart_aq_risk_drivers": drivers.sort_values(["station_id", "target_date", "model_id", "rank"]).reset_index(
            drop=True
        ),
    }

    weather = features.weather_features(dataset.load_weather(weather_file))
    trend_parts = []
    trend_diagnostics = {}
    for station_id, years in TREND_YEARS.items():
        series = dataset.load_eea_daily(eea_dir / f"SPO-{station_id}_{dataset.PM10_CODE}_100.parquet")
        result = trend.weather_adjusted_monthly(series, weather, n_replicates=n_replicates)
        trend_parts.append(build_trend(station_id, result))
        summary = trend.yearly_summary(result, *years)
        trend_diagnostics[station_id] = {
            "model": result.model,
            "bounds_moved_to_estimate": result.n_clipped_to_estimate,
            "change": summary.get("change"),
            "yearly": json.loads(summary["years"].round(4).to_json(orient="records")),
        }
    trend_frame = pd.concat(trend_parts, ignore_index=True)
    frames["mart_aq_trend_monthly"] = trend_frame.sort_values(
        ["station_id", "pollutant", "month"]
    ).reset_index(drop=True)

    diagnostics = {
        "input_check_max_abs_probability_difference": input_check,
        "trend": trend_diagnostics,
        "inputs": {
            "eea_dir": eea_dir.name,
            "weather_file": weather_file.name,
            "sha256": {
                p.name: sha256_of(p)
                for p in sorted(
                    [models_dir / f for f in (PRIMARY_MODEL_FILE, SECOND_MODEL_FILE, METRICS_FILE, PREDICTIONS_FILE)]
                    + [models_dir / f"{d['model']}.json" for d in station_documents]
                    + [eea_dir / f"SPO-{s}_{dataset.PM10_CODE}_100.parquet" for s in TREND_YEARS]
                    + [eea_dir / f"SPO-{dataset.STATION_ID}_{dataset.PM25_CODE}_100.parquet", weather_file]
                )
            },
        },
    }
    return frames, diagnostics


def partition_date(path: Path) -> str:
    """Fetch date of a landing partition such as `fetch_date=2026-10-07`."""
    return path.name.split("=", 1)[1]


def write_marts(
    frames: dict[str, pd.DataFrame], out_dir: Path, diagnostics: dict, generated_at: str, run_id: str
) -> dict:
    """Validate and write each mart, then the partial manifest and the build report."""
    out_dir = Path(out_dir)
    written: dict[str, pd.DataFrame] = {}
    blocked: dict[str, list[str]] = {}
    for name in MART_ORDER:
        table = to_table(name, frames[name])
        problems = validate(name, table)
        if problems:
            blocked[name] = problems
            continue
        write_parquet(table, mart_path(out_dir, name))
        written[name] = frames[name]

    manifest = {
        "partial": True,
        "partial_reason": PARTIAL_REASON,
        "missing_required_marts": list(REQUIRED_MARTS),
        "blocked_marts": sorted(blocked),
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "attribution": ATTRIBUTION,
        "stations": STATIONS,
        "marts": {name: manifest_entry(name, written[name]) for name in MART_ORDER if name in written},
    }
    write_json(out_dir / "manifest.partial.json", manifest)

    report = {
        "marts_written": {name: int(len(df)) for name, df in written.items()},
        "marts_blocked": blocked,
        **diagnostics,
    }
    write_json(out_dir / "diagnostics" / "build_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the model and trend marts of the data contract.")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--eea-dir", type=Path, default=None)
    parser.add_argument("--weather-file", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--replicates", type=int, default=trend.N_REPLICATES, help="bootstrap replicates")
    parser.add_argument(
        "--generated-at",
        default=None,
        help="generated_at_utc of the manifest; default is the fetch date of the weather input at 00:00 UTC",
    )
    parser.add_argument("--strict", action="store_true", help="exit with status 1 when a mart is blocked")
    parser.add_argument(
        "--live-store",
        type=Path,
        default=DEFAULT_LIVE_STORE,
        help="live predictions of python -m plovdiv.ml.live, added as split live when the file exists",
    )
    parser.add_argument(
        "--replay-store",
        type=Path,
        default=DEFAULT_REPLAY_STORE,
        help="replayed predictions of python -m plovdiv.ml.replay, added as split replay when the file exists",
    )
    args = parser.parse_args(argv)

    eea_dir = args.eea_dir or default_eea_dir()
    weather_file = args.weather_file or default_weather_file()
    frames, diagnostics = build_all(
        args.models_dir, eea_dir, weather_file, args.replicates, live_store=args.live_store, replay_store=args.replay_store
    )

    generated_at = args.generated_at or f"{partition_date(weather_file.parent)}T00:00:00Z"
    digest = hashlib.sha256(json.dumps(diagnostics["inputs"]["sha256"], sort_keys=True).encode()).hexdigest()
    run_id = f"marts-{digest[:12]}"
    report = write_marts(frames, args.out_dir, diagnostics, generated_at, run_id)

    for name, rows in report["marts_written"].items():
        print(f"wrote {name}: {rows} rows")
    for name, problems in report["marts_blocked"].items():
        print(f"BLOCKED {name}: not written, fails the contract schema:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
    print(f"manifest: {args.out_dir / 'manifest.partial.json'}")
    return 1 if (args.strict and report["marts_blocked"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
