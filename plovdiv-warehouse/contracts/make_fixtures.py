"""Deterministic fixtures generator for Plovdiv weather and air quality warehouse contracts.

Generates:
1. Valid fixtures under contracts/fixtures/ (all synthetic):
   - manifest.json
   - README.md
   - mart_weather_aq_hourly/latest/mart_weather_aq_hourly.parquet
   - mart_aq_daily/latest/mart_aq_daily.parquet
   - mart_aq_trend_monthly/latest/mart_aq_trend_monthly.parquet
   - mart_aq_risk_daily/latest/mart_aq_risk_daily.parquet
   - mart_aq_risk_drivers/latest/mart_aq_risk_drivers.parquet
   - mart_model_card/latest/mart_model_card.parquet
   - mart_model_calibration/latest/mart_model_calibration.parquet
2. Deliberately mismatched fixtures under contracts/fixtures_invalid/:
   - manifest.json (fails manifest schema validation)
   - mart_weather_aq_hourly/latest/mart_weather_aq_hourly.parquet (fails Pandera validation)
   - mart_aq_daily/latest/mart_aq_daily.parquet (fails Pandera validation)
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path
import random

# Ensure project root is on sys.path when invoked directly as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from contracts.version import SCHEMA_VERSION
except ImportError:
    from version import SCHEMA_VERSION

HOURLY_PYARROW_SCHEMA = pa.schema([
    ("valid_at_utc", pa.timestamp("us", tz="UTC")),
    ("location_id", pa.string()),
    ("source_type", pa.string()),
    ("pm2_5", pa.float64()),
    ("pm10", pa.float64()),
    ("no2", pa.float64()),
    ("temperature_2m", pa.float64()),
    ("wind_gusts_10m", pa.float64()),
    ("precipitation", pa.float64()),
])

DAILY_PYARROW_SCHEMA = pa.schema([
    ("day", pa.date32()),
    ("location_id", pa.string()),
    ("pollutant", pa.string()),
    ("daily_mean", pa.float64()),
    ("valid_hours", pa.int64()),
    ("is_valid_day", pa.bool_()),
])

# Optional marts: air-quality trend, exceedance risk, model card and calibration.
# All values are synthetic. They are plausible in shape but are not measurements or results of a trained model.

STATIONS = {
    "BG0051A": {"label": "Kamenitsa", "station_type": "background", "area_type": "urban", "location_id": "plovdiv"},
    "BG0078A": {"label": "Trakia", "station_type": "traffic", "area_type": "urban", "location_id": "plovdiv"},
}
MODEL_ID = "pm10_exceed_gbm_v1"
PRIOR_MODEL_ID = "pm10_exceed_logreg_v0"
LABEL_ID = "pm10_daily_mean_gt_50"
LABEL_TEXT = "Daily mean PM10 above the EU daily limit value of 50 ug/m3 on the target day (EEA day, valid days only)"
MODEL_FEATURES = [
    "pm10_lag_1d",
    "pm10_mean_7d",
    "no2_lag_1d",
    "temperature_2m_forecast_mean",
    "wind_gusts_10m_forecast_max",
    "precipitation_forecast_sum",
    "is_heating_season",
    "is_weekend",
]
PRIOR_MODEL_FEATURES = ["pm10_lag_1d", "pm10_mean_7d", "is_heating_season", "is_weekend"]
DRIVERS_PUBLISHED = 5

TREND_PYARROW_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    ("month", pa.date32()),
    ("observed_mean_ugm3", pa.float64()),
    ("weather_adjusted_mean_ugm3", pa.float64()),
    ("adjusted_lo_ugm3", pa.float64()),
    ("adjusted_hi_ugm3", pa.float64()),
    ("n_days", pa.int64()),
    ("pollutant", pa.string()),
])

RISK_PYARROW_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    ("target_date", pa.date32()),
    ("issued_at", pa.timestamp("us", tz="UTC")),
    ("probability", pa.float64()),
    ("label_id", pa.string()),
    ("split", pa.string()),
    ("actual_label", pa.int64()),
    ("model_id", pa.string()),
])

DRIVERS_PYARROW_SCHEMA = pa.schema([
    ("station_id", pa.string()),
    ("target_date", pa.date32()),
    ("model_id", pa.string()),
    ("feature", pa.string()),
    ("contribution", pa.float64()),
    ("rank", pa.int64()),
])

MODEL_CARD_PYARROW_SCHEMA = pa.schema([
    ("model_id", pa.string()),
    ("algorithm", pa.string()),
    ("trained_at", pa.timestamp("us", tz="UTC")),
    ("train_start", pa.date32()),
    ("train_end", pa.date32()),
    ("validation_start", pa.date32()),
    ("validation_end", pa.date32()),
    ("test_start", pa.date32()),
    ("test_end", pa.date32()),
    ("label_id", pa.string()),
    ("label_text", pa.string()),
    ("features", pa.list_(pa.string())),
    ("roc_auc_test", pa.float64()),
    ("pr_auc_test", pa.float64()),
    ("brier_test", pa.float64()),
    ("baseline_roc_auc_test_climatology", pa.float64()),
    ("baseline_roc_auc_test_persistence", pa.float64()),
    ("threshold", pa.float64()),
    ("precision_test", pa.float64()),
    ("recall_test", pa.float64()),
    ("uses_forecast_weather", pa.bool_()),
    ("limitations", pa.string()),
])

CALIBRATION_PYARROW_SCHEMA = pa.schema([
    ("model_id", pa.string()),
    ("bin_lo", pa.float64()),
    ("bin_hi", pa.float64()),
    ("predicted_mean", pa.float64()),
    ("observed_rate", pa.float64()),
    ("n", pa.int64()),
])

FIXTURE_README = """# Contract fixtures

