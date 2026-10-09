"""Daily table, label definition and time-ordered split for the next-day PM10 risk model.

Everything here works on calendar days. A row of the model table is identified by the
as-of day t (the last day whose data are used as input) and describes the target day t + 1.

Label (id `pm10_gt_p75_same_month_prev3y_v1`)
    y(t+1) = 1 if the PM10 daily mean on day t+1 is above the 75th percentile of the PM10
    daily means of the same calendar month in the three calendar years immediately before the
    year of t+1. Only strictly earlier years enter the percentile, so the threshold follows the
    long-term decline in concentrations and never sees the day it is applied to. The first year
    with a full three-year history is 2016 (the station data start in 2013).

Day alignment
    EEA daily values are the daily means as reported by Bulgaria for the station. The ERA5
    archive returns Europe/Sofia local calendar days. The two are joined on the calendar date
    and a possible offset of one hour between the day definitions is ignored.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

STATION_ID = "BG0051A"
PM10_CODE = "00005"
PM25_CODE = "06001"

LABEL_ID = "pm10_gt_p75_same_month_prev3y_v1"
LABEL_TEXT = (
    "1 if the PM10 daily mean on day t+1 is above the 75th percentile of the PM10 daily means "
    "of the same calendar month in the three calendar years immediately before the year of "
    "t+1 (strictly earlier data only), else 0."
)
LABEL_QUANTILE = 0.75
HISTORY_YEARS = 3
# Every one of the history years must contribute at least this many valid days of the month.
MIN_DAYS_PER_HISTORY_MONTH = 10
EU_DAILY_LIMIT = 50.0

# Weather columns of the ERA5 archive request (daily, Europe/Sofia days, wind in m/s).
WEATHER_COLUMNS = (
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "shortwave_radiation_sum",
    "relative_humidity_2m_mean",
    "surface_pressure_mean",
    "pressure_msl_mean",
    "cloud_cover_mean",
    "dew_point_2m_mean",
)

# Time-ordered split on the target date (inclusive bounds).
SPLITS = {
    "train": ("2016-01-01", "2020-12-31"),
    "validation": ("2021-01-01", "2022-12-31"),
    "test": ("2023-01-01", "2025-12-31"),
}


@dataclass(frozen=True)
class SourceFile:
    """A landed input file as read by the training run."""

    role: str
    path: Path


def _to_day_index(values) -> pd.DatetimeIndex:
    """Naive calendar days at nanosecond resolution (independent of the pandas default unit)."""
    return pd.DatetimeIndex(pd.to_datetime(values)).normalize().astype("datetime64[ns]")


def load_eea_daily(path: str | Path) -> pd.Series:
    """Daily means from a landed EEA E1a parquet file, indexed by the calendar day.

    Value is a decimal string; it is cast to float. Only valid rows (Validity == 1) with a
    non-negative value are kept. A day reported twice raises, because the file is expected to
    carry one value per day.
    """
    frame = pd.read_parquet(path)
    value = pd.to_numeric(frame["Value"], errors="coerce")
    keep = (frame["Validity"] == 1) & value.notna() & (value >= 0)
    series = pd.Series(
        value[keep].to_numpy(dtype=float),
        index=_to_day_index(frame.loc[keep, "Start"]),
        name="value",
    )
    if series.index.has_duplicates:
        raise ValueError(f"{path}: more than one valid value for the same day")
    return series.sort_index()


def load_weather(path: str | Path) -> pd.DataFrame:
    """Daily ERA5 values from a landed Open-Meteo archive response (.json or .json.gz)."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        payload = json.loads(handle.read())
    daily = payload["daily"]
    frame = pd.DataFrame({k: v for k, v in daily.items() if k != "time"})
    frame.index = _to_day_index(daily["time"])
    frame.index.name = None
    missing = [c for c in WEATHER_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{path}: missing weather columns {missing}")
    if frame.index.has_duplicates:
        raise ValueError(f"{path}: duplicate days in the weather response")
    return frame[list(WEATHER_COLUMNS)].astype(float).sort_index()


def build_daily_table(pm10: pd.Series, pm25: pd.Series, weather: pd.DataFrame) -> pd.DataFrame:
    """One row per calendar day with PM10, PM2.5 and the weather columns.

    The table covers every day between the first and last day of the weather record and the
    station record, so a missing day is a NaN row and shifts and rolling windows count calendar
    days, never rows.
    """
    start = min(pm10.index.min(), weather.index.min())
    end = max(pm10.index.max(), weather.index.max())
    days = pd.date_range(start, end, freq="D").astype("datetime64[ns]")
    table = pd.DataFrame(index=days)
    table["pm10"] = pm10.reindex(days)
    table["pm25"] = pm25.reindex(days)
    for column in WEATHER_COLUMNS:
        table[column] = weather[column].reindex(days)
    table.index.name = "date"
    return table


def monthly_thresholds(
    pm10: pd.Series,
    quantile: float = LABEL_QUANTILE,
    history_years: int = HISTORY_YEARS,
    min_days: int = MIN_DAYS_PER_HISTORY_MONTH,
) -> pd.DataFrame:
    """Percentile threshold per (year, month) from the same month of the previous years.

    The threshold for (Y, M) uses the valid daily means of month M in years Y-3 .. Y-1 only.
    It is NaN unless every one of those years has at least `min_days` valid days in that month.
    """
    valid = pm10.dropna()
    by_month = {
        key: group.to_numpy()
        for key, group in valid.groupby([valid.index.year, valid.index.month])
    }
    rows = []
    for year in range(int(pm10.index.year.min()), int(pm10.index.year.max()) + 1):
        for month in range(1, 13):
            parts = [by_month.get((year - k, month)) for k in range(1, history_years + 1)]
            ok = all(p is not None and len(p) >= min_days for p in parts)
            threshold = float(np.quantile(np.concatenate(parts), quantile)) if ok else np.nan
            rows.append((year, month, threshold))
    return pd.DataFrame(rows, columns=["year", "month", "threshold"])


def threshold_for_days(days: pd.DatetimeIndex, thresholds: pd.DataFrame) -> pd.Series:
    """Threshold applicable to each day, looked up by the day's own year and month."""
    lookup = thresholds.set_index(["year", "month"])["threshold"]
    keys = pd.MultiIndex.from_arrays([days.year, days.month])
    return pd.Series(lookup.reindex(keys).to_numpy(), index=days)


def climatology_rate(pm10: pd.Series, thresholds: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    """Share of days in the same month of the previous year that exceed this year's threshold.

    Used by the monthly climatology baseline. For a day in (Y, M) it is the fraction of valid
    days of (Y-1, M) above the threshold of (Y, M). It only uses data before year Y. The result
    is looked up for each of the given days.
    """
    valid = pm10.dropna()
    previous_values = {
        key: group.to_numpy()
        for key, group in valid.groupby([valid.index.year, valid.index.month])
    }
    rates = {}
    for (year, month), threshold in thresholds.set_index(["year", "month"])["threshold"].items():
        previous = previous_values.get((year - 1, month))
        usable = previous is not None and not np.isnan(threshold)
        rates[(year, month)] = float(np.mean(previous > threshold)) if usable else np.nan
    keys = pd.MultiIndex.from_arrays([days.year, days.month])
    return pd.Series(pd.Series(rates).reindex(keys).to_numpy(), index=days)


def assign_split(target_dates: pd.DatetimeIndex) -> pd.Series:
    """Split name per target date: train, validation, test, or NaN outside all periods."""
    names = pd.Series(np.nan, index=target_dates, dtype=object)
    for name, (first, last) in SPLITS.items():
        inside = (target_dates >= pd.Timestamp(first)) & (target_dates <= pd.Timestamp(last))
        names[inside] = name
    return names
