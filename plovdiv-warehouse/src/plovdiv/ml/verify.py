"""Fill in the actual outcome of live risk rows once their target day is complete.

    python -m plovdiv.ml.verify

For every row of the live store (`data/live/pm10_risk_live.parquet`) whose target day is a complete
EEA day in the newest landed up-to-date (UTD) file of its station:

* `actual_pm10` is the daily mean of the valid hours (Validity 1, non-negative value), published only
  when at least 18 hours are valid, the rule of `mart_aq_daily.is_valid_day` and of the live row itself;
* `label_threshold_ugm3` is the label threshold of the target month, computed by
  `plovdiv.ml.dataset.monthly_thresholds` on the same E1a plus UTD record the live row used;
* `actual_label` is 1 when `actual_pm10` is above that threshold, 0 when not, and stays NULL when the
  day has fewer than 18 valid hours;
* `actual_valid_hours`, `actual_verification` (verified, preliminary or unverified, the weakest valid
  hour decides) and `verified_at` (the time of this run) record what the outcome rests on.

Rows are checked again on every run, so a value that the reporting country corrects or verifies
later replaces the earlier one. The command only rewrites the store. Run it before
`python -m plovdiv.ml.marts`, which publishes the store in `mart_aq_risk_daily` with the outcome.

The same rule fills the replay store of `python -m plovdiv.ml.replay` (`--store`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from plovdiv.ml import dataset, live, marts


def station_outcomes(landing: Path, station_id: str) -> tuple[pd.DataFrame, pd.Series] | None:
    """Daily means of the newest UTD file of a station and the PM10 record used for thresholds.

    Returns None when no UTD file of the station is landed.
    """
    try:
        utd_file = live.latest_utd_file(landing, station_id)
    except live.LiveInputError:
        return None
    hourly = live.load_utd_hourly(utd_file)
    means = live.utd_daily_means(hourly)
    closed = live.latest_closed_day(hourly)
    means = means[means.index <= closed]
    try:
        e1a = dataset.load_eea_daily(live.latest_e1a_file(landing, station_id))
    except live.LiveInputError:
        e1a = pd.Series(dtype=float, index=pd.DatetimeIndex([], dtype="datetime64[ns]"))
    record = live.combined_pm10(e1a, means)
    return means, record


def verify_store(store: pd.DataFrame, landing: Path, verified_at: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Store with the outcome columns filled where the target day is complete. Returns it and a summary."""
    store = live.normalise_store(store.copy())
    summary = {"rows": int(len(store)), "decided": 0, "undecided_day_incomplete": 0, "waiting": 0, "changed": [], "stations": {}}
    for station_id, rows in store.groupby("station_id"):
        found = station_outcomes(landing, station_id)
        if found is None:
            summary["stations"][station_id] = "no UTD file landed"
            summary["waiting"] += int(len(rows))
            continue
        means, record = found
        thresholds = dataset.monthly_thresholds(record)
        targets = pd.DatetimeIndex(rows["target_date"]).astype("datetime64[ns]")
        threshold = dataset.threshold_for_days(targets, thresholds).to_numpy()
        for position, (index, target) in enumerate(zip(rows.index, targets)):
            if target not in means.index:
                summary["waiting"] += 1
                continue
            day = means.loc[target]
            value = day["pm10"]
            limit = threshold[position]
            label = pd.NA if pd.isna(value) or pd.isna(limit) else int(value > limit)
            before = store.at[index, "actual_label"]
            after = None if pd.isna(label) else int(label)
            if not pd.isna(before) and int(before) != after:
                summary["changed"].append(
                    {"station_id": station_id, "target_date": target.date().isoformat(), "from": int(before), "to": after}
                )
            store.at[index, "actual_pm10"] = np.nan if pd.isna(value) else round(float(value), 3)
            store.at[index, "actual_valid_hours"] = int(day["valid_hours"])
            store.at[index, "actual_verification"] = str(day["verification"])
            store.at[index, "label_threshold_ugm3"] = np.nan if pd.isna(limit) else float(limit)
            store.at[index, "actual_label"] = label
            store.at[index, "verified_at"] = verified_at
            if pd.isna(label):
                summary["undecided_day_incomplete"] += 1
            else:
                summary["decided"] += 1
        summary["stations"].setdefault(station_id, "checked")
    return live.normalise_store(store), summary


def scorecard(store: pd.DataFrame) -> list[dict]:
    """Per station and model: decided rows, hits, misses, false alarms and the Brier score."""
    out = []
    decided = store[store["actual_label"].notna()]
    for (station_id, model_id), rows in decided.groupby(["station_id", "model_id"]):
        y = rows["actual_label"].astype(int).to_numpy()
        p = rows["probability"].astype(float).to_numpy()
        predicted = rows["predicted_label"].astype(int).to_numpy()
        out.append(
            {
                "station_id": station_id,
                "model_id": model_id,
                "decided": int(len(rows)),
                "correct": int((predicted == y).sum()),
                "bad_days": int(y.sum()),
                "bad_days_flagged": int(((predicted == 1) & (y == 1)).sum()),
                "false_alarms": int(((predicted == 1) & (y == 0)).sum()),
                "brier": round(float(np.mean((p - y) ** 2)), 4),
                "roc_auc": round(float(roc_auc_score(y, p)), 4) if 0 < y.sum() < len(y) else None,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plovdiv.ml.verify", description=__doc__.splitlines()[0])
    parser.add_argument("--landing-dir", type=Path, default=live.LANDING)
    parser.add_argument("--store", type=Path, default=marts.DEFAULT_LIVE_STORE, help="live (or replay) store to verify")
    parser.add_argument("--verified-at", default=None, help="time of the check, ISO-8601 (default: now, UTC)")
    args = parser.parse_args(argv)

    if not Path(args.store).exists():
        print(f"no store at {args.store}; nothing to verify")
        return 0
    verified_at = pd.Timestamp(args.verified_at) if args.verified_at else pd.Timestamp.now(tz="UTC")
    if verified_at.tzinfo is None:
        parser.error("--verified-at needs a time zone")
    verified_at = verified_at.tz_convert("UTC").floor("s")

    store, summary = verify_store(live.read_store(args.store), args.landing_dir, verified_at)
    live.write_store(args.store, store)
    print(
        f"verified {summary['decided']} of {summary['rows']} rows; {summary['waiting']} wait for their target day, "
        f"{summary['undecided_day_incomplete']} have a target day with fewer than {live.MIN_VALID_HOURS} valid hours"
    )
    for change in summary["changed"]:
        print(f"  outcome changed: {change}")
    for card in scorecard(store):
        print(
            f"  {card['station_id']} {card['model_id']}: {card['correct']} of {card['decided']} calls right, "
            f"{card['bad_days_flagged']} of {card['bad_days']} bad days flagged, {card['false_alarms']} false alarms, "
            f"Brier {card['brier']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
