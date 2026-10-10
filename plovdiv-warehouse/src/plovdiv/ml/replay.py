"""Replay the live model over the days after the test period, out of sample.

    python -m plovdiv.ml.replay                 # fetch the weather once, then score and verify
    python -m plovdiv.ml.replay --no-fetch      # use the landed weather file only

The live model has only scored a handful of real days. This command scores every day from
2026-01-01 up to the day before the first live row of each station with the same inputs a live run
uses, so the model can be judged on several hundred days it has never seen:

* PM10 history: E1a verified daily means to 2025-12-31, then the daily means of the newest landed
  up-to-date (UTD) file, cut at the as-of day (nothing after day t enters the features);
* weather of day t: hourly ECMWF IFS values (the model and grid point of the forecast capture) from the
  Open-Meteo historical forecast API, aggregated with the live rule (`plovdiv.ml.live.daily_weather`).
  The response is landed once under `source=openmeteo/endpoint=historical_forecast/`;
* the live model of the station (`plovdiv.ml.stations.live_model_stem`).

Rows go to `data/live/pm10_risk_replay.parquet` with split `replay` and the issue time of the
evaluation convention (end of the as-of EEA day), then `python -m plovdiv.ml.verify` fills the
outcomes. `python -m plovdiv.ml.marts` publishes them in `mart_aq_risk_daily`.

Difference from a real live run: station values are read as they are published today. Most 2026 hours
have since been verified, while a live run sees them before verification, so the replay can be a
little better than real time. No row is issued in real time, which is why the split is not `live`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from plovdiv.config import settings
from plovdiv.ml import dataset, live, marts, stations, verify

ENDPOINT = "https://historical-forecast-api.open-meteo.com/v1/forecast"
HOURLY = "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,surface_pressure"
MODEL = "ecmwf_ifs025"
FIRST_DAY = pd.Timestamp("2026-01-01")
SPLIT = "replay"
LANDING_GLOB = "source=openmeteo/endpoint=historical_forecast/fetch_date=*/ifs025_*.json.gz"


def fetch_history(landing: Path, start: date, end: date, today: date | None = None) -> Path:
    """Land one historical forecast response for start..end (local days padded by one day each side)."""
    params = {
        "latitude": settings.latitude,
        "longitude": settings.longitude,
        "models": MODEL,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": HOURLY,
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    response = requests.get(ENDPOINT, params=params, headers={"User-Agent": settings.user_agent}, timeout=120)
    response.raise_for_status()
    payload = response.json()
    if "hourly" not in payload:
        raise ValueError(f"historical forecast response has no hourly block: {str(payload)[:200]}")
    payload["_audit"] = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": ENDPOINT,
        "params": params,
    }
    day = (today or datetime.now(timezone.utc).date()).isoformat()
    out = Path(landing) / f"source=openmeteo/endpoint=historical_forecast/fetch_date={day}/ifs025_{start}_{end}.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with gzip.open(tmp, "wb") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    tmp.replace(out)
    return out


def latest_history(landing: Path) -> Path | None:
    found = sorted(Path(landing).glob(LANDING_GLOB))
    return found[-1] if found else None


def replay_station(
    station_id: str,
    document: dict,
    landing: Path,
    weather: pd.DataFrame,
    era5: pd.DataFrame,
    before: pd.Timestamp | None,
) -> tuple[list[dict], dict]:
    """Rows for every as-of day of 2026 whose target day is complete and earlier than `before`."""
    utd_file = live.latest_utd_file(landing, station_id)
    hourly = live.load_utd_hourly(utd_file)
    utd = live.utd_daily_means(hourly)
    closed = live.latest_closed_day(hourly)
    e1a = dataset.load_eea_daily(live.latest_e1a_file(landing, station_id))
    names = list(document["feature_names"])
    threshold = float(document["threshold"]["value"])
    last_target = closed if before is None else min(closed, before - pd.Timedelta(days=1))

    rows, skipped = [], {"no_weather": 0, "missing_feature": 0}
    for asof in pd.date_range(FIRST_DAY - pd.Timedelta(days=1), last_target - pd.Timedelta(days=1), freq="D"):
        weather_t = live.daily_weather(weather, asof)
        if weather_t is None:
            skipped["no_weather"] += 1
            continue
        row = live.live_feature_row(e1a, utd, era5, asof, weather_t)
        if row[names].isna().any():
            skipped["missing_feature"] += 1
            continue
        probability = float(marts.reconstruct_probability(document, row[names].to_numpy(dtype=float)[None, :])[0])
        rows.append(
            {
                "station_id": station_id,
                "target_date": asof + pd.Timedelta(days=1),
                "issued_at": live.eea_day_end_utc(asof),
                "probability": probability,
                "label_id": document["label"]["id"],
                "split": SPLIT,
                "actual_label": None,
                "model_id": document["model"],
                "asof_date": asof,
                "threshold": threshold,
                "predicted_label": int(probability >= threshold),
                "features_json": json.dumps({n: float(row[n]) for n in names}, sort_keys=True),
                "inputs_json": json.dumps(
                    {"utd_file": f"{utd_file.parent.name}/{utd_file.name}", "weather_day_t": weather_t}, sort_keys=True
                ),
            }
        )
    return rows, {"rows": len(rows), "skipped": skipped, "utd_file": f"{utd_file.parent.name}/{utd_file.name}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plovdiv.ml.replay", description=__doc__.splitlines()[0])
    parser.add_argument("--landing-dir", type=Path, default=live.LANDING)
    parser.add_argument("--models-dir", type=Path, default=marts.DEFAULT_MODELS_DIR)
    parser.add_argument("--live-store", type=Path, default=marts.DEFAULT_LIVE_STORE)
    parser.add_argument("--store", type=Path, default=marts.DEFAULT_REPLAY_STORE)
    parser.add_argument("--no-fetch", action="store_true", help="use the newest landed weather response, do not download")
    args = parser.parse_args(argv)

    landing = Path(args.landing_dir)
    live_store = live.read_store(args.live_store)
    chosen = live.stations_to_score(Path(args.models_dir))

    history = latest_history(landing)
    if not args.no_fetch:
        end = datetime.now(timezone.utc).date()
        history = fetch_history(landing, (FIRST_DAY - pd.Timedelta(days=2)).date(), end)
        print(f"landed {history.relative_to(landing)}")
    if history is None:
        print(f"no historical forecast response under {landing}; run without --no-fetch", file=sys.stderr)
        return 2
    weather = live.load_capture(history).hourly
    era5 = dataset.load_weather(live._era5_file(landing))

    rows, report = [], {}
    for station_id in chosen:
        document = marts.read_json(Path(args.models_dir) / f"{stations.live_model_stem(station_id)}.json")
        mine = live_store[live_store["station_id"] == station_id]
        before = pd.Timestamp(mine["target_date"].min()) if len(mine) else None
        station_rows, report[station_id] = replay_station(station_id, document, landing, weather, era5, before)
        rows += station_rows
    if not rows:
        print("no replay rows", file=sys.stderr)
        return 2

    store = live.normalise_store(pd.DataFrame(rows))
    verified_at = pd.Timestamp.now(tz="UTC").floor("s")
    store, summary = verify.verify_store(store, landing, verified_at)
    live.write_store(args.store, store)
    for station_id, entry in report.items():
        print(f"{station_id}: {entry['rows']} rows, skipped {entry['skipped']}")
    for card in verify.scorecard(store):
        print(
            f"  {card['station_id']} {card['model_id']}: {card['correct']} of {card['decided']} calls right, "
            f"{card['bad_days_flagged']} of {card['bad_days']} bad days flagged, {card['false_alarms']} false alarms, "
            f"Brier {card['brier']}"
        )
    print(f"store: {args.store}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
