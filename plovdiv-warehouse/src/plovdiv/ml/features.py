"""Feature construction for the next-day PM10 risk model.

The model table has one row per as-of day t. Inputs are limited to what is known at the end of
day t:

* PM10 and PM2.5 of day t and earlier (log of the daily mean concentration in ug/m3)
* weather of day t (ERA5 daily values)
* weather of day t + 1 (ERA5 actuals, used as a stand-in for a perfect forecast; real
  forecasts are imperfect, so scores that use these columns are an optimistic upper bound)
* calendar attributes of the target day t + 1 (month, weekend, heating season). The calendar is
  known in advance and the label is defined relative to the month of the target day.

Rolling quantities are computed on the contiguous calendar-day table, so a missing day is a gap
and never silently shifts a window. A rolling mean needs at least 2 of 3 and 4 of 7 valid days.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from plovdiv.ml.dataset import (
    EU_DAILY_LIMIT,
    LABEL_QUANTILE,
    assign_split,
    climatology_rate,
    monthly_thresholds,
    threshold_for_days,
)

PM10_FEATURES = [
    "log_pm10_t",
    "log_pm10_t_minus_1",
    "log_pm10_mean3",
    "log_pm10_mean7",
]
PM25_FEATURES = ["log_pm25_t"]
CALENDAR_FEATURES = ["month_sin", "month_cos", "is_weekend", "heating_season"]
WEATHER_BASE = [
    "temp_mean_c",
    "temp_range_c",
    "log1p_precip_mm",
    "wind_max_ms",
    "radiation_mj_m2",
    "rh_mean_pct",
    "pressure_mean_hpa",
]
WEATHER_T_FEATURES = [f"{name}_t" for name in WEATHER_BASE]
WEATHER_NEXT_FEATURES = [f"{name}_t1" for name in WEATHER_BASE]
LIVE_WEATHER_T_FEATURES = [f"{name}_t" for name in WEATHER_BASE if name != "radiation_mj_m2"]

# Model variants. The first is the primary model; the others are the ablations and the
# PM10-only reference model.
VARIANTS: dict[str, list[str]] = {
    "with_next_day_weather": (
        PM10_FEATURES + PM25_FEATURES + CALENDAR_FEATURES + WEATHER_T_FEATURES + WEATHER_NEXT_FEATURES
    ),
    "no_next_day_weather": (
        PM10_FEATURES + PM25_FEATURES + CALENDAR_FEATURES + WEATHER_T_FEATURES
    ),
    "no_weather": PM10_FEATURES + PM25_FEATURES + CALENDAR_FEATURES,
    "pm10_only": PM10_FEATURES + CALENDAR_FEATURES,
    # Inputs available for a live 2026 row: no PM2.5 (not landed hourly) and no radiation
    # (captured only since 2026-10-08), and no next-day weather.
    "live": PM10_FEATURES + CALENDAR_FEATURES + LIVE_WEATHER_T_FEATURES,
}
PRIMARY_VARIANT = "with_next_day_weather"
DEPLOYABLE_VARIANT = "no_next_day_weather"
# Variant scored by plovdiv.ml.live for the live row; all of its inputs are landed in 2026.
LIVE_VARIANT = "live"


def weather_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Derived weather columns for each calendar day (indexed by that day)."""
    out = pd.DataFrame(index=daily.index)
    out["temp_mean_c"] = daily["temperature_2m_mean"]
    out["temp_range_c"] = daily["temperature_2m_max"] - daily["temperature_2m_min"]
    out["log1p_precip_mm"] = np.log1p(daily["precipitation_sum"])
    out["wind_max_ms"] = daily["wind_speed_10m_max"]
    out["radiation_mj_m2"] = daily["shortwave_radiation_sum"]
    out["rh_mean_pct"] = daily["relative_humidity_2m_mean"]
    out["pressure_mean_hpa"] = daily["surface_pressure_mean"]
    return out


