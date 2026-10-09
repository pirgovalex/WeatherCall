"""Find outages in the forecast capture and say what can still be rebuilt.

Input is the run log written by plovdiv.extract.fetch_forecasts. A gap is a stretch in which
a source was not captured for noticeably longer than the schedule interval. For each gap the
report gives the time capture stopped, the time it resumed (or that it has not), and the
window to backfill.

What a gap costs depends on what each snapshot carries:

* Every snapshot includes `past_days` of recent values (3 for the weather model, 7 for CAMS).
  A gap shorter than that is covered for analysis-like values by the next snapshot.
* Forecast vintages issued during a gap are gone. Open-Meteo's Previous Runs API can restore
  some weather leads (day multiples, from 2024). CAMS vintages cannot be restored.
* Longer gaps need the archive APIs (ERA5 / CAMS reanalysis) for the analysis-like values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_OPS_DIR = Path(__file__).resolve().parents[3] / "data" / "ops"
RUN_LOG_NAME = "ingest_runs.jsonl"

# `past_days` requested for each source by the capture script.
PAST_DAYS = {("openmeteo", "forecast"): 3, ("cams", "air_quality"): 7}


@dataclass(frozen=True)
class Gap:
    source: str
    endpoint: str
    last_capture_utc: str
    resumed_at_utc: str | None
    missed_runs: int
    cause: str  # "no_run" (program did not run) or "failed" (it ran and could not capture)
    analysis_recoverable_from_next_snapshot: bool
    backfill_from_utc: str
    backfill_to_utc: str | None


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def load_records(path: Path | str) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                print(f"warning: skipping unreadable line {number} in {path}", file=sys.stderr)
    return records


def find_gaps(
    records: list[dict],
    every: timedelta = timedelta(hours=6),
    tolerance: float = 1.5,
    now: datetime | None = None,
) -> list[Gap]:
    """Gaps per source, oldest first. A gap needs more than `tolerance` x `every` without a capture."""
    now = now or datetime.now(timezone.utc)
    limit = every * tolerance

    by_source: dict[tuple, list[dict]] = {}
    for r in records:
        by_source.setdefault((r["source"], r["endpoint"]), []).append(r)

    gaps: list[Gap] = []
    for (source, endpoint), rows in sorted(by_source.items()):
        rows.sort(key=lambda r: _parse(r["started_at_utc"]))
        captured = [_parse(r["finished_at_utc"]) for r in rows if r.get("landing_file")]
        if not captured:
            continue
        failures = [_parse(r["started_at_utc"]) for r in rows if r.get("status") == "failed"]
        past_days = timedelta(days=PAST_DAYS.get((source, endpoint), 0))

        edges = list(zip(captured, captured[1:]))
        if now - captured[-1] > limit:
            edges.append((captured[-1], None))  # still down

        for start, end in edges:
            stop = end or now
            if stop - start <= limit:
                continue
            cause = "failed" if any(start < t < stop for t in failures) else "no_run"
            gaps.append(
                Gap(
                    source=source,
                    endpoint=endpoint,
                    last_capture_utc=start.isoformat(),
                    resumed_at_utc=end.isoformat() if end else None,
                    missed_runs=max(int(round((stop - start) / every)) - 1, 0),
                    cause=cause,
                    analysis_recoverable_from_next_snapshot=bool(end) and (end - start) <= past_days,
                    backfill_from_utc=start.isoformat(),
                    backfill_to_utc=end.isoformat() if end else None,
                )
            )
    return gaps


def format_report(gaps: list[Gap]) -> str:
    if not gaps:
        return "No gaps found."
    lines = []
    for g in gaps:
        resumed = g.resumed_at_utc or "still down"
        lines.append(f"{g.source}/{g.endpoint}: stopped {g.last_capture_utc}, resumed {resumed}")
        lines.append(f"  missed runs: {g.missed_runs}, cause: {g.cause}")
        if g.analysis_recoverable_from_next_snapshot:
            lines.append("  analysis values: covered by the next snapshot's past_days")
        else:
            lines.append("  analysis values: backfill from the archive API for the window below")
        lines.append("  forecast vintages issued in this window: lost (weather leads partly restorable via Previous Runs)")
        lines.append(f"  backfill window: {g.backfill_from_utc} to {g.backfill_to_utc or 'now'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report outages in the forecast capture.")
    parser.add_argument("--ops-dir", default=str(DEFAULT_OPS_DIR))
    parser.add_argument("--every-hours", type=float, default=6.0)
    parser.add_argument("--json", action="store_true", help="print the gaps as JSON")
    args = parser.parse_args(argv)

    records = load_records(Path(args.ops_dir) / RUN_LOG_NAME)
    gaps = find_gaps(records, every=timedelta(hours=args.every_hours))
    print(json.dumps([asdict(g) for g in gaps], indent=2) if args.json else format_report(gaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
