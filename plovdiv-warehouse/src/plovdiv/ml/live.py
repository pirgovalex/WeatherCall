"""Score the `live` PM10 risk model for the day after the newest complete station day.

    python -m plovdiv.ml.live

Inputs (all read-only):

* EEA up-to-date (UTD) hourly PM10 for Kamenitsa, `source=eea/endpoint=utd_hourly/fetch_date=<latest>`.
  Hours with Validity other than 1, or a value that is not a non-negative number, are excluded.
  A day has a daily mean when at least 18 of its hours are valid, the same 18-of-24 rule as
  `mart_aq_daily.is_valid_day`. The day of an hour is the date of its `Start` (the EEA day, UTC+1),
  as in `mart_aq_daily` and the E1a loader of the training data.
* EEA E1a daily means (`endpoint=e1a_daily`), the verified history used for training. They are
  joined with the UTD daily means (E1a wins where both exist) so that the rolling features and the
  label threshold are computed by `plovdiv.ml.features.build_model_table`, unchanged.
* Open-Meteo forecast captures (`source=openmeteo/endpoint=forecast`). The hourly values of day t
  are aggregated to the daily definitions of the ERA5 archive request used in training (Europe/Sofia
  local day, mean, max, min, sum, max of the hourly values); the most recent capture that covers
  all 24 hours of day t is used.
* The readable weights of the `live` model, `models/pm10_risk_logreg_live.json`.

The as-of day t is the newest EEA day whose hours have all been reported (the landed file reaches
past its end). The target day is t + 1. Nothing is scored when an input is missing, incomplete or
stale; the reason is printed, written to the run report and the exit status is 2.

A scored row is upserted into the live store (`data/live/pm10_risk_live.parquet`, one row per
station, target day and model; a later issue replaces an earlier one), then the `live` rows of
`mart_aq_risk_daily` and `mart_aq_risk_drivers` are rewritten from the store, and the counts and
ranges of those marts in `manifest.json` are refreshed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from plovdiv.ml import dataset, features, marts

LANDING = marts.LANDING
DEFAULT_MODELS_DIR = marts.DEFAULT_MODELS_DIR
DEFAULT_MARTS_DIR = marts.DEFAULT_OUT_DIR
DEFAULT_STORE = marts.DEFAULT_LIVE_STORE
DEFAULT_REPORT = DEFAULT_STORE.parent / "last_run.json"

MIN_VALID_HOURS = 18  # same rule as mart_aq_daily.is_valid_day
LOCAL_TZ = "Europe/Sofia"  # day definition of the ERA5 archive request used in training
EEA_UTC_OFFSET = pd.Timedelta(hours=1)  # EEA day is UTC+1, fixed
SPLIT = "live"

# Hourly capture variable -> unit expected after conversion.
WEATHER_HOURLY = {
    "temperature_2m": "°C",
    "precipitation": "mm",
    "wind_speed_10m": "m/s",
    "relative_humidity_2m": "%",
    "surface_pressure": "hPa",
}
UNIT_CONVERSIONS = {("wind_speed_10m", "km/h"): 1 / 3.6}


class LiveInputError(RuntimeError):
    """An input needed for the live row is missing, incomplete or stale. No row is written."""


# ---------------------------------------------------------------------------------------
# Station data: EEA UTD hourly -> daily means
# ---------------------------------------------------------------------------------------


def load_utd_hourly(path: str | Path) -> pd.DataFrame:
    """Hourly records of a landed UTD file: start, end, value, valid flag and verification code."""
    frame = pd.read_parquet(path)
    if set(frame["AggType"].unique()) - {"hour"}:
        raise ValueError(f"{path}: expected hourly records only")
    units = set(frame["Unit"].unique())
    if units - {"ug.m-3"}:
        raise ValueError(f"{path}: unexpected units {sorted(units)}")
    value = pd.to_numeric(frame["Value"], errors="coerce").astype(float)
    out = pd.DataFrame(
        {
            "start": pd.to_datetime(frame["Start"]).astype("datetime64[ns]"),
            "end": pd.to_datetime(frame["End"]).astype("datetime64[ns]"),
            "value": value,
            "valid": (frame["Validity"] == 1) & value.notna() & (value >= 0),
            "verification": frame["Verification"].astype(int),
        }
    ).sort_values("start", kind="stable")
    if out["start"].duplicated().any():
        raise ValueError(f"{path}: more than one record for the same hour")
    return out.reset_index(drop=True)


def _verification_status(codes: pd.Series) -> str:
    """EEA codes: 1 verified, 2 preliminary verified, 3 not verified. The weakest hour decides."""
    if codes.empty:
        return "none"
    worst = int(codes.max())
    return {1: "verified", 2: "preliminary"}.get(worst, "unverified")


def utd_daily_means(hourly: pd.DataFrame, min_valid_hours: int = MIN_VALID_HOURS) -> pd.DataFrame:
    """Daily mean per EEA day (date of `start`) with its completeness and verification.

    Columns: pm10 (mean of the valid hours, NaN unless at least `min_valid_hours` are valid),
    valid_hours, unverified_hours (valid hours with Verification 3) and verification (status of the
    valid hours: verified, preliminary, unverified or none).
    """
    data = hourly.assign(day=hourly["start"].dt.normalize())
    valid = data[data["valid"]]
    grouped = valid.groupby("day")
    days = pd.DatetimeIndex(sorted(data["day"].unique())).astype("datetime64[ns]")
    out = pd.DataFrame(index=days)
    out.index.name = "day"
    out["valid_hours"] = grouped.size().reindex(days, fill_value=0).astype(int)
    mean = grouped["value"].mean().reindex(days)
    out["pm10"] = mean.where(out["valid_hours"] >= min_valid_hours)
    out["unverified_hours"] = (
        valid[valid["verification"] == 3].groupby("day").size().reindex(days, fill_value=0).astype(int)
    )
    out["verification"] = [
        _verification_status(valid.loc[valid["day"] == d, "verification"]) for d in days
    ]
    return out


def latest_closed_day(hourly: pd.DataFrame) -> pd.Timestamp:
    """Newest EEA day whose last hour lies inside the landed file (its end is reached)."""
    last_end = hourly["end"].max()
    return (last_end - pd.Timedelta(days=1)).normalize()


# ---------------------------------------------------------------------------------------
# Weather: Open-Meteo hourly forecast captures -> ERA5-style daily values
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Capture:
    """One landed Open-Meteo forecast response, hourly values in UTC with converted units."""

    path: Path
    fetched_at: pd.Timestamp
    hourly: pd.DataFrame = field(repr=False)
    latitude: float = float("nan")
    longitude: float = float("nan")


def load_capture(path: str | Path) -> Capture:
    """Read a landed forecast capture. Times must be GMT; units are converted to the ERA5 units."""
    path = Path(path)
    with gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb") as handle:
        payload = json.loads(handle.read())
    if payload.get("utc_offset_seconds", 0) != 0:
        raise ValueError(f"{path}: hourly times are not UTC")
    fetched = (payload.get("_audit") or {}).get("fetched_at_utc")
    if not fetched:
        raise ValueError(f"{path}: no _audit.fetched_at_utc")
    hourly = payload["hourly"]
    units = payload.get("hourly_units", {})
    frame = pd.DataFrame(index=pd.DatetimeIndex(pd.to_datetime(hourly["time"])).tz_localize("UTC"))
    for name, unit in WEATHER_HOURLY.items():
        if name not in hourly:
            raise ValueError(f"{path}: no hourly {name}")
        values = pd.to_numeric(pd.Series(hourly[name], index=frame.index), errors="coerce").astype(float)
        got = units.get(name)
        if got != unit:
            factor = UNIT_CONVERSIONS.get((name, got))
            if factor is None:
                raise ValueError(f"{path}: unit {got!r} of {name}, expected {unit!r}")
            values = values * factor
        frame[name] = values
    return Capture(
        path=path,
        fetched_at=pd.Timestamp(fetched).tz_convert("UTC"),
        hourly=frame,
        latitude=float(payload.get("latitude", float("nan"))),
        longitude=float(payload.get("longitude", float("nan"))),
    )


def local_day_hours(day: pd.Timestamp, tz: str = LOCAL_TZ) -> pd.DatetimeIndex:
    """The UTC hour stamps of one local calendar day (23, 24 or 25 of them)."""
    start = pd.Timestamp(day.date()).tz_localize(tz)
    end = (pd.Timestamp(day.date()) + pd.Timedelta(days=1)).tz_localize(tz)
    return pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")


def daily_weather(hourly: pd.DataFrame, day: pd.Timestamp, tz: str = LOCAL_TZ) -> dict | None:
    """ERA5-style daily values of one local day, or None unless every hour of the day is present.

    Same definitions as the daily variables of the archive request: mean, max and min of the hourly
    2 m temperature, sum of the hourly precipitation, max of the hourly 10 m wind speed and the mean
    of the hourly relative humidity and surface pressure, over the hours stamped inside the local day.
    """
    hours = local_day_hours(day, tz)
    block = hourly.reindex(hours)
    if block[list(WEATHER_HOURLY)].isna().any().any():
        return None
    return {
        "temperature_2m_mean": float(block["temperature_2m"].mean()),
        "temperature_2m_max": float(block["temperature_2m"].max()),
        "temperature_2m_min": float(block["temperature_2m"].min()),
        "precipitation_sum": float(block["precipitation"].sum()),
        "wind_speed_10m_max": float(block["wind_speed_10m"].max()),
        "relative_humidity_2m_mean": float(block["relative_humidity_2m"].mean()),
        "surface_pressure_mean": float(block["surface_pressure"].mean()),
    }


def capture_files(landing: Path) -> list[Path]:
    return sorted(Path(landing).glob("source=openmeteo/endpoint=forecast/fetched_date=*/*.json.gz"))


def choose_capture(paths: list[Path], day: pd.Timestamp, issued_at: pd.Timestamp) -> tuple[Capture, dict]:
    """Most recent capture taken after local day `day` ended and covering all of its hours.

    A capture taken after the day ended holds that day as past (analysis) hours, so no forecast
    hour enters the inputs. Captures taken after `issued_at` are ignored.
    """
    day_end = local_day_hours(day)[-1] + pd.Timedelta(hours=1)
    candidates = []
    skipped = []
    for path in paths:
        try:
            capture = load_capture(path)
        except (ValueError, KeyError, OSError) as exc:
            skipped.append(f"{path.name}: {exc}")
            continue
        if capture.fetched_at > issued_at:
            continue
        if capture.fetched_at < day_end:
            continue
        values = daily_weather(capture.hourly, day)
        if values is None:
            skipped.append(f"{path.name}: does not cover every hour of {day.date()}")
            continue
        candidates.append((capture.fetched_at, path.name, capture, values))
    if not candidates:
        raise LiveInputError(
            f"no Open-Meteo capture taken after the end of local day {day.date()} "
            f"({day_end.isoformat()}) covers every hour of that day"
            + (f"; skipped: {'; '.join(skipped)}" if skipped else "")
        )
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, capture, values = candidates[-1]
    return capture, values


# ---------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------


def latest_utd_file(landing: Path, station_id: str = dataset.STATION_ID) -> Path:
    name = f"SPO-{station_id}_{dataset.PM10_CODE}_100.parquet"
    found = sorted(Path(landing).glob(f"source=eea/endpoint=utd_hourly/fetch_date=*/{name}"))
    if not found:
        raise LiveInputError(f"no landed UTD hourly file {name} under {landing}")
    return found[-1]


def latest_e1a_file(landing: Path, station_id: str = dataset.STATION_ID) -> Path:
    name = f"SPO-{station_id}_{dataset.PM10_CODE}_100.parquet"
    found = sorted(Path(landing).glob(f"source=eea/endpoint=e1a_daily/fetch_date=*/{name}"))
    if not found:
        raise LiveInputError(f"no landed E1a daily file {name} under {landing}")
    return found[-1]


def combined_pm10(e1a: pd.Series, utd: pd.DataFrame) -> pd.Series:
    """Verified E1a daily means, extended with the UTD daily means of later days."""
    extra = utd["pm10"].dropna()
    extra = extra[~extra.index.isin(e1a.index)]
    return pd.concat([e1a, extra]).sort_index()


def eea_day_end_utc(day: pd.Timestamp) -> pd.Timestamp:
    """End of EEA day `day` (UTC+1) as a UTC timestamp: 23:00 UTC of that date."""
    return pd.Timestamp(day.date()).tz_localize("UTC") + pd.Timedelta(days=1) - EEA_UTC_OFFSET


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def live_feature_row(
    e1a: pd.Series,
    utd: pd.DataFrame,
    era5: pd.DataFrame,
    asof: pd.Timestamp,
    weather_t: dict,
) -> pd.Series:
    """Model table row of as-of day `asof`, built by plovdiv.ml.features.build_model_table.

    The daily table holds the E1a and UTD PM10 means, no PM2.5, the ERA5 history and, for day
    `asof` only, the weather aggregated from the forecast capture (radiation stays missing).
    """
    pm10 = combined_pm10(e1a, utd)
    weather = era5.reindex(columns=list(dataset.WEATHER_COLUMNS)).copy()
    weather = weather[weather.index < asof]
    today = pd.DataFrame([{c: weather_t.get(c, np.nan) for c in dataset.WEATHER_COLUMNS}], index=[asof])
    weather = pd.concat([weather, today]).astype(float)
    pm10 = pm10[pm10.index <= asof]
    pm25 = pd.Series(dtype=float, index=pd.DatetimeIndex([], dtype="datetime64[ns]"))
    daily = dataset.build_daily_table(pm10, pm25, weather)
    table = features.build_model_table(daily)
    return table.loc[asof]


@dataclass
class LiveResult:
    row: dict
    features: dict
    inputs: dict


def score_live(
    document: dict,
    utd_file: Path,
    e1a_file: Path,
    era5_file: Path,
    capture_paths: list[Path],
    issued_at: pd.Timestamp,
    station_id: str = dataset.STATION_ID,
) -> LiveResult:
    """Score the live row or raise LiveInputError with the reason. Nothing is written here."""
    if document.get("variant") != features.LIVE_VARIANT:
        raise LiveInputError(f"model file is variant {document.get('variant')!r}, expected {features.LIVE_VARIANT!r}")
    issued_at = pd.Timestamp(issued_at).tz_convert("UTC")
    hourly = load_utd_hourly(utd_file)
    utd = utd_daily_means(hourly)
    asof = latest_closed_day(hourly)
    target = asof + pd.Timedelta(days=1)

    if issued_at < eea_day_end_utc(asof):
        raise LiveInputError(
            f"issue time {issued_at.isoformat()} is before the end of the as-of day {asof.date()} "
            f"({eea_day_end_utc(asof).isoformat()}); the inputs would postdate the issue"
        )
    if issued_at >= eea_day_end_utc(target):
        raise LiveInputError(
            f"stale station data: the newest complete EEA day in {utd_file.parent.name}/{utd_file.name} is "
            f"{asof.date()}, so the target day would be {target.date()}, which ended at "
            f"{eea_day_end_utc(target).isoformat()} before the issue time {issued_at.isoformat()}"
        )
    if asof not in utd.index or pd.isna(utd.loc[asof, "pm10"]):
        hours = int(utd.loc[asof, "valid_hours"]) if asof in utd.index else 0
        raise LiveInputError(
            f"PM10 of as-of day {asof.date()} is not a valid daily mean: {hours} valid hours, "
            f"at least {MIN_VALID_HOURS} needed"
        )

    capture, weather_t = choose_capture(capture_paths, asof, issued_at)
    row = live_feature_row(dataset.load_eea_daily(e1a_file), utd, dataset.load_weather(era5_file), asof, weather_t)
    names = list(document["feature_names"])
    missing = [n for n in names if pd.isna(row[n])]
    if missing:
        raise LiveInputError(f"features missing for as-of day {asof.date()}: {missing}")
    x = row[names].to_numpy(dtype=float)[None, :]
    probability = float(marts.reconstruct_probability(document, x)[0])
    threshold = float(document["threshold"]["value"])

    window = utd.loc[asof - pd.Timedelta(days=6) : asof]
    result_row = {
        "station_id": station_id,
        "target_date": target.date(),
        "issued_at": issued_at,
        "probability": probability,
        "label_id": document["label"]["id"],
        "split": SPLIT,
        "actual_label": None,
        "model_id": document["model"],
        "asof_date": asof.date(),
        "threshold": threshold,
        "predicted_label": int(probability >= threshold),
        "label_threshold_ugm3": float(row["label_threshold"]) if pd.notna(row["label_threshold"]) else None,
    }
    inputs = {
        "utd_file": f"{utd_file.parent.name}/{utd_file.name}",
        "utd_sha256": sha256_of(utd_file),
        "utd_last_hour_end": hourly["end"].max().isoformat(),
        "e1a_file": f"{e1a_file.parent.name}/{e1a_file.name}",
        "era5_file": f"{era5_file.parent.name}/{era5_file.name}",
        "weather_capture": f"{capture.path.parent.name}/{capture.path.name}",
        "weather_capture_fetched_at_utc": capture.fetched_at.isoformat(),
        "weather_capture_grid": [capture.latitude, capture.longitude],
        "weather_day_t": weather_t,
        "pm10_daily_last7": {
            d.strftime("%Y-%m-%d"): {
                "pm10": None if pd.isna(r["pm10"]) else round(float(r["pm10"]), 3),
                "valid_hours": int(r["valid_hours"]),
                "verification": r["verification"],
            }
            for d, r in window.iterrows()
        },
    }
    return LiveResult(row=result_row, features={n: float(row[n]) for n in names}, inputs=inputs)


# ---------------------------------------------------------------------------------------
# Live store and marts
# ---------------------------------------------------------------------------------------

STORE_COLUMNS = [
    "station_id", "target_date", "issued_at", "probability", "label_id", "split", "actual_label",
    "model_id", "asof_date", "threshold", "predicted_label", "features_json", "inputs_json",
]


def read_store(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=STORE_COLUMNS)
    return pd.read_parquet(path)


def upsert_store(path: Path, result: LiveResult) -> pd.DataFrame:
    """Add the row to the live store; a row with the same station, target day and model is replaced."""
    path = Path(path)
    store = read_store(path)
    new = pd.DataFrame(
        [
            {
                **{k: result.row[k] for k in STORE_COLUMNS if k in result.row},
                "features_json": json.dumps(result.features, sort_keys=True),
                "inputs_json": json.dumps(result.inputs, sort_keys=True, default=str),
            }
        ]
    )
    new["target_date"] = pd.to_datetime(new["target_date"])
    new["asof_date"] = pd.to_datetime(new["asof_date"])
    if len(store):
        store["target_date"] = pd.to_datetime(store["target_date"])
        key = (store["station_id"] == result.row["station_id"]) & (
            store["target_date"] == pd.Timestamp(result.row["target_date"])
        ) & (store["model_id"] == result.row["model_id"])
        store = store[~key]
        store = pd.concat([store, new], ignore_index=True)
    else:
        store = new
    store = store[STORE_COLUMNS].sort_values(["station_id", "target_date", "model_id"]).reset_index(drop=True)
    store["issued_at"] = pd.to_datetime(store["issued_at"], utc=True).astype("datetime64[us, UTC]")
    store["actual_label"] = store["actual_label"].astype("Int64")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    store.to_parquet(tmp, index=False)
    tmp.replace(path)
    return store


def _manifest_refresh(marts_dir: Path, frames: dict[str, pd.DataFrame], issued_at: pd.Timestamp) -> dict | None:
    """Update counts and ranges of the rewritten marts in manifest.json, when it exists.

    The run id gets a `+live-<hash>` suffix over the store content, so readers keyed on the run id
    see the change; generated_at_utc becomes the issue time.
    """
    path = Path(marts_dir) / "manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for name, frame in frames.items():
        if name in manifest.get("marts", {}) or name == "mart_aq_risk_daily":
            manifest["marts"][name] = marts.manifest_entry(name, frame)
    digest = hashlib.sha256()
    for name in sorted(frames):
        digest.update(marts.mart_path(marts_dir, name).read_bytes())
    base = str(manifest["run_id"]).split("+live-", 1)[0]
    manifest["run_id"] = f"{base}+live-{digest.hexdigest()[:12]}"
    manifest["generated_at_utc"] = issued_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    marts.write_json(path, manifest)
    return manifest


def refresh_marts(marts_dir: Path, store: pd.DataFrame, documents: dict[str, dict], issued_at: pd.Timestamp) -> dict:
    """Replace the `live` rows of the risk and drivers marts with the store content.

    The other rows are kept as they are. Both marts are validated against the contract before
    anything is written; a mart that fails is not written and the error is raised.
    """
    marts_dir = Path(marts_dir)
    risk_path = marts.mart_path(marts_dir, "mart_aq_risk_daily")
    if not risk_path.exists():
        raise LiveInputError(f"{risk_path} does not exist; build the model marts first (python -m plovdiv.ml.marts)")
    card_path = marts.mart_path(marts_dir, "mart_model_card")
    if card_path.exists():
        known = set(pd.read_parquet(card_path)["model_id"])
        unknown = sorted(set(store["model_id"]) - known)
        if unknown:
            raise LiveInputError(
                f"models {unknown} are not in mart_model_card; rebuild the model marts with the live model "
                "(python -m plovdiv.ml.train, then python -m plovdiv.ml.marts)"
            )
    risk = pd.read_parquet(risk_path)
    risk = risk[risk["split"] != SPLIT]
    live_rows = marts.live_risk_rows(store)
    risk = pd.concat([risk, live_rows], ignore_index=True)
    risk = risk.sort_values(["station_id", "target_date", "model_id"]).reset_index(drop=True)

    frames = {"mart_aq_risk_daily": risk}
    drivers_path = marts.mart_path(marts_dir, "mart_aq_risk_drivers")
    if drivers_path.exists():
        drivers = pd.read_parquet(drivers_path)
        keys = set(zip(live_rows["station_id"], live_rows["target_date"], live_rows["model_id"]))
        old_keys = list(zip(drivers["station_id"], drivers["target_date"], drivers["model_id"]))
        drivers = drivers[[k not in keys for k in old_keys]]
        drivers = drivers[drivers["model_id"].isin(set(risk["model_id"]))]
        drivers = pd.concat([drivers, marts.live_driver_rows(store, documents)], ignore_index=True)
        frames["mart_aq_risk_drivers"] = drivers.sort_values(
            ["station_id", "target_date", "model_id", "rank"]
        ).reset_index(drop=True)

    tables = {}
    for name, frame in frames.items():
        table = marts.to_table(name, frame)
        problems = marts.validate(name, table)
        if problems:
            raise ValueError(f"{name} fails the contract schema, not written: {problems}")
        tables[name] = table
    for name, table in tables.items():
        marts.write_parquet(table, marts.mart_path(marts_dir, name))
    manifest = _manifest_refresh(marts_dir, frames, issued_at)
    return {"rows": {n: int(len(f)) for n, f in frames.items()}, "manifest_run_id": manifest and manifest["run_id"]}


# ---------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------


def write_report(path: Path, report: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _json_row(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if isinstance(value, pd.Timestamp):
            out[key] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def run(
    landing: Path,
    models_dir: Path,
    marts_dir: Path,
    store_path: Path,
    issued_at: pd.Timestamp,
    utd_file: Path | None = None,
    write_marts: bool = True,
) -> dict:
    """Score, store and publish one live row. Raises LiveInputError when an input is not usable."""
    model_file = Path(models_dir) / marts.LIVE_MODEL_FILE
    if not model_file.exists():
        raise LiveInputError(f"{model_file} does not exist; train with python -m plovdiv.ml.train")
    document = marts.read_json(model_file)
    try:
        era5_file = _era5_file(landing)
    except FileNotFoundError as exc:
        raise LiveInputError(str(exc)) from exc
    result = score_live(
        document,
        utd_file=Path(utd_file) if utd_file else latest_utd_file(landing),
        e1a_file=latest_e1a_file(landing),
        era5_file=era5_file,
        capture_paths=capture_files(landing),
        issued_at=issued_at,
    )
    report = {"status": "scored", "row": _json_row(result.row), "features": result.features, "inputs": result.inputs}
    if write_marts:
        store = upsert_store(store_path, result)
        documents = {d["model"]: d for d in marts.model_documents(Path(models_dir))}
        report["marts"] = refresh_marts(marts_dir, store, documents, pd.Timestamp(issued_at).tz_convert("UTC"))
        report["store"] = str(store_path)
    return report


def _era5_file(landing: Path) -> Path:
    directory = marts.latest_partition(Path(landing), "source=openmeteo/endpoint=archive_daily_era5/fetch_date=*")
    for pattern in ("archive_*.json.gz", "archive_*.json"):
        found = sorted(directory.glob(pattern))
        if found:
            return found[-1]
    raise FileNotFoundError(f"no archive response in {directory}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m plovdiv.ml.live",
        description="Score the live PM10 risk row for the day after the newest complete station day.",
    )
    parser.add_argument("--landing-dir", type=Path, default=LANDING)
    parser.add_argument("--utd-file", type=Path, default=None, help="UTD hourly parquet (default: latest landed)")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--marts-dir", type=Path, default=DEFAULT_MARTS_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE, help="live prediction store (parquet)")
    parser.add_argument("--report", type=Path, default=None, help="run report (default: next to the store)")
    parser.add_argument("--issued-at", default=None, help="issue time, ISO-8601 (default: now, UTC)")
    parser.add_argument("--dry-run", action="store_true", help="score and print, write nothing but the report")
    args = parser.parse_args(argv)

    issued_at = pd.Timestamp(args.issued_at) if args.issued_at else pd.Timestamp.now(tz="UTC")
    if issued_at.tzinfo is None:
        parser.error("--issued-at needs a time zone, for example 2026-10-09T13:00:00Z")
    issued_at = issued_at.tz_convert("UTC").floor("s")
    report_path = args.report or Path(args.store).parent / "last_run.json"
    try:
        report = run(
            args.landing_dir, args.models_dir, args.marts_dir, args.store, issued_at,
            utd_file=args.utd_file, write_marts=not args.dry_run,
        )
    except LiveInputError as exc:
        write_report(report_path, {"status": "refused", "issued_at": issued_at.isoformat(), "reason": str(exc)})
        print(f"NO LIVE ROW: {exc}", file=sys.stderr)
        return 2
    report["issued_at"] = issued_at.isoformat()
    write_report(report_path, report)
    row = report["row"]
    print(
        f"live row: station {row['station_id']} as-of {row['asof_date']} target {row['target_date']} "
        f"probability {row['probability']:.4f} threshold {row['threshold']:.2f} "
        f"label {row['predicted_label']} model {row['model_id']} issued {row['issued_at']}"
    )
    if args.dry_run:
        print("dry run: nothing written except the report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