def _log_positive(values: pd.Series) -> pd.Series:
    return np.log(values.where(values > 0))


def build_model_table(daily: pd.DataFrame) -> pd.DataFrame:
    """Features, labels and split for every as-of day t of a contiguous daily table.

    Columns: asof_date (index), target_date, pm10_target, label_threshold, label,
    label_gt50, the feature columns, the persistence flag `bad_today`, the climatology
    probability `climatology_rate` and `split` (by target date; NaN outside all periods).
    """
    if not daily.index.is_monotonic_increasing or daily.index.has_duplicates:
        raise ValueError("daily table must be sorted by day without duplicates")
    expected = pd.date_range(daily.index[0], daily.index[-1], freq="D")
    if len(expected) != len(daily) or not (expected == daily.index).all():
        raise ValueError("daily table must contain every calendar day")

    pm10 = daily["pm10"]
    pm25 = daily["pm25"]
    thresholds = monthly_thresholds(pm10, quantile=LABEL_QUANTILE)

    table = pd.DataFrame(index=daily.index)
    table.index.name = "asof_date"
    target_dates = daily.index + pd.Timedelta(days=1)
    table["target_date"] = target_dates

    # Label and the secondary limit-exceedance flag: both use day t + 1 only as the target.
    target_pm10 = pm10.shift(-1)
    target_threshold = pd.Series(
        threshold_for_days(pd.DatetimeIndex(target_dates), thresholds).to_numpy(),
        index=daily.index,
    )
    table["pm10_target"] = target_pm10
    table["label_threshold"] = target_threshold
    known = target_pm10.notna() & target_threshold.notna()
    table["label"] = (target_pm10 > target_threshold).astype(float).where(known)
    table["label_gt50"] = (target_pm10 > EU_DAILY_LIMIT).astype(float).where(target_pm10.notna())

    # PM features, information up to and including day t.
    table["log_pm10_t"] = _log_positive(pm10)
    table["log_pm10_t_minus_1"] = _log_positive(pm10.shift(1))
    table["log_pm10_mean3"] = _log_positive(pm10.rolling(3, min_periods=2).mean())
    table["log_pm10_mean7"] = _log_positive(pm10.rolling(7, min_periods=4).mean())
    table["log_pm25_t"] = _log_positive(pm25)

    # Calendar attributes of the target day.
    month = pd.DatetimeIndex(target_dates).month.to_numpy()
    table["month_sin"] = np.sin(2 * np.pi * month / 12)
    table["month_cos"] = np.cos(2 * np.pi * month / 12)
    table["is_weekend"] = (pd.DatetimeIndex(target_dates).dayofweek.to_numpy() >= 5).astype(float)
    table["heating_season"] = ((month >= 10) | (month <= 3)).astype(float)

    # Weather of day t and of day t + 1.
    weather = weather_features(daily)
    for name in WEATHER_BASE:
        table[f"{name}_t"] = weather[name]
        table[f"{name}_t1"] = weather[name].shift(-1)

    # Reference quantities for the baselines.
    own_threshold = pd.Series(
        threshold_for_days(daily.index, thresholds).to_numpy(), index=daily.index
    )
    today_known = pm10.notna() & own_threshold.notna()
    table["bad_today"] = (pm10 > own_threshold).astype(float).where(today_known)
    rate = climatology_rate(pm10, thresholds, pd.DatetimeIndex(target_dates))
    table["climatology_rate"] = rate.to_numpy()
    table["split"] = assign_split(pd.DatetimeIndex(target_dates)).to_numpy()
    return table


def usable_rows(table: pd.DataFrame, columns: list[str] | None = None) -> pd.Series:
    """Rows with a label, a split and all of the given feature columns present."""
    columns = columns if columns is not None else VARIANTS[PRIMARY_VARIANT]
    mask = table["label"].notna() & table["split"].notna() & table["label_gt50"].notna()
    return mask & table[columns].notna().all(axis=1)
