"""Build the two required marts and the authoritative manifest.json, straight from the landing zone.

    python -m plovdiv.marts.core

This is a direct landing-to-mart path. The bronze layer and the dbt models are not built yet, so the
two required marts of the contract are produced here, reading the landed files directly:

    mart_aq_daily            EEA E1a daily station means (data/landing/source=eea/endpoint=e1a_daily)
    mart_weather_aq_hourly   CAMS Europe air quality and Open-Meteo (ECMWF IFS) weather snapshots
                             (data/landing/source=cams, data/landing/source=openmeteo)

When bronze and dbt exist they replace this module; the contract, the output paths and the manifest do
not change.

The command then assembles `<out-dir>/manifest.json` from these two marts and the optional marts that
`python -m plovdiv.ml.marts` wrote earlier (it must run first), and checks the whole output: the manifest
against contracts/manifest.schema.json, every Parquet file against its Pandera schema in
contracts/schemas.py, and the rules that tie the marts together. `manifest.json` is the authoritative
manifest. `manifest.partial.json`, written by plovdiv.ml.marts before the required marts existed, is only
an earlier, incomplete listing and is not read by consumers.

How the landed values map onto the contract
-------------------------------------------
mart_aq_daily
    * One row per day, station location and pollutant, from the first to the last reported day of each
      EEA file. `day` is the EEA day (UTC+1) taken from the `Start` of the daily record.
    * Only records with Validity 1, a numeric Value and a non-negative Value are valid. Every other
      record, and every day missing between the first and last reported day, becomes a row with
      daily_mean NULL, valid_hours 0 and is_valid_day false, so a gap is data and never an interpolated value.
    * The unit is converted to ug/m3; an unknown unit stops the build.
    * The source reports daily means, not hourly values. The contract wants the number of valid hourly
      readings in `valid_hours` and defines is_valid_day as valid_hours >= 18, and it has no way to say
      "not reported". A day that the source marks valid already met the EEA rule of 18 of 24 readings, so
      a valid day gets valid_hours = DataCapture * 24 when the source reports DataCapture and otherwise
      the guaranteed minimum of 18. That is a lower bound, not a count (see the README).
    * The contract keys the daily mart by location, not by station. The urban background station
      Kamenitsa (BG0051A) is the reference of location `plovdiv`; the traffic station Trakia (BG0078A) is
      published under its own location `plovdiv-trakia`, because two stations cannot share a key.

mart_weather_aq_hourly
    * One row per hour, location `plovdiv` and source_type. A value is `analysis` when its hour is not
      after the hour of the capture and `forecast` when it is later, so a snapshot taken at 14:33 UTC
      gives analysis up to 14:00 UTC and forecast from 15:00 UTC.
    * The two sources are joined on the hour and the source_type. They come from different grid cells
      (the echoed coordinates are in the diagnostics); the contract has no column for that.
    * Units are read from the file and converted to the contract units (ug/m3, degC, m/s, mm). Open-Meteo
      wind is converted from km/h when the file says km/h. A value outside the range of the schema
      becomes NULL and is counted.
    * When several snapshots are landed, the latest snapshot decides a given hour and source_type.
      The contract has no column for the forecast issue time, so only the latest forecast vintage is kept.

Output is deterministic: files are read in sorted order, rows are sorted by the grain of each mart, values
are rounded, Parquet files are written with fixed settings and without pandas metadata, the run id is a
hash of the input and output bytes, and no wall-clock time is recorded. The generation time of the manifest
is an argument. Running the command twice on the same inputs writes identical bytes.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
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

from plovdiv.ml import marts as model_marts  # noqa: E402

LANDING = model_marts.LANDING
DEFAULT_OUT_DIR = model_marts.DEFAULT_OUT_DIR
MANIFEST_SCHEMA_PATH = REPO_ROOT / "contracts" / "manifest.schema.json"

HOURLY_MART = "mart_weather_aq_hourly"
DAILY_MART = "mart_aq_daily"
REQUIRED_MARTS = [HOURLY_MART, DAILY_MART]
OPTIONAL_MARTS = list(model_marts.MART_ORDER)
ALL_MARTS = REQUIRED_MARTS + OPTIONAL_MARTS
# Order of the entries in manifest.json (the order of the contract fixtures).
MANIFEST_ORDER = [
    HOURLY_MART,
    DAILY_MART,
    "mart_aq_trend_monthly",
    "mart_aq_risk_daily",
    "mart_aq_risk_drivers",
    "mart_model_card",
    "mart_model_calibration",
]

ARROW_SCHEMAS = {
    HOURLY_MART: contract_types.HOURLY_PYARROW_SCHEMA,
    DAILY_MART: contract_types.DAILY_PYARROW_SCHEMA,
    **model_marts.ARROW_SCHEMAS,
}
PANDERA_SCHEMAS = {
    HOURLY_MART: schemas.mart_weather_aq_hourly_schema,
    DAILY_MART: schemas.mart_aq_daily_schema,
    **schemas.OPTIONAL_MART_SCHEMAS,
}

HOURLY_LOCATION = "plovdiv"
# The daily mart is keyed by location. Station -> location of its daily series.
DAILY_LOCATIONS = {"BG0051A": "plovdiv", "BG0078A": "plovdiv-trakia"}
# EEA pollutant vocabulary code -> contract pollutant.
POLLUTANTS = {5: "pm10", 6001: "pm2_5", 8: "no2"}

MIN_VALID_HOURS = 18  # EEA data-capture rule of the contract: at least 18 of 24 hourly values
HOURS_PER_DAY = 24
VALUE_DECIMALS = 4

# Factors to ug/m3 for the unit strings the sources use.
CONCENTRATION_UNITS = {
    "ug.m-3": 1.0,
    "ug/m3": 1.0,
    "\u03bcg/m\u00b3": 1.0,  # Greek small letter mu
    "\u00b5g/m\u00b3": 1.0,  # micro sign
    "mg.m-3": 1000.0,
    "ng.m-3": 0.001,
}
# Factors to m/s.
WIND_UNITS = {"m/s": 1.0, "km/h": 1.0 / 3.6, "mph": 0.44704, "kn": 1852.0 / 3600.0}
TEMPERATURE_UNITS = {"\u00b0C": 1.0}
PRECIPITATION_UNITS = {"mm": 1.0}

# Source variable -> (contract column, accepted units with factors).
CAMS_VARIABLES = {
    "pm10": ("pm10", CONCENTRATION_UNITS),
    "pm2_5": ("pm2_5", CONCENTRATION_UNITS),
    "nitrogen_dioxide": ("no2", CONCENTRATION_UNITS),
}
WEATHER_VARIABLES = {
    "temperature_2m": ("temperature_2m", TEMPERATURE_UNITS),
    "wind_gusts_10m": ("wind_gusts_10m", WIND_UNITS),
    "precipitation": ("precipitation", PRECIPITATION_UNITS),
}
MEASURE_COLUMNS = ["pm2_5", "pm10", "no2", "temperature_2m", "wind_gusts_10m", "precipitation"]
# Range of the contract schema; a value outside it is NULL.
HOURLY_RANGES = {
    "pm2_5": (0.0, None),
    "pm10": (0.0, None),
    "no2": (0.0, None),
    "temperature_2m": (-60.0, 60.0),
    "wind_gusts_10m": (0.0, None),
    "precipitation": (0.0, None),
}

# Metadata of the six variables of the contract. The manifest schema allows only a unit and a label.
VARIABLES = {
    "pm2_5": {"unit": "ug/m3", "label": "PM2.5"},
    "pm10": {"unit": "ug/m3", "label": "PM10"},
    "no2": {"unit": "ug/m3", "label": "NO2"},
    "temperature_2m": {"unit": "degC", "label": "Temperature at 2 m"},
    "wind_gusts_10m": {"unit": "m/s", "label": "Wind gusts at 10 m"},
    "precipitation": {"unit": "mm", "label": "Precipitation (sum of the preceding hour)"},
}

ATTRIBUTION = [
    "Weather: Open-Meteo (https://open-meteo.com/), CC BY 4.0. Hourly forecasts: ECMWF IFS. "
    "Daily history: ERA5 reanalysis (Copernicus Climate Change Service, ECMWF)",
    "Air quality forecasts and analyses: Copernicus Atmosphere Monitoring Service (CAMS) Europe, via Open-Meteo. "
    "Contains modified Copernicus Atmosphere Monitoring Service information",
    "Air quality stations: European Environment Agency, air quality data reported by Bulgaria",
]

SAMPLINGPOINT = re.compile(r"SPO-(?P<station>[A-Z0-9]+)_(?P<code>\d{5})_(?P<n>\d+)$")
GENERATED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------------------
# mart_aq_daily
# ---------------------------------------------------------------------------------------


def capture_hours(capture: pd.Series) -> pd.Series:
    """Valid hours implied by the DataCapture field, NaN where it is absent or unreadable.

    DataCapture is a share of the day: 0 to 1, or a percentage when larger than 1.
    """
    share = pd.to_numeric(capture, errors="coerce").astype("float64")
    share = share.where(share <= 1.0, share / 100.0)
    share = share.where((share >= 0.0) & (share <= 1.0))
    return (share * HOURS_PER_DAY).round()


def read_eea_daily(path: Path) -> tuple[pd.DataFrame, dict]:
    """One landed EEA E1a file as a complete daily table of one station and pollutant.

    Returns the rows of mart_aq_daily and a dictionary of counts for the diagnostics.
    """
    path = Path(path)
    frame = pd.read_parquet(path)
    needed = {"Samplingpoint", "Pollutant", "Start", "End", "Value", "Unit", "AggType", "Validity"}
    missing = needed - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{path.name}: no records")

    points = frame["Samplingpoint"].unique()
    match = SAMPLINGPOINT.search(str(points[0])) if len(points) == 1 else None
    if match is None:
        raise ValueError(f"{path.name}: expected exactly one sampling point, found {sorted(map(str, points))}")
    station_id, code = match.group("station"), int(match.group("code"))
    if set(frame["Pollutant"].astype(int)) != {code}:
        raise ValueError(f"{path.name}: Pollutant {sorted(set(frame['Pollutant']))} does not match sampling point code {code}")
    if code not in POLLUTANTS:
        raise ValueError(f"{path.name}: pollutant code {code} is not in the contract")
    if station_id not in DAILY_LOCATIONS:
        raise ValueError(f"{path.name}: station {station_id} has no location in DAILY_LOCATIONS")
    if (frame["AggType"] != "day").any():
        raise ValueError(f"{path.name}: records with AggType other than 'day': {sorted(set(frame['AggType']) - {'day'})}")

    start = pd.to_datetime(frame["Start"])
    end = pd.to_datetime(frame["End"])
    if start.dt.tz is not None:
        # Aware timestamps: the EEA day is a fixed UTC+1 day.
        start = start.dt.tz_convert("Etc/GMT-1").dt.tz_localize(None)
        end = end.dt.tz_convert("Etc/GMT-1").dt.tz_localize(None)
    if (start != start.dt.normalize()).any() or ((end - start) != pd.Timedelta(days=1)).any():
        raise ValueError(f"{path.name}: records that are not whole EEA days (Start at midnight, End one day later)")

    factors = frame["Unit"].map(CONCENTRATION_UNITS)
    if factors.isna().any():
        raise ValueError(f"{path.name}: unknown units {sorted(set(frame.loc[factors.isna(), 'Unit']))}")
    value = pd.to_numeric(frame["Value"], errors="coerce").astype("float64") * factors.astype("float64")
    validity = pd.to_numeric(frame["Validity"], errors="coerce")
    valid = (validity == 1) & value.notna() & np.isfinite(value) & (value >= 0)
    if "DataCapture" in frame.columns:
        hours = capture_hours(frame["DataCapture"])
    else:
        hours = pd.Series(np.nan, index=frame.index)
    hours = hours.fillna(float(MIN_VALID_HOURS))
    result_time = pd.to_datetime(frame["ResultTime"]) if "ResultTime" in frame.columns else pd.Series(pd.NaT, index=frame.index)

    records = pd.DataFrame(
        {
            "day": start.dt.normalize(),
            "valid": valid,
            "value": value.where(valid),
            "hours": hours.where(valid, 0.0),
            "result_time": result_time,
        }
    )
    if int(records.groupby("day")["valid"].sum().max()) > 1:
        raise ValueError(f"{path.name}: more than one valid record for the same day")
    # A day with several records keeps the valid one, else the most recently produced.
    records = records.sort_values(["day", "valid", "result_time"], ascending=[True, False, False], kind="stable")
    kept = records.drop_duplicates("day", keep="first")

    days = pd.date_range(kept["day"].min(), kept["day"].max(), freq="D")
    daily = kept.set_index("day").reindex(days)
    reported_valid = daily["valid"].eq(True)
    valid_hours = daily["hours"].where(reported_valid, 0.0).fillna(0.0).astype("int64")
    out = pd.DataFrame(
        {
            "day": [d.date() for d in days],
            "location_id": DAILY_LOCATIONS[station_id],
            "pollutant": POLLUTANTS[code],
            "daily_mean": daily["value"].where(reported_valid).round(VALUE_DECIMALS).to_numpy(dtype="float64"),
            "valid_hours": valid_hours.to_numpy(),
            "is_valid_day": (valid_hours >= MIN_VALID_HOURS).to_numpy(),
        }
    )
    capture_reported = "DataCapture" in frame.columns and bool(pd.to_numeric(frame["DataCapture"], errors="coerce").notna().any())
    counts = {
        "station_id": station_id,
        "pollutant": POLLUTANTS[code],
        "location_id": DAILY_LOCATIONS[station_id],
        "records": int(len(frame)),
        "records_valid": int(valid.sum()),
        "records_not_valid": int((~valid).sum()),
        "records_duplicate_days_dropped": int(len(records) - len(kept)),
        "days_without_record": int(len(days) - len(kept)),
        "rows": int(len(out)),
        "valid_days": int(out["is_valid_day"].sum()),
        "first_day": out["day"].iloc[0].isoformat(),
        "last_day": out["day"].iloc[-1].isoformat(),
        "validity_codes": {str(k): int(v) for k, v in sorted(frame["Validity"].value_counts().items())},
        "verification_codes": (
            {str(k): int(v) for k, v in sorted(frame["Verification"].value_counts().items())}
            if "Verification" in frame.columns
            else {}
        ),
        "units": sorted(set(frame["Unit"])),
        "data_capture_reported": capture_reported,
    }
    return out, counts


def build_daily(eea_dir: Path) -> tuple[pd.DataFrame, dict]:
    """mart_aq_daily from every `SPO-*.parquet` file of an EEA landing partition."""
    files = sorted(Path(eea_dir).glob("SPO-*.parquet"))
    if not files:
        raise FileNotFoundError(f"no EEA daily files in {eea_dir}")
    parts, series = [], {}
    for path in files:
        frame, counts = read_eea_daily(path)
        parts.append(frame)
        series[path.name] = counts
    daily = pd.concat(parts, ignore_index=True)
    daily = daily.sort_values(["day", "location_id", "pollutant"]).reset_index(drop=True)
    diagnostics = {
        "eea_dir": Path(eea_dir).name,
        "series": series,
        "location_of_station": dict(DAILY_LOCATIONS),
        "valid_hours_note": (
            "The source reports daily means. valid_hours is DataCapture * 24 when the source reports DataCapture, "
            f"otherwise {MIN_VALID_HOURS}, the minimum that a valid EEA day guarantees. It is a lower bound, not a count."
        ),
    }
    return daily, diagnostics


# ---------------------------------------------------------------------------------------
# mart_weather_aq_hourly
# ---------------------------------------------------------------------------------------


def snapshot_files(landing: Path, source: str, endpoint: str) -> list[Path]:
    """Landed snapshot files of one source, in name order (the capture order is read from the files)."""
    return sorted(Path(landing).glob(f"source={source}/endpoint={endpoint}/fetched_date=*/*.json.gz"))


def read_snapshot(path: Path, variables: dict) -> tuple[pd.DataFrame, dict]:
    """One landed snapshot as hourly rows with contract columns, units and source_type.

    `variables` maps a source variable to (contract column, accepted units with factors). Returns the
    rows (all-NULL hours dropped) and a dictionary for the diagnostics that includes the capture time.
    """
    path = Path(path)
    with gzip.open(path, "rb") as handle:
        payload = json.loads(handle.read())
    audit = payload.get("_audit") or {}
    if "fetched_at_utc" not in audit:
        raise ValueError(f"{path.name}: no capture time (_audit.fetched_at_utc); analysis and forecast cannot be told apart")
    fetched_at = pd.Timestamp(audit["fetched_at_utc"])
    if fetched_at.tzinfo is None:
        raise ValueError(f"{path.name}: the capture time has no time zone")
    fetched_at = fetched_at.tz_convert("UTC")
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError(f"{path.name}: times are not UTC (utc_offset_seconds {payload.get('utc_offset_seconds')!r})")

    hourly = payload["hourly"]
    declared = payload.get("hourly_units", {})
    times = pd.to_datetime(pd.Series(hourly["time"], dtype="object"), utc=True).astype("datetime64[us, UTC]")
    if (times.dt.minute != 0).any() or (times.dt.second != 0).any():
        raise ValueError(f"{path.name}: times that are not on the hour")
    if times.duplicated().any():
        raise ValueError(f"{path.name}: duplicate hours")

    last_capture_hour = fetched_at.floor("h")
    frame = pd.DataFrame(
        {
            "valid_at_utc": times,
            "source_type": np.where(times <= last_capture_hour, "analysis", "forecast"),
        }
    )
    units_in_file, nulled, missing = {}, {}, []
    for source_name, (column, accepted) in variables.items():
        if source_name not in hourly:
            frame[column] = np.nan
            missing.append(source_name)
            continue
        unit = declared.get(source_name)
        if unit not in accepted:
            raise ValueError(f"{path.name}: {source_name} is in {unit!r}; accepted units are {sorted(accepted)}")
        units_in_file[source_name] = unit
        raw = pd.to_numeric(pd.Series(hourly[source_name], dtype="object"), errors="coerce").astype("float64")
        values = (raw * accepted[unit]).round(VALUE_DECIMALS)
        low, high = HOURLY_RANGES[column]
        outside = values.notna() & (((values < low) if low is not None else False) | ((values > high) if high is not None else False))
        nulled[column] = int(outside.sum())
        frame[column] = values.mask(outside)

    columns = [c for c, _ in variables.values()]
    empty_hours = frame[columns].isna().all(axis=1)
    last_valid = {c: frame.loc[frame[c].notna(), "valid_at_utc"].max() for c in columns}
    meta = {
        "file": path.name,
        "fetched_at_utc": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "elevation_m": payload.get("elevation"),
        "hours_in_file": int(len(frame)),
        "first_hour": times.min().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_hour": times.max().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "units_in_file": units_in_file,
        "variables_missing_from_file": missing,
        "values_outside_schema_range_set_to_null": nulled,
        "hours_without_any_value_dropped": int(empty_hours.sum()),
        "last_hour_with_value": {c: (t.strftime("%Y-%m-%dT%H:%M:%SZ") if pd.notna(t) else None) for c, t in last_valid.items()},
    }
    return frame.loc[~empty_hours].reset_index(drop=True), {"fetched_at": fetched_at, **meta}


def combine_snapshots(paths: list[Path], variables: dict) -> tuple[pd.DataFrame, list[dict]]:
    """Hourly rows of one source over all its snapshots; the latest capture decides each hour and source_type."""
    columns = [c for c, _ in variables.values()]
    parts, metas = [], []
    for path in paths:
        frame, meta = read_snapshot(path, variables)
        parts.append((meta["fetched_at"], path.name, frame))
        metas.append(meta)
    if not parts:
        empty = pd.DataFrame({"valid_at_utc": pd.Series(dtype="datetime64[us, UTC]"), "source_type": pd.Series(dtype="object")})
        for column in columns:
            empty[column] = pd.Series(dtype="float64")
        return empty, []
    parts.sort(key=lambda item: (item[0], item[1]))
    stacked = pd.concat([frame for _, _, frame in parts], ignore_index=True)
    latest = stacked.drop_duplicates(["valid_at_utc", "source_type"], keep="last")
    metas.sort(key=lambda m: (m["fetched_at"], m["file"]))
    for meta in metas:
        del meta["fetched_at"]
    return latest.reset_index(drop=True), metas


def build_hourly(landing: Path) -> tuple[pd.DataFrame, dict]:
    """mart_weather_aq_hourly from the CAMS and Open-Meteo snapshots under the landing zone."""
    landing = Path(landing)
    cams_files = snapshot_files(landing, "cams", "air_quality")
    weather_files = snapshot_files(landing, "openmeteo", "forecast")
    if not cams_files and not weather_files:
        raise FileNotFoundError(f"no CAMS or Open-Meteo snapshots under {landing}")
    cams, cams_meta = combine_snapshots(cams_files, CAMS_VARIABLES)
    weather, weather_meta = combine_snapshots(weather_files, WEATHER_VARIABLES)

    hourly = cams.merge(weather, on=["valid_at_utc", "source_type"], how="outer")
    hourly["location_id"] = HOURLY_LOCATION
    hourly = hourly[["valid_at_utc", "location_id", "source_type", *MEASURE_COLUMNS]]
    hourly = hourly.sort_values(["location_id", "source_type", "valid_at_utc"]).reset_index(drop=True)
    for column in MEASURE_COLUMNS:
        hourly[column] = hourly[column].astype("float64")

    per_type = {
        kind: {
            "rows": int(len(group)),
            "first_hour": group["valid_at_utc"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_hour": group["valid_at_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "non_null": {c: int(group[c].notna().sum()) for c in MEASURE_COLUMNS},
        }
        for kind, group in hourly.groupby("source_type")
    }
    diagnostics = {
        "cams_snapshots": cams_meta,
        "openmeteo_snapshots": weather_meta,
        "by_source_type": per_type,
        "classification_rule": "analysis when the hour is not after the hour of the capture, forecast when it is later",
    }
    return hourly, diagnostics


# ---------------------------------------------------------------------------------------
# Validation and writing of the two marts
# ---------------------------------------------------------------------------------------


def to_table(name: str, df: pd.DataFrame) -> pa.Table:
    """Arrow table with the column types of the contract fixtures and no pandas metadata."""
    schema = ARROW_SCHEMAS[name]
    table = pa.Table.from_pandas(df[schema.names], schema=schema, preserve_index=False)
    return table.replace_schema_metadata(None)


def validate_frame(name: str, frame: pd.DataFrame) -> list[str]:
    """Run the Pandera schema of the contract on what a reader of the file would see. Returns problems."""
    import pandera.errors

    try:
        PANDERA_SCHEMAS[name].validate(frame, lazy=True)
    except pandera.errors.SchemaErrors as exc:
        cases = exc.failure_cases
        problems = sorted({f"{row.column if isinstance(row.column, str) else 'table'}: {row.check}" for row in cases.itertuples()})
        return problems or [str(exc)]
    except pandera.errors.SchemaError as exc:
        return [str(exc)]
    return []


def validate(name: str, table: pa.Table) -> list[str]:
    return validate_frame(name, table.to_pandas())


def write_required_marts(out_dir: Path, hourly: pd.DataFrame, daily: pd.DataFrame) -> dict[str, int]:
    """Validate both marts and write them. Nothing is written unless both pass their schema."""
    tables = {HOURLY_MART: to_table(HOURLY_MART, hourly), DAILY_MART: to_table(DAILY_MART, daily)}
    problems = {name: validate(name, table) for name, table in tables.items()}
    problems = {name: found for name, found in problems.items() if found}
    if problems:
        text = "; ".join(f"{name}: {', '.join(found)}" for name, found in sorted(problems.items()))
        raise ValueError(f"mart fails the contract schema, nothing written: {text}")
    for name, table in tables.items():
        model_marts.write_parquet(table, model_marts.mart_path(out_dir, name))
    return {name: int(table.num_rows) for name, table in tables.items()}


# ---------------------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------------------


def read_mart(out_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_parquet(model_marts.mart_path(out_dir, name))


def present_marts(out_dir: Path) -> list[str]:
    return [name for name in MANIFEST_ORDER if model_marts.mart_path(out_dir, name).exists()]


def compute_run_id(out_dir: Path, input_files: list[Path], landing: Path) -> tuple[str, dict]:
    """Run id from the bytes of the inputs and of every mart file: the same content gives the same id."""
    digests = {}
    for path in input_files:
        try:
            label = "input/" + Path(path).resolve().relative_to(Path(landing).resolve()).as_posix()
        except ValueError:
            label = "input/" + Path(path).name
        digests[label] = model_marts.sha256_of(path)
    for name in present_marts(out_dir):
        digests[f"mart/{name}"] = model_marts.sha256_of(model_marts.mart_path(out_dir, name))
    digest = hashlib.sha256(json.dumps(digests, sort_keys=True).encode("utf-8")).hexdigest()
    return f"warehouse-{digest[:12]}", digests


def _utc_text(stamp: pd.Timestamp) -> str:
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def optional_range_fields(name: str, frame: pd.DataFrame) -> dict:
    """The optional range fields of the manifest entries of the two required marts, read from the data.

    Hourly: `max_analysis_valid_at_utc`, the last hour with source_type analysis (left out when there is none).
    Daily: `min_day` and `max_day`, the first and last EEA day that has a row, gap days included.
    """
    if name == HOURLY_MART:
        analysis = frame.loc[frame["source_type"] == "analysis", "valid_at_utc"]
        return {"max_analysis_valid_at_utc": _utc_text(analysis.max())} if len(analysis) else {}
    if name == DAILY_MART:
        if not len(frame):
            return {}
        days = pd.to_datetime(frame["day"])
        return {"min_day": days.min().strftime("%Y-%m-%d"), "max_day": days.max().strftime("%Y-%m-%d")}
    return {}


def assemble_manifest(out_dir: Path, generated_at: str, run_id: str) -> dict:
    """manifest.json from the Parquet files in the output directory: counts and ranges are read from the files."""
    out_dir = Path(out_dir)
    names = present_marts(out_dir)
    missing = [name for name in REQUIRED_MARTS if name not in names]
    if missing:
        raise FileNotFoundError(f"required marts are not in {out_dir}: {missing}")
    frames = {name: read_mart(out_dir, name) for name in names}
    hourly_times = frames[HOURLY_MART]["valid_at_utc"]
    marts = {}
    for name in names:
        if name == HOURLY_MART:
            marts[name] = {
                "glob": f"{name}/latest/*.parquet",
                "rows": int(len(frames[name])),
                "min_valid_at_utc": hourly_times.min().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "max_valid_at_utc": hourly_times.max().strftime("%Y-%m-%dT%H:%M:%SZ"),
                **optional_range_fields(name, frames[name]),
            }
        else:
            marts[name] = {**model_marts.manifest_entry(name, frames[name]), **optional_range_fields(name, frames[name])}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "attribution": list(ATTRIBUTION),
        "variables": {name: dict(entry) for name, entry in VARIABLES.items()},
        "marts": marts,
    }
    if any(name in marts for name in ("mart_aq_trend_monthly", "mart_aq_risk_daily", "mart_aq_risk_drivers")):
        manifest["stations"] = {sid: dict(entry) for sid, entry in model_marts.STATIONS.items()}
    return manifest


# ---------------------------------------------------------------------------------------
# Whole-output checks
# ---------------------------------------------------------------------------------------


def _month_key(days: pd.Series) -> pd.Series:
    return pd.to_datetime(days).dt.to_period("M").dt.to_timestamp()


def cross_mart_problems(frames: dict[str, pd.DataFrame], manifest: dict) -> list[str]:
    """Rules that tie the marts and the manifest together. `frames` holds the marts that exist."""
    problems: list[str] = []
    variables = manifest.get("variables", {})

    hourly = frames.get(HOURLY_MART)
    daily = frames.get(DAILY_MART)
    if hourly is not None:
        for column in hourly.columns:
            if column not in {"valid_at_utc", "location_id", "source_type"} and column not in variables:
                problems.append(f"hourly column {column} is not declared in manifest.variables")
    if daily is not None:
        for pollutant in sorted(set(daily["pollutant"])):
            if pollutant not in variables:
                problems.append(f"daily pollutant {pollutant} is not declared in manifest.variables")
    for name, unit in {c: VARIABLES[c]["unit"] for c in VARIABLES}.items():
        if name in variables and variables[name].get("unit") != unit:
            problems.append(f"variable {name} has unit {variables[name].get('unit')!r}, expected {unit!r}")

    locations = set(hourly["location_id"]) if hourly is not None else set()
    if hourly is not None and daily is not None and not (locations & set(daily["location_id"])):
        problems.append("the hourly and the daily mart share no location")
    for sid, entry in manifest.get("stations", {}).items():
        if hourly is not None and entry["location_id"] not in locations:
            problems.append(f"station {sid} belongs to location {entry['location_id']}, which is not in the hourly mart")

    declared = set(manifest.get("stations", {}))
    for name in ("mart_aq_trend_monthly", "mart_aq_risk_daily", "mart_aq_risk_drivers"):
        if name in frames:
            unknown = sorted(set(frames[name]["station_id"]) - declared)
            if unknown:
                problems.append(f"{name}: stations {unknown} are not declared in the manifest")

    card = frames.get("mart_model_card")
    risk = frames.get("mart_aq_risk_daily")
    drivers = frames.get("mart_aq_risk_drivers")
    calibration = frames.get("mart_model_calibration")
    if card is not None:
        models = set(card["model_id"])
        for name, frame in (("mart_aq_risk_daily", risk), ("mart_model_calibration", calibration), ("mart_aq_risk_drivers", drivers)):
            if frame is not None and not set(frame["model_id"]) <= models:
                problems.append(f"{name}: model ids {sorted(set(frame['model_id']) - models)} are not in mart_model_card")
        if risk is not None:
            labels = card.set_index("model_id")["label_id"]
            for model_id, group in risk.groupby("model_id"):
                if model_id in labels.index and set(group["label_id"]) != {labels[model_id]}:
                    problems.append(f"mart_aq_risk_daily: label_id of model {model_id} differs from mart_model_card")
        if drivers is not None:
            features = card.set_index("model_id")["features"]
            for model_id, group in drivers.groupby("model_id"):
                if model_id in features.index and not set(group["feature"]) <= set(features[model_id]):
                    problems.append(f"mart_aq_risk_drivers: features of model {model_id} are not in mart_model_card.features")
    if risk is not None and drivers is not None:
        risk_keys = set(zip(risk["station_id"], risk["target_date"], risk["model_id"]))
        driver_keys = set(zip(drivers["station_id"], drivers["target_date"], drivers["model_id"]))
        if not driver_keys <= risk_keys:
            problems.append("mart_aq_risk_drivers: rows without a matching mart_aq_risk_daily row")

    if daily is not None:
        valid_pm10 = daily[(daily["pollutant"] == "pm10") & daily["is_valid_day"]]
        if risk is not None:
            decided = risk[risk["actual_label"].notna()]
            known = set(zip(valid_pm10["location_id"], valid_pm10["day"]))
            for station_id, group in decided.groupby("station_id"):
                location = DAILY_LOCATIONS.get(station_id)
                if location is None:
                    problems.append(f"mart_aq_risk_daily: station {station_id} has no daily location")
                    continue
                absent = [d for d in group["target_date"] if (location, d) not in known]
                if absent:
                    problems.append(
                        f"mart_aq_risk_daily: {len(absent)} labelled days of {station_id} have no valid PM10 day in mart_aq_daily "
                        f"(first {min(absent)})"
                    )
        trend = frames.get("mart_aq_trend_monthly")
        if trend is not None:
            keyed = daily[daily["is_valid_day"]].assign(month=_month_key(daily.loc[daily["is_valid_day"], "day"]))
            stats = keyed.groupby(["location_id", "pollutant", "month"])["daily_mean"].agg(["count", "mean"])
            for row in trend.itertuples():
                location = DAILY_LOCATIONS.get(row.station_id)
                key = (location, row.pollutant, pd.Timestamp(row.month))
                count, mean = (stats.loc[key, "count"], stats.loc[key, "mean"]) if key in stats.index else (0, np.nan)
                if row.n_days != count:
                    problems.append(
                        f"mart_aq_trend_monthly: {row.station_id} {row.pollutant} {row.month} has n_days {row.n_days}, "
                        f"mart_aq_daily has {count} valid days"
                    )
                elif count and abs(row.observed_mean_ugm3 - mean) > 1e-3:
                    problems.append(
                        f"mart_aq_trend_monthly: {row.station_id} {row.pollutant} {row.month} observed mean "
                        f"{row.observed_mean_ugm3} differs from the daily mean {mean:.4f}"
                    )
                if len(problems) > 50:
                    problems.append("further trend differences not listed")
                    break
    return problems


def validate_output(out_dir: Path) -> list[str]:
    """Check manifest.json and every mart in it. Returns a list of problems; empty means the output is valid."""
    from jsonschema import Draft7Validator

    out_dir = Path(out_dir)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"{manifest_path} does not exist"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    problems = [
        f"manifest.json: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
        for e in sorted(Draft7Validator(schema).iter_errors(manifest), key=lambda e: list(map(str, e.absolute_path)))
    ]
    if not GENERATED_AT.match(str(manifest.get("generated_at_utc", ""))):
        problems.append("manifest.json: generated_at_utc is not an ISO-8601 UTC timestamp")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"manifest.json: schema_version {manifest.get('schema_version')!r} is not {SCHEMA_VERSION!r}")

    frames: dict[str, pd.DataFrame] = {}
    for name, entry in manifest.get("marts", {}).items():
        if name not in PANDERA_SCHEMAS:
            problems.append(f"{name}: not a mart of the contract")
            continue
        files = sorted(out_dir.glob(entry["glob"]))
        if not files:
            problems.append(f"{name}: no file matches {entry['glob']}")
            continue
        frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        frames[name] = frame
        problems += [f"{name}: {p}" for p in validate_frame(name, frame)]
        if entry["rows"] != len(frame):
            problems.append(f"{name}: manifest says {entry['rows']} rows, the file has {len(frame)}")
        if name == HOURLY_MART and len(frame):
            low = frame["valid_at_utc"].min().strftime("%Y-%m-%dT%H:%M:%SZ")
            high = frame["valid_at_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ")
            if (entry["min_valid_at_utc"], entry["max_valid_at_utc"]) != (low, high):
                problems.append(f"{name}: manifest range differs from the file ({low} to {high})")
        if name in (HOURLY_MART, DAILY_MART):
            # Optional fields: when present they must equal what the files contain.
            expected_optional = optional_range_fields(name, frame)
            for key in ("max_analysis_valid_at_utc", "min_day", "max_day"):
                if key in entry and entry[key] != expected_optional.get(key):
                    problems.append(
                        f"{name}: manifest {key} {entry[key]} differs from the file ({expected_optional.get(key)})"
                    )
        if name in ("mart_aq_trend_monthly", "mart_aq_risk_daily") and len(frame):
            expected = model_marts.manifest_entry(name, frame)
            for key in ("min_month", "max_month", "min_target_date", "max_target_date"):
                if key in expected and entry.get(key) != expected[key]:
                    problems.append(f"{name}: manifest {key} {entry.get(key)} differs from the file ({expected[key]})")
    problems += cross_mart_problems(frames, manifest)
    return problems


# ---------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------


def partition_date(path: Path) -> str:
    """Fetch date of a landing partition directory such as `fetch_date=2026-10-07`."""
    return Path(path).name.split("=", 1)[1]


def optional_mart_notes(out_dir: Path, entries: dict) -> dict:
    """Compare the optional marts with the earlier partial manifest and build report, when they exist."""
    notes: dict = {"partial_manifest_differences": [], "build_report_differences": []}
    partial = Path(out_dir) / "manifest.partial.json"
    if partial.exists():
        listed = json.loads(partial.read_text(encoding="utf-8")).get("marts", {})
        for name, entry in sorted(listed.items()):
            if entries.get(name) != entry:
                notes["partial_manifest_differences"].append(f"{name}: partial {entry}, file {entries.get(name)}")
    report = Path(out_dir) / "diagnostics" / "build_report.json"
    if report.exists():
        written = json.loads(report.read_text(encoding="utf-8")).get("marts_written", {})
        for name, rows in sorted(written.items()):
            if entries.get(name, {}).get("rows") != rows:
                notes["build_report_differences"].append(f"{name}: build report {rows} rows, file {entries.get(name, {}).get('rows')}")
    return notes


def build(landing: Path, eea_dir: Path, out_dir: Path, generated_at: str) -> dict:
    """Build both required marts, write them with manifest.json and return the build report.

    The marts are built here from `landing` and `eea_dir` and written to `out_dir`. The dbt project in dbt/
    is not run: it reads and writes fixed paths relative to dbt/ and so cannot honour these arguments.
    """
    landing, eea_dir, out_dir = Path(landing), Path(eea_dir), Path(out_dir)
    daily, daily_diagnostics = build_daily(eea_dir)
    hourly, hourly_diagnostics = build_hourly(landing)
    rows = write_required_marts(out_dir, hourly, daily)

    inputs = sorted(eea_dir.glob("SPO-*.parquet"))
    inputs += snapshot_files(landing, "cams", "air_quality") + snapshot_files(landing, "openmeteo", "forecast")
    run_id, digests = compute_run_id(out_dir, inputs, landing)
    manifest = assemble_manifest(out_dir, generated_at, run_id)
    model_marts.write_json(out_dir / "manifest.json", manifest)

    report = {
        "authoritative_manifest": "manifest.json",
        "note": (
            "manifest.json is the authoritative manifest. manifest.partial.json is an earlier listing written "
            "before the required marts existed; it is kept for history and consumers must not read it."
        ),
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "path": "direct landing-to-mart (plovdiv.marts.core)",
        "marts_written": rows,
        "optional_marts_in_manifest": [n for n in MANIFEST_ORDER if n in manifest["marts"] and n in OPTIONAL_MARTS],
        "optional_marts_check": optional_mart_notes(out_dir, manifest["marts"]),
        "sha256": digests,
        DAILY_MART: daily_diagnostics,
        HOURLY_MART: hourly_diagnostics,
    }
    model_marts.write_json(out_dir / "diagnostics" / "core_build_report.json", report)
    return report


def default_generated_at(landing: Path, eea_dir: Path) -> str:
    """Latest fetch date among the inputs at 00:00 UTC, so that the file is reproducible."""
    dates = [partition_date(Path(eea_dir))]
    for source, endpoint in (("cams", "air_quality"), ("openmeteo", "forecast")):
        dates += [partition_date(p.parent) for p in snapshot_files(landing, source, endpoint)]
    return f"{max(dates)}T00:00:00Z"


def summary_lines(out_dir: Path) -> list[str]:
    lines = []
    manifest = json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["marts"].items():
        lines.append(f"{name}: {entry['rows']} rows")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the required marts of the data contract and manifest.json.")
    parser.add_argument("--landing-dir", type=Path, default=LANDING)
    parser.add_argument("--eea-dir", type=Path, default=None, help="default: the latest EEA E1a partition")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--generated-at",
        default=None,
        help="generated_at_utc of the manifest; default is the latest fetch date of the inputs at 00:00 UTC",
    )
    parser.add_argument("--check-only", action="store_true", help="only validate manifest.json and the marts in --out-dir")
    args = parser.parse_args(argv)

    if not args.check_only:
        eea_dir = args.eea_dir or model_marts.latest_partition(args.landing_dir, "source=eea/endpoint=e1a_daily/fetch_date=*")
        generated_at = args.generated_at or default_generated_at(args.landing_dir, eea_dir)
        if not GENERATED_AT.match(generated_at):
            parser.error("--generated-at must look like 2026-10-07T00:00:00Z")
        report = build(args.landing_dir, eea_dir, args.out_dir, generated_at)
        for name, rows in report["marts_written"].items():
            print(f"wrote {name}: {rows} rows")
        print(f"run id: {report['run_id']}")

    problems = validate_output(args.out_dir)
    if problems:
        print(f"INVALID: {len(problems)} problem(s) in {args.out_dir}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    for line in summary_lines(args.out_dir):
        print(line)
    print(f"manifest: {args.out_dir / 'manifest.json'} (valid; this is the authoritative manifest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