Everything in this directory is synthetic. The numbers are plausible in shape, so that
downstream code can be developed and tested, but they are not measurements, and the model
metrics, probabilities, driver contributions and calibration bins are not the results of a
trained model.

The files are produced by `contracts/make_fixtures.py` with a fixed seed. A test compares the
checked-in bytes with a fresh run, so edit the generator and regenerate rather than editing a
file here.

## Contents

| Path | Rows | What it holds |
|---|---|---|
| `manifest.json` | | Run metadata, variable catalogue, station catalogue and mart globs |
| `mart_weather_aq_hourly/` | {hourly} | 14 days of hourly values for `plovdiv` (2026-10-14 to 2026-10-27) |
| `mart_aq_daily/` | {daily} | Daily means per pollutant derived from the hourly mart |
| `mart_aq_trend_monthly/` | {trend} | Monthly observed and weather-adjusted means for two stations |
| `mart_aq_risk_daily/` | {risk} | Daily exceedance probabilities for two stations |
| `mart_aq_risk_drivers/` | {drivers} | Top 5 feature contributions per test and live prediction |
| `mart_model_card/` | {card} | One row per model |
| `mart_model_calibration/` | {calibration} | Ten calibration bins per model |

## Edge cases kept on purpose

- Hourly: a six-hour gap on 2026-10-20, scattered NULLs, and the 25-hour Europe/Sofia day on 2026-10-25.
- Daily: 2026-10-23 has 17 valid PM10 hours, so it is not a valid day.
- Trend: `BG0078A` (traffic) has fewer rows than `BG0051A`. It has a month with no valid days
  (all means NULL, `n_days` 0) and a month with low coverage and a wide interval.
  The partial month 2026-10 is included.
- Risk: `BG0078A` has no train or validation rows. `actual_label` is NULL for the two newest live
  days and for 2026-10-23, which has no valid PM10 day. Keys are dates, so 2026-10-25 is a single
  ordinary row per station; `issued_at` of the following day is shown as 08:00 +02:00 in
  Europe/Sofia, after the clock change.
- Model card: the older model has no persistence baseline (NULL) and no rows in the risk mart.
- Calibration: the last bin of the current model is empty (`n` 0, NULL rates).

