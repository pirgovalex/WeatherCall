"""Export the contract Parquet fixtures to JSON tables for the mock API.

The mock handlers in src/mocks serve these tables through MSW. Nothing is
invented here: every value comes from contracts/fixtures. Re-run this script
after copying a new contract into the repository.

    python -m venv .venv && .venv\\Scripts\\pip install pandas pyarrow
    .venv\\Scripts\\python scripts/export_mock_data.py
"""

from __future__ import annotations

import datetime
import glob
import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "contracts" / "fixtures"
OUT = ROOT / "src" / "mocks" / "data"


def read(mart: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(FIXTURES / mart / "latest" / "*.parquet")))
    if not files:
        raise SystemExit(f"No Parquet file found for {mart}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def clean(value):
    """Convert pandas scalars to plain JSON values; missing becomes None."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if getattr(value, "ndim", 0) > 0:
        return [clean(v) for v in value.tolist()]
    if hasattr(value, "item"):
        return clean(value.item())
    return value


def columns(df: pd.DataFrame) -> dict:
    return {name: [clean(v) for v in df[name]] for name in df.columns}


def write(name: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {name}")


def main() -> None:
    hourly = read("mart_weather_aq_hourly").sort_values(["location_id", "source_type", "valid_at_utc"])
    hourly = hourly.assign(valid_at_utc=hourly["valid_at_utc"].map(lambda t: int(t.timestamp())))
    write("hourly.json", columns(hourly))

    write("daily.json", columns(read("mart_aq_daily").sort_values(["pollutant", "day"])))
    write("trend.json", columns(read("mart_aq_trend_monthly").sort_values(["station_id", "pollutant", "month"])))
    write("risk.json", columns(read("mart_aq_risk_daily").sort_values(["station_id", "target_date", "model_id"])))
    write(
        "drivers.json",
        columns(read("mart_aq_risk_drivers").sort_values(["station_id", "target_date", "model_id", "rank"])),
    )
    write("model_card.json", columns(read("mart_model_card").sort_values("model_id")))
    write("calibration.json", columns(read("mart_model_calibration").sort_values(["model_id", "bin_lo"])))


if __name__ == "__main__":
    main()
