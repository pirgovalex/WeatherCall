"""Outage detection from the capture run log."""

import json
from datetime import datetime, timedelta, timezone

from plovdiv.ops.gaps import find_gaps, format_report, load_records, main

T0 = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
EVERY = timedelta(hours=6)


def run(source, endpoint, at, status="ok"):
    entry = {
        "source": source,
        "endpoint": endpoint,
        "started_at_utc": at.isoformat(),
        "finished_at_utc": (at + timedelta(seconds=3)).isoformat(),
        "status": status,
        "landing_file": f"data/landing/{source}/x.json.gz" if status != "failed" else None,
        "error": None if status != "failed" else "fetch failed",
    }
    return entry


def schedule(source, endpoint, hours):
    return [run(source, endpoint, T0 + timedelta(hours=h)) for h in hours]


def test_no_gap_when_the_schedule_is_kept():
    records = schedule("cams", "air_quality", [0, 6, 12, 18])
    assert find_gaps(records, EVERY, now=T0 + timedelta(hours=19)) == []


def test_small_jitter_is_not_a_gap():
    records = schedule("cams", "air_quality", [0, 6.5, 12.9, 19.1])
    assert find_gaps(records, EVERY, now=T0 + timedelta(hours=20)) == []


def test_pc_off_between_runs_is_reported_with_stop_and_resume_times():
    records = schedule("cams", "air_quality", [0, 6, 36, 42])
    (gap,) = find_gaps(records, EVERY, now=T0 + timedelta(hours=43))
    assert gap.last_capture_utc.startswith("2026-10-07T06:00:03")
    assert gap.resumed_at_utc.startswith("2026-10-08T12:00:03")
    assert gap.missed_runs == 4
    assert gap.cause == "no_run"
    assert gap.backfill_from_utc == gap.last_capture_utc
    assert gap.backfill_to_utc == gap.resumed_at_utc


def test_a_run_that_failed_inside_the_gap_is_classified_as_failed():
    records = schedule("openmeteo", "forecast", [0, 6])
    records += [run("openmeteo", "forecast", T0 + timedelta(hours=h), "failed") for h in (12, 18, 24)]
    records += schedule("openmeteo", "forecast", [30])
    (gap,) = find_gaps(records, EVERY, now=T0 + timedelta(hours=31))
    assert gap.cause == "failed"
    assert gap.missed_runs == 3


def test_open_gap_when_capture_has_not_resumed():
    records = schedule("cams", "air_quality", [0, 6])
    (gap,) = find_gaps(records, EVERY, now=T0 + timedelta(hours=40))
    assert gap.resumed_at_utc is None and gap.backfill_to_utc is None
    assert not gap.analysis_recoverable_from_next_snapshot


def test_gap_shorter_than_past_days_is_recoverable_for_analysis_values():
    cams = schedule("cams", "air_quality", [0, 6, 60, *range(66, 121, 6)])  # one 54 h gap, < 7 days
    weather = schedule("openmeteo", "forecast", [0, 6, 120])  # 114 h > 3 days
    gaps = {g.source: g for g in find_gaps(cams + weather, EVERY, now=T0 + timedelta(hours=121))}
    assert gaps["cams"].analysis_recoverable_from_next_snapshot
    assert not gaps["openmeteo"].analysis_recoverable_from_next_snapshot


def test_sources_are_tracked_independently():
    records = schedule("cams", "air_quality", [0, 6, 12, 18, 24]) + schedule("openmeteo", "forecast", [0, 24])
    gaps = find_gaps(records, EVERY, now=T0 + timedelta(hours=25))
    assert [g.source for g in gaps] == ["openmeteo"]


def test_source_that_never_captured_is_not_reported_as_a_gap():
    records = [run("cams", "air_quality", T0, "failed")]
    assert find_gaps(records, EVERY, now=T0 + timedelta(days=2)) == []


def test_unreadable_log_lines_are_skipped(tmp_path, capsys):
    path = tmp_path / "ingest_runs.jsonl"
    good = json.dumps(run("cams", "air_quality", T0))
    path.write_text(good + "\n{ broken\n\n" + good + "\n", encoding="utf-8")
    assert len(load_records(path)) == 2
    assert "skipping" in capsys.readouterr().err


def test_missing_log_means_no_records(tmp_path):
    assert load_records(tmp_path / "none.jsonl") == []


def test_report_and_cli(tmp_path, capsys):
    records = schedule("cams", "air_quality", [0, 6, 36])
    (tmp_path / "ingest_runs.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    assert main(["--ops-dir", str(tmp_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out and out[0]["source"] == "cams"

    gaps = find_gaps(records, EVERY, now=T0 + timedelta(hours=37))
    text = format_report(gaps)
    assert "stopped 2026-10-07T06:00:03" in text and "resumed 2026-10-08T12:00:03" in text
    assert "lost" in text
    assert format_report([]) == "No gaps found."