`fixtures_invalid/` holds deliberately wrong hourly and daily data and a wrong manifest. It is
used only to prove that validation fails.
"""


def generate_hourly_fixture(seed: int = 42) -> pd.DataFrame:
    """Generate 14 days of hourly weather and air quality data ending 2026-10-27."""
    rng = random.Random(seed)
    np_rng = np.random.RandomState(seed)

    timestamps = pd.date_range("2026-10-14 00:00:00", "2026-10-27 23:00:00", freq="h", tz="UTC")
    total_hours = len(timestamps)

    rows = []
    for i, ts in enumerate(timestamps):
        hour_of_day = ts.hour
        day_str = ts.strftime("%Y-%m-%d")

        # 6-hour continuous gap scenario: 2026-10-20 from 06:00:00Z to 11:00:00Z inclusive
        is_6h_gap = (day_str == "2026-10-20") and (6 <= hour_of_day <= 11)

        if is_6h_gap:
            # During the gap, all measures are true NULL
            row = {
                "valid_at_utc": ts,
                "location_id": "plovdiv",
                "source_type": "analysis",
                "pm2_5": None,
                "pm10": None,
                "no2": None,
                "temperature_2m": None,
                "wind_gusts_10m": None,
                "precipitation": None,
            }
        else:
            # Diurnal curves with noise
            sin_phase = math.sin((hour_of_day - 6) * 2 * math.pi / 24)
            cos_phase = math.cos((hour_of_day - 4) * 2 * math.pi / 24)

            temp = round(14.0 + 6.0 * sin_phase + float(np_rng.normal(0, 0.5)), 2)
            pm25 = round(max(2.0, 15.0 + 8.0 * cos_phase + float(np_rng.normal(0, 1.2))), 2)
            pm10 = round(pm25 + max(5.0, 12.0 + 4.0 * cos_phase + float(np_rng.normal(0, 1.5))), 2)
            no2 = round(max(3.0, 20.0 + 10.0 * cos_phase + float(np_rng.normal(0, 1.8))), 2)
            wind = round(max(0.5, 3.2 + 2.0 * sin_phase + float(np_rng.normal(0, 0.4))), 2)

            # Precipitation: mostly 0.0 (true zero), occasional rain
            if day_str in ["2026-10-18", "2026-10-26"] and 13 <= hour_of_day <= 16:
                precip = round(max(0.2, float(np_rng.uniform(0.4, 2.8))), 2)
            else:
                precip = 0.0

            # Scattered NULL values on other days:
            # 1. 2026-10-16 at 03:00:00Z: pm2_5 is NULL
            if day_str == "2026-10-16" and hour_of_day == 3:
                pm25 = None

            # 2. 2026-10-17 at 12:00:00Z: precipitation is NULL (distinguishes NULL from 0.0)
            if day_str == "2026-10-17" and hour_of_day == 12:
                precip = None

            # 3. 2026-10-22 at 14:00:00Z: wind_gusts_10m is NULL
            if day_str == "2026-10-22" and hour_of_day == 14:
                wind = None

            # 4. 2026-10-23 at 7 scattered non-consecutive hours: pm10 is NULL
            # This creates a day with valid_hours = 17 and is_valid_day = False for pm10
            # without creating a second 6-hour contiguous gap
            if day_str == "2026-10-23" and hour_of_day in [1, 4, 7, 10, 13, 16, 19]:
                pm10 = None

            row = {
                "valid_at_utc": ts,
                "location_id": "plovdiv",
                "source_type": "analysis",
                "pm2_5": pm25,
                "pm10": pm10,
                "no2": no2,
                "temperature_2m": temp,
                "wind_gusts_10m": wind,
                "precipitation": precip,
            }

        rows.append(row)

    df = pd.DataFrame(rows)
    # Sort deterministically
    df = df.sort_values(by=["location_id", "source_type", "valid_at_utc"]).reset_index(drop=True)
    return df


def generate_daily_fixture(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """Generate daily aggregated metrics for pollutants following EEA day convention (UTC+1)."""
    pollutants = ["pm2_5", "pm10", "no2"]

    # Extract calendar date (EEA day convention UTC+1: 24 UTC hours map to the date)
    df_copy = hourly_df.copy()
    df_copy["day"] = df_copy["valid_at_utc"].dt.date

    daily_rows = []
    unique_days = sorted(df_copy["day"].unique())

    for day in unique_days:
        day_slice = df_copy[df_copy["day"] == day]
        location_id = "plovdiv"

        for poll in pollutants:
            valid_vals = day_slice[poll].dropna()
            valid_hours = int(len(valid_vals))
            daily_mean = round(float(valid_vals.mean()), 2) if valid_hours > 0 else None
            is_valid_day = bool(valid_hours >= 18)

            daily_rows.append({
                "day": day,
                "location_id": location_id,
                "pollutant": poll,
                "daily_mean": daily_mean,
                "valid_hours": valid_hours,
                "is_valid_day": is_valid_day,
            })

    daily_df = pd.DataFrame(daily_rows)
    daily_df = daily_df.sort_values(by=["day", "location_id", "pollutant"]).reset_index(drop=True)
    return daily_df


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def generate_trend_fixture(seed: int = 42) -> pd.DataFrame:
    """Monthly trend rows: 18 months for BG0051A (PM10 and PM2.5), 14 months for BG0078A (PM10 only).

    BG0078A has one month without valid days (all means NULL) and one month with low coverage.
    The last month (2026-10) is partial.
    """
    np_rng = np.random.RandomState(seed + 11)
    months = pd.period_range("2025-05", "2026-10", freq="M")

    # (station, pollutant, first month, annual mean, seasonal amplitude)
    series = [
        ("BG0051A", "pm10", "2025-05", 29.0, 13.0),
        ("BG0051A", "pm2_5", "2025-05", 19.0, 10.0),
        ("BG0078A", "pm10", "2025-09", 37.0, 14.0),
    ]
    rows = []
    for station_id, pollutant, first_month, level, amplitude in series:
        for period in months:
            if period < pd.Period(first_month, freq="M"):
                continue
            seasonal = level + amplitude * math.cos(2 * math.pi * (period.month - 1) / 12)
            noise = float(np_rng.normal(0, 1.4))
            weather_effect = float(np_rng.normal(0, 2.0))
            days_in_month = period.days_in_month
            n_days = int(np_rng.randint(max(days_in_month - 6, 20), days_in_month + 1))
            half_width = float(abs(np_rng.normal(2.0, 0.3)))

            if station_id == "BG0078A" and period == pd.Period("2026-03", freq="M"):
                n_days = 0  # instrument outage: no valid day in the month
            if station_id == "BG0078A" and period == pd.Period("2026-02", freq="M"):
                n_days = 9  # low coverage: wide interval
                half_width = 5.5
            if period == pd.Period("2026-10", freq="M"):
                n_days = 13  # partial month: the data ends on 2026-10-27

            if n_days == 0:
                observed = adjusted = lo = hi = None
            else:
                observed = round(seasonal + noise, 2)
                adjusted = round(max(0.5, observed - weather_effect), 2)
                lo = round(max(0.0, adjusted - half_width), 2)
                hi = round(adjusted + half_width, 2)
            rows.append({
                "station_id": station_id,
                "month": datetime.date(period.year, period.month, 1),
                "observed_mean_ugm3": observed,
                "weather_adjusted_mean_ugm3": adjusted,
                "adjusted_lo_ugm3": lo,
                "adjusted_hi_ugm3": hi,
                "n_days": n_days,
                "pollutant": pollutant,
            })
    df = pd.DataFrame(rows)
    return df.sort_values(by=["station_id", "pollutant", "month"]).reset_index(drop=True)


def _heating_effect(month: int) -> float:
    if month in (11, 12, 1, 2, 3):
        return 0.6
    if month == 10:
        return 0.2
    if month == 4:
        return 0.0
    return -0.3


def generate_risk_fixtures(daily_df: pd.DataFrame, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Risk rows and the matching driver rows.

    A day without a valid PM10 value in mart_aq_daily has no outcome (actual_label is NULL), and so do
    the two newest live days. Drivers are published for the test and live splits only.
    """
    np_rng = np.random.RandomState(seed + 23)

    undecidable = set(
        daily_df.loc[(daily_df["pollutant"] == "pm10") & (~daily_df["is_valid_day"]), "day"]
    )
    windows = {
        "BG0051A": [
            ("train", datetime.date(2025, 6, 24), datetime.date(2025, 6, 30)),
            ("validation", datetime.date(2025, 12, 25), datetime.date(2025, 12, 31)),
            ("test", datetime.date(2026, 9, 23), datetime.date(2026, 10, 13)),
            ("live", datetime.date(2026, 10, 14), datetime.date(2026, 10, 28)),
        ],
        # The traffic station joined later: it has no train or validation rows.
        "BG0078A": [
            ("test", datetime.date(2026, 10, 1), datetime.date(2026, 10, 13)),
            ("live", datetime.date(2026, 10, 14), datetime.date(2026, 10, 28)),
        ],
    }
    station_offset = {"BG0051A": 0.0, "BG0078A": 0.35}
    intercept = -1.4
    first_unknown_day = datetime.date(2026, 10, 27)

    risk_rows = []
    driver_rows = []
    for station_id in sorted(windows):
        for split, first, last in windows[station_id]:
            day = first
            while day <= last:
                contributions = {
                    "pm10_lag_1d": float(np_rng.normal(0.5, 0.6)),
                    "pm10_mean_7d": float(np_rng.normal(0.3, 0.4)),
                    "no2_lag_1d": float(np_rng.normal(0.1, 0.25)),
                    "temperature_2m_forecast_mean": float(np_rng.normal(0.0, 0.4)),
                    "wind_gusts_10m_forecast_max": float(np_rng.normal(-0.4, 0.5)),
                    "precipitation_forecast_sum": float(np_rng.normal(-0.2, 0.3)),
                    "is_heating_season": _heating_effect(day.month),
                    "is_weekend": -0.15 if day.weekday() >= 5 else 0.05,
                }
                contributions = {k: round(v, 3) for k, v in contributions.items()}
                logit = intercept + station_offset[station_id] + sum(contributions.values())
                probability = round(_sigmoid(logit), 4)
                draw = float(np_rng.uniform())

                if day >= first_unknown_day or day in undecidable:
                    actual = None
                else:
                    actual = int(draw < probability)

                issued_at = pd.Timestamp(
                    datetime.datetime(day.year, day.month, day.day, 6, 0, 0, tzinfo=datetime.timezone.utc)
                ) - pd.Timedelta(days=1)
                risk_rows.append({
                    "station_id": station_id,
                    "target_date": day,
                    "issued_at": issued_at,
                    "probability": probability,
                    "label_id": LABEL_ID,
                    "split": split,
                    "actual_label": actual,
                    "model_id": MODEL_ID,
                })

                if split in ("test", "live"):
                    ranked = sorted(contributions.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
                    for rank, (feature, value) in enumerate(ranked[:DRIVERS_PUBLISHED], start=1):
                        driver_rows.append({
                            "station_id": station_id,
                            "target_date": day,
                            "model_id": MODEL_ID,
                            "feature": feature,
                            "contribution": value,
                            "rank": rank,
                        })
                day += datetime.timedelta(days=1)

    risk_df = pd.DataFrame(risk_rows)
    risk_df = risk_df.sort_values(by=["station_id", "target_date", "model_id"]).reset_index(drop=True)
    drivers_df = pd.DataFrame(driver_rows)
    drivers_df = drivers_df.sort_values(by=["station_id", "target_date", "model_id", "rank"]).reset_index(drop=True)
    return risk_df, drivers_df


def generate_model_card_fixture() -> pd.DataFrame:
    """Two model cards: the current model and an older, simpler one that no longer produces predictions."""
    utc = datetime.timezone.utc
    rows = [
        {
            "model_id": MODEL_ID,
            "algorithm": "gradient_boosted_trees",
            "trained_at": pd.Timestamp(datetime.datetime(2025, 12, 31, 21, 15, 0, tzinfo=utc)),
            "train_start": datetime.date(2022, 1, 1),
            "train_end": datetime.date(2025, 6, 30),
            "validation_start": datetime.date(2025, 7, 1),
            "validation_end": datetime.date(2025, 12, 31),
            "test_start": datetime.date(2026, 1, 1),
            "test_end": datetime.date(2026, 10, 13),
            "label_id": LABEL_ID,
            "label_text": LABEL_TEXT,
            "features": list(MODEL_FEATURES),
            "roc_auc_test": 0.842,
            "pr_auc_test": 0.611,
            "brier_test": 0.118,
            "baseline_roc_auc_test_climatology": 0.676,
            "baseline_roc_auc_test_persistence": 0.771,
            "threshold": 0.35,
            "precision_test": 0.58,
            "recall_test": 0.71,
            "uses_forecast_weather": True,
            "limitations": (
                "Trained on two stations in one city; performance elsewhere is unknown. "
                "Archived weather forecasts exist only from 2024, so earlier training rows use analysis values "
                "in place of forecasts. Days with fewer than 18 valid hourly values have no label and are excluded. "
                "Few test days have a predicted probability above 0.8, so the upper calibration bins are uncertain."
            ),
        },
        {
            "model_id": PRIOR_MODEL_ID,
            "algorithm": "logistic_regression",
            "trained_at": pd.Timestamp(datetime.datetime(2025, 6, 30, 20, 0, 0, tzinfo=utc)),
            "train_start": datetime.date(2022, 1, 1),
            "train_end": datetime.date(2024, 12, 31),
            "validation_start": datetime.date(2025, 1, 1),
            "validation_end": datetime.date(2025, 6, 30),
            "test_start": datetime.date(2025, 7, 1),
            "test_end": datetime.date(2025, 12, 31),
            "label_id": LABEL_ID,
            "label_text": LABEL_TEXT,
            "features": list(PRIOR_MODEL_FEATURES),
            "roc_auc_test": 0.781,
            "pr_auc_test": 0.52,
            "brier_test": 0.139,
            "baseline_roc_auc_test_climatology": 0.668,
            "baseline_roc_auc_test_persistence": None,
            "threshold": 0.4,
            "precision_test": 0.55,
            "recall_test": 0.62,
            "uses_forecast_weather": False,
            "limitations": (
                "Superseded by a model that uses weather forecasts. Uses no weather inputs, so it cannot anticipate "
                "a change in wind or rain. The persistence baseline was not computed for this model."
            ),
        },
    ]
    df = pd.DataFrame(rows)
    return df.sort_values(by=["model_id"]).reset_index(drop=True)


def generate_calibration_fixture() -> pd.DataFrame:
    """Ten reliability bins per model, built from fixed counts. The last bin of the current model is empty."""
    specs = {
        MODEL_ID: {
            "n": [238, 104, 66, 47, 33, 25, 16, 9, 3, 0],
            "mean_offset": [-0.02, -0.01, 0.0, 0.01, 0.0, -0.01, 0.02, 0.0, 0.01, 0.0],
            "rate_gap": [0.0, 0.01, -0.01, 0.03, 0.04, 0.0, -0.06, -0.1, -0.2, 0.0],
        },
        PRIOR_MODEL_ID: {
            "n": [150, 90, 60, 45, 30, 20, 12, 8, 4, 1],
            "mean_offset": [-0.01, 0.0, 0.01, 0.0, -0.01, 0.01, 0.0, -0.02, 0.0, 0.0],
            "rate_gap": [0.02, 0.0, -0.02, -0.03, 0.05, 0.02, -0.08, -0.12, -0.1, 0.0],
        },
    }
    rows = []
    for model_id, spec in specs.items():
        for i in range(10):
            lo = round(i * 0.1, 1)
            hi = round(lo + 0.1, 1)
            n = spec["n"][i]
            if n == 0:
                predicted = observed = None
            else:
                predicted = round(lo + 0.05 + spec["mean_offset"][i], 4)
                positives = int(round(n * min(1.0, max(0.0, predicted + spec["rate_gap"][i]))))
                observed = round(positives / n, 4)
            rows.append({
                "model_id": model_id,
                "bin_lo": lo,
                "bin_hi": hi,
                "predicted_mean": predicted,
                "observed_rate": observed,
                "n": n,
            })
    df = pd.DataFrame(rows)
    return df.sort_values(by=["model_id", "bin_lo"]).reset_index(drop=True)


def generate_optional_marts(daily_df: pd.DataFrame, seed: int = 42) -> dict[str, pd.DataFrame]:
    """Generate the five optional marts."""
    trend_df = generate_trend_fixture(seed=seed)
    risk_df, drivers_df = generate_risk_fixtures(daily_df, seed=seed)
    risk_df["actual_label"] = risk_df["actual_label"].astype("Int64")
    return {
        "mart_aq_trend_monthly": trend_df,
        "mart_aq_risk_daily": risk_df,
        "mart_aq_risk_drivers": drivers_df,
        "mart_model_card": generate_model_card_fixture(),
        "mart_model_calibration": generate_calibration_fixture(),
    }


def generate_manifest(
    hourly_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    optional_marts: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """Generate manifest.json describing execution metadata and mart outputs.

    optional_marts maps the names of the optional marts to their data. The stations catalogue is added
    together with them.
    """
    min_utc = hourly_df["valid_at_utc"].min().strftime("%Y-%m-%dT%H:%M:%SZ")
    max_utc = hourly_df["valid_at_utc"].max().strftime("%Y-%m-%dT%H:%M:%SZ")
    hourly_entry = {
        "glob": "mart_weather_aq_hourly/latest/*.parquet",
        "rows": int(len(hourly_df)),
        "min_valid_at_utc": min_utc,
        "max_valid_at_utc": max_utc,
    }
    analysis = hourly_df.loc[hourly_df["source_type"] == "analysis", "valid_at_utc"]
    if len(analysis):
        hourly_entry["max_analysis_valid_at_utc"] = analysis.max().strftime("%Y-%m-%dT%H:%M:%SZ")
    daily_entry = {
        "glob": "mart_aq_daily/latest/*.parquet",
        "rows": int(len(daily_df)),
    }
    if len(daily_df):
        daily_entry["min_day"] = daily_df["day"].min().isoformat()
        daily_entry["max_day"] = daily_df["day"].max().isoformat()

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": "2026-10-07T14-00-00Z-abc123",
        "generated_at_utc": "2026-10-07T14:05:12Z",
        "attribution": [
            "Copernicus Atmosphere Monitoring Service (CAMS)",
            "Open-Meteo"
        ],
        "variables": {
            "pm2_5": {"unit": "ug/m3", "label": "PM2.5"},
            "pm10": {"unit": "ug/m3", "label": "PM10"},
            "no2": {"unit": "ug/m3", "label": "NO2"},
            "temperature_2m": {"unit": "degC", "label": "Temperature"},
            "wind_gusts_10m": {"unit": "m/s", "label": "Wind gusts"},
            "precipitation": {"unit": "mm", "label": "Precipitation"}
        },
        "marts": {
            "mart_weather_aq_hourly": hourly_entry,
            "mart_aq_daily": daily_entry,
        }
    }

    if optional_marts:
        manifest["stations"] = {station_id: dict(entry) for station_id, entry in STATIONS.items()}
        trend = optional_marts["mart_aq_trend_monthly"]
        risk = optional_marts["mart_aq_risk_daily"]
        manifest["marts"]["mart_aq_trend_monthly"] = {
            "glob": "mart_aq_trend_monthly/latest/*.parquet",
            "rows": int(len(trend)),
            "min_month": trend["month"].min().isoformat(),
            "max_month": trend["month"].max().isoformat(),
        }
        manifest["marts"]["mart_aq_risk_daily"] = {
            "glob": "mart_aq_risk_daily/latest/*.parquet",
            "rows": int(len(risk)),
            "min_target_date": risk["target_date"].min().isoformat(),
            "max_target_date": risk["target_date"].max().isoformat(),
        }
        for name in ("mart_aq_risk_drivers", "mart_model_card", "mart_model_calibration"):
            manifest["marts"][name] = {
                "glob": f"{name}/latest/*.parquet",
                "rows": int(len(optional_marts[name])),
            }
    return manifest


def generate_invalid_fixtures(invalid_dir: Path) -> None:
    """Write deliberately mismatched fixtures designed to trigger clear validation failures."""
    invalid_dir.mkdir(parents=True, exist_ok=True)

    # 1. Invalid manifest.json (fails JSON Schema Draft-07 validation)
    invalid_manifest = {
        "schema_version": "invalid_version_999",
        "run_id": "",
        "generated_at_utc": "invalid-timestamp",
        "attribution": [],
        "variables": {},
        "marts": {}
    }
    manifest_path = invalid_dir / "manifest.json"
    manifest_path.write_text(json.dumps(invalid_manifest, indent=2) + "\n", encoding="utf-8", newline="\n")

    # 2. Invalid mart_weather_aq_hourly (fails Pandera validation: invalid source_type and negative pm2_5)
    hourly_invalid_data = {
        "valid_at_utc": pd.to_datetime(["2026-10-14 00:00:00", "2026-10-14 01:15:00"], utc=True),
        "location_id": ["plovdiv", "plovdiv"],
        "source_type": ["invalid_sensor_network", "analysis"],
        "pm2_5": [-55.0, 12.0],
        "pm10": [20.0, 25.0],
        "no2": [15.0, 18.0],
        "temperature_2m": [15.0, 16.0],
        "wind_gusts_10m": [2.0, 3.0],
        "precipitation": [0.0, 0.0],
    }
    hourly_invalid_df = pd.DataFrame(hourly_invalid_data)
    hourly_out = invalid_dir / "mart_weather_aq_hourly" / "latest" / "mart_weather_aq_hourly.parquet"
    hourly_out.parent.mkdir(parents=True, exist_ok=True)
    table_hourly_inv = pa.Table.from_pandas(hourly_invalid_df, schema=HOURLY_PYARROW_SCHEMA, preserve_index=False)
    pq.write_table(table_hourly_inv, hourly_out)

    # 3. Invalid mart_aq_daily (fails Pandera 18-of-24 rule check: valid_hours=10 but is_valid_day=True)
    daily_invalid_data = {
        "day": [datetime.date(2026, 10, 14)],
        "location_id": ["plovdiv"],
        "pollutant": ["invalid_pollutant"],
        "daily_mean": [25.0],
        "valid_hours": [10],
        "is_valid_day": [True],
    }
    daily_invalid_df = pd.DataFrame(daily_invalid_data)
    daily_out = invalid_dir / "mart_aq_daily" / "latest" / "mart_aq_daily.parquet"
    daily_out.parent.mkdir(parents=True, exist_ok=True)
    table_daily_inv = pa.Table.from_pandas(daily_invalid_df, schema=DAILY_PYARROW_SCHEMA, preserve_index=False)
    pq.write_table(table_daily_inv, daily_out)


def write_fixtures(out_dir: Path, invalid_dir: Path, seed: int = 42) -> None:
    """Generate both valid and invalid fixtures deterministically."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Generate dataframes
    hourly_df = generate_hourly_fixture(seed=seed)
    daily_df = generate_daily_fixture(hourly_df)
    optional_marts = generate_optional_marts(daily_df, seed=seed)
    manifest = generate_manifest(hourly_df, daily_df, optional_marts)

    # Write valid Parquet marts
    hourly_path = out_dir / "mart_weather_aq_hourly" / "latest" / "mart_weather_aq_hourly.parquet"
    hourly_path.parent.mkdir(parents=True, exist_ok=True)
    hourly_table = pa.Table.from_pandas(hourly_df, schema=HOURLY_PYARROW_SCHEMA, preserve_index=False)
    pq.write_table(hourly_table, hourly_path)

    daily_path = out_dir / "mart_aq_daily" / "latest" / "mart_aq_daily.parquet"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_table = pa.Table.from_pandas(daily_df, schema=DAILY_PYARROW_SCHEMA, preserve_index=False)
    pq.write_table(daily_table, daily_path)

    # Write the optional marts
    optional_schemas = {
        "mart_aq_trend_monthly": TREND_PYARROW_SCHEMA,
        "mart_aq_risk_daily": RISK_PYARROW_SCHEMA,
        "mart_aq_risk_drivers": DRIVERS_PYARROW_SCHEMA,
        "mart_model_card": MODEL_CARD_PYARROW_SCHEMA,
        "mart_model_calibration": CALIBRATION_PYARROW_SCHEMA,
    }
    for name, frame in optional_marts.items():
        mart_path = out_dir / name / "latest" / f"{name}.parquet"
        mart_path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(frame, schema=optional_schemas[name], preserve_index=False)
        pq.write_table(table, mart_path)

    # Write valid manifest.json
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")

    # Write the fixture README
    readme = FIXTURE_README.format(
        hourly=len(hourly_df),
        daily=len(daily_df),
        trend=len(optional_marts["mart_aq_trend_monthly"]),
        risk=len(optional_marts["mart_aq_risk_daily"]),
        drivers=len(optional_marts["mart_aq_risk_drivers"]),
        card=len(optional_marts["mart_model_card"]),
        calibration=len(optional_marts["mart_model_calibration"]),
    )
    (out_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")

    # Generate invalid fixtures
    generate_invalid_fixtures(invalid_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic fixtures for Plovdiv warehouse.")
    parser.add_argument("--out-dir", type=Path, default=Path("contracts/fixtures"), help="Directory for valid fixtures.")
    parser.add_argument("--invalid-dir", type=Path, default=Path("contracts/fixtures_invalid"), help="Directory for invalid fixtures.")
    parser.add_argument("--seed", type=int, default=42, help="Fixed random seed for deterministic generation.")
    args = parser.parse_args()

    write_fixtures(out_dir=args.out_dir, invalid_dir=args.invalid_dir, seed=args.seed)
    print(f"Fixtures successfully written to {args.out_dir} and {args.invalid_dir}")


if __name__ == "__main__":
    main()
