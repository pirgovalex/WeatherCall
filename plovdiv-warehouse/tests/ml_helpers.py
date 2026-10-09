"""Synthetic inputs in the format of the landed files, for the risk model tests."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from plovdiv.ml import dataset


def synthetic_days(first="2013-01-01", last="2025-12-31") -> pd.DatetimeIndex:
    return pd.date_range(first, last, freq="D")


def synthetic_series(days: pd.DatetimeIndex, seed: int = 0) -> dict:
    """Seasonal PM10 with day-to-day persistence that depends on wind, plus PM2.5 and weather."""
    rng = np.random.default_rng(seed)
    n = len(days)
    doy = days.dayofyear.to_numpy()
    wind = np.clip(rng.gamma(3.0, 1.0, size=n), 0.5, 15.0)
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.6 * noise[i - 1] + rng.normal(0, 0.25)
    years = (days.year.to_numpy() - 2013) / 12.0
    log_pm10 = 3.3 + 0.45 * np.cos(2 * np.pi * (doy - 15) / 365.25) - 0.25 * years + noise
    log_pm10 = log_pm10 - 0.08 * (wind - 3.0)
    pm10 = np.exp(log_pm10)
    pm25 = pm10 * rng.uniform(0.45, 0.7, size=n)
    temp = 13 + 11 * np.sin(2 * np.pi * (doy - 110) / 365.25) + rng.normal(0, 2.5, size=n)
    return {
        "pm10": pm10,
        "pm25": pm25,
        "weather": {
            "temperature_2m_mean": temp,
            "temperature_2m_max": temp + 4.0,
            "temperature_2m_min": temp - 4.0,
            "precipitation_sum": np.clip(rng.gamma(0.4, 4.0, size=n) - 0.5, 0, None),
            "wind_speed_10m_max": wind,
            "wind_gusts_10m_max": wind * 1.8,
            "wind_direction_10m_dominant": rng.uniform(0, 360, size=n),
            "shortwave_radiation_sum": np.clip(12 + 10 * np.sin(2 * np.pi * (doy - 80) / 365.25), 1, None),
            "relative_humidity_2m_mean": rng.uniform(40, 95, size=n),
            "surface_pressure_mean": 1000 + rng.normal(0, 6, size=n),
            "pressure_msl_mean": 1015 + rng.normal(0, 6, size=n),
            "cloud_cover_mean": rng.uniform(0, 100, size=n),
            "dew_point_2m_mean": temp - 5.0,
        },
    }


def eea_frame(days: pd.DatetimeIndex, values: np.ndarray, code: str, missing_every: int = 37) -> pd.DataFrame:
    keep = np.arange(len(days)) % missing_every != 5
    return pd.DataFrame(
        {
            "Samplingpoint": f"BG/SPO-BG0051A_{code}_100",
            "Pollutant": int(code),
            "Start": days[keep],
            "End": days[keep] + pd.Timedelta(days=1),
            "Value": [f"{v:.18f}" for v in values[keep]],
            "Unit": "ug.m-3",
            "AggType": "day",
            "Validity": 1,
            "Verification": 1,
        }
    )


def write_landed_inputs(root: Path, seed: int = 0) -> tuple[Path, Path]:
    """Write EEA parquet files and an archive response; return (eea_dir, weather_file)."""
    days = synthetic_days()
    data = synthetic_series(days, seed)
    eea_dir = root / "eea"
    eea_dir.mkdir(parents=True, exist_ok=True)
    eea_frame(days, data["pm10"], dataset.PM10_CODE).to_parquet(
        eea_dir / f"SPO-{dataset.STATION_ID}_{dataset.PM10_CODE}_100.parquet", index=False
    )
    eea_frame(days, data["pm25"], dataset.PM25_CODE, missing_every=41).to_parquet(
        eea_dir / f"SPO-{dataset.STATION_ID}_{dataset.PM25_CODE}_100.parquet", index=False
    )
    payload = {
        "latitude": 42.14,
        "longitude": 24.70,
        "elevation": 166.0,
        "timezone": "Europe/Sofia",
        "utc_offset_seconds": 10800,
        "daily_units": {"time": "iso8601"},
        "daily": {
            "time": [d.strftime("%Y-%m-%d") for d in days],
            **{k: [round(float(x), 3) for x in v] for k, v in data["weather"].items()},
        },
    }
    weather_file = root / "archive_synthetic.json.gz"
    with gzip.open(weather_file, "wb") as handle:
        handle.write(json.dumps(payload).encode("utf-8"))
    return eea_dir, weather_file


def synthetic_daily_table(seed: int = 0) -> pd.DataFrame:
    days = synthetic_days()
    data = synthetic_series(days, seed)
    table = pd.DataFrame(index=days.astype("datetime64[ns]"))
    table["pm10"] = data["pm10"]
    table["pm25"] = data["pm25"]
    for name, values in data["weather"].items():
        table[name] = values
    table.index.name = "date"
    return table
