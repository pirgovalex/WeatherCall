"""The required marts built straight from landed files, and the authoritative manifest.

All tests build small synthetic landing files in a temporary directory, so they run without the real
data. The last test checks the real output and is skipped when `data/marts/manifest.json` is absent.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from plovdiv.marts import core

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "contracts" / "fixtures"
MU_UG_M3 = "\u03bcg/m\u00b3"
DEGREE_C = "\u00b0C"
GENERATED_AT = "2026-10-07T00:00:00Z"


# ---------------------------------------------------------------------------------------
# Synthetic landing files
# ---------------------------------------------------------------------------------------


def eea_records(
    station: str,
    code: int,
    days: list[str],
    values: list,
    validity: int | list[int] = 1,
    unit: str = "ug.m-3",
    **extra,
) -> pd.DataFrame:
    """Records in the shape of a landed E1a file; `days` are the Start dates."""
    start = pd.to_datetime(days)
    n = len(days)
    frame = pd.DataFrame(
        {
            "Samplingpoint": f"BG/SPO-{station}_{code:05d}_100",
            "Pollutant": code,
            "Start": start,
            "End": start + pd.Timedelta(days=1),
            "Value": values,
            "Unit": unit,
            "AggType": "day",
            "Validity": validity if isinstance(validity, list) else [validity] * n,
            "Verification": 1,
            "ResultTime": pd.Timestamp("2025-12-31 23:00:00"),
        }
    )
    for key, value in extra.items():
        frame[key] = value
    return frame


def write_eea(root: Path, frame: pd.DataFrame, date: str = "2026-10-07") -> Path:
    directory = root / "source=eea" / "endpoint=e1a_daily" / f"fetch_date={date}"
    directory.mkdir(parents=True, exist_ok=True)
    point = re.sub(r"[^A-Za-z0-9_-]", "_", str(frame["Samplingpoint"].iloc[0]))
    path = directory / f"SPO-{point}.parquet"
    frame.to_parquet(path, index=False)
    return path


def write_snapshot(
    root: Path,
    source: str,
    endpoint: str,
    name: str,
    fetched_at: str | None,
    start: str,
    columns: dict,
    units: dict,
    offset: int = 0,
    date: str = "2026-10-07",
) -> Path:
    n = len(next(iter(columns.values())))
    times = pd.date_range(start, periods=n, freq="h").strftime("%Y-%m-%dT%H:%M").tolist()
    payload = {
        "latitude": 42.1,
        "longitude": 24.8,
        "elevation": 167.0,
        "utc_offset_seconds": offset,
        "hourly_units": {"time": "iso8601", **units},
        "hourly": {"time": times, **columns},
    }
    if fetched_at is not None:
        payload["_audit"] = {"fetched_at_utc": fetched_at, "run_id": name}
    directory = root / f"source={source}" / f"endpoint={endpoint}" / f"fetched_date={date}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json.gz"
    # A fixed header time keeps the bytes, and therefore the input digests, independent of the clock.
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
        handle.write(json.dumps(payload).encode("utf-8"))
    return path


def cams_snapshot(root: Path, name="cams-1", fetched_at="2026-10-07T14:33:21+00:00", start="2026-10-07 12:00", pm10=None, **kw):
    pm10 = pm10 if pm10 is not None else [10.0, 11.0, 12.0, 13.0, 14.0]
    n = len(pm10)
    columns = {"pm10": pm10, "pm2_5": [v / 2 for v in pm10], "nitrogen_dioxide": [5.0] * n}
    units = {k: MU_UG_M3 for k in columns}
    return write_snapshot(root, "cams", "air_quality", name, fetched_at, start, columns, units, **kw)


def weather_snapshot(root: Path, name="om-1", fetched_at="2026-10-07T14:33:20+00:00", start="2026-10-07 12:00", gusts=None, gust_unit="km/h", **kw):
    gusts = gusts if gusts is not None else [3.6, 7.2, 10.8, 14.4, 18.0]
    n = len(gusts)
    columns = {"temperature_2m": [20.0] * n, "wind_gusts_10m": gusts, "precipitation": [0.0] * n}
    units = {"temperature_2m": DEGREE_C, "wind_gusts_10m": gust_unit, "precipitation": "mm"}
    return write_snapshot(root, "openmeteo", "forecast", name, fetched_at, start, columns, units, **kw)


def tiny_landing(root: Path) -> tuple[Path, Path]:
    """A landing zone with two stations, one pollutant gap and one snapshot per forecast source."""
    write_eea(root, eea_records("BG0051A", 5, ["2026-10-01", "2026-10-02", "2026-10-04"], ["20.5", "30.25", "41"]))
    write_eea(root, eea_records("BG0051A", 6001, ["2026-10-01", "2026-10-02"], ["12", "15"]))
    write_eea(root, eea_records("BG0078A", 5, ["2026-10-02", "2026-10-03"], ["50.5", "60"]))
    cams_snapshot(root)
    weather_snapshot(root)
    return root, root / "source=eea" / "endpoint=e1a_daily" / "fetch_date=2026-10-07"


def prepare_out(out: Path) -> Path:
    """An output directory that already holds the model marts, as plovdiv.ml.marts leaves it."""
    for name in ("mart_model_card", "mart_model_calibration"):
        target = core.model_marts.mart_path(out, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / name / "latest" / f"{name}.parquet", target)
    return out


def digest_tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------------------------------
# mart_aq_daily
# ---------------------------------------------------------------------------------------


def test_valid_records_become_contract_rows(tmp_path):
    path = write_eea(tmp_path, eea_records("BG0051A", 5, ["2026-10-01", "2026-10-02"], ["20.5", "30.25"]))
    frame, counts = core.read_eea_daily(path)
    assert list(frame.columns) == ["day", "location_id", "pollutant", "daily_mean", "valid_hours", "is_valid_day"]
    assert list(frame["daily_mean"]) == [20.5, 30.25]
    assert set(frame["location_id"]) == {"plovdiv"} and set(frame["pollutant"]) == {"pm10"}
    assert counts["records_valid"] == 2 and counts["days_without_record"] == 0
    assert core.validate_frame(core.DAILY_MART, frame) == []


def test_station_and_pollutant_names_follow_the_contract(tmp_path):
    kamenitsa = core.read_eea_daily(write_eea(tmp_path, eea_records("BG0051A", 6001, ["2026-10-01"], ["9"])))[0]
    trakia = core.read_eea_daily(write_eea(tmp_path, eea_records("BG0078A", 5, ["2026-10-01"], ["9"])))[0]
    assert (kamenitsa["location_id"].iloc[0], kamenitsa["pollutant"].iloc[0]) == ("plovdiv", "pm2_5")
    assert (trakia["location_id"].iloc[0], trakia["pollutant"].iloc[0]) == ("plovdiv-trakia", "pm10")


def test_missing_days_between_first_and_last_become_null_rows(tmp_path):
    path = write_eea(tmp_path, eea_records("BG0051A", 5, ["2026-10-01", "2026-10-04"], ["20", "40"]))
    frame, counts = core.read_eea_daily(path)
    assert [d.isoformat() for d in frame["day"]] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    gap = frame.iloc[1:3]
    assert gap["daily_mean"].isna().all() and (gap["valid_hours"] == 0).all() and not gap["is_valid_day"].any()
    assert counts["days_without_record"] == 2 and counts["rows"] == 4
    assert core.validate_frame(core.DAILY_MART, frame) == []


def test_records_that_are_not_valid_keep_their_day_as_a_null_row(tmp_path):
    frame = eea_records(
        "BG0051A", 5, ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"],
        ["20", "30", "-5", "not a number", "50"], validity=[1, -1, 1, 1, 1],
    )
    out, counts = core.read_eea_daily(write_eea(tmp_path, frame))
    assert list(out["is_valid_day"]) == [True, False, False, False, True]
    assert out["daily_mean"].tolist()[0] == 20.0 and out["daily_mean"].isna().tolist() == [False, True, True, True, False]
    assert list(out["valid_hours"]) == [18, 0, 0, 0, 18]
    assert counts["records_not_valid"] == 3 and counts["validity_codes"] == {"-1": 1, "1": 4}
    assert core.validate_frame(core.DAILY_MART, out) == []


def test_units_are_converted_and_an_unknown_unit_stops_the_build(tmp_path):
    out, _ = core.read_eea_daily(write_eea(tmp_path, eea_records("BG0051A", 5, ["2026-10-01"], ["0.02"], unit="mg.m-3")))
    assert out["daily_mean"].iloc[0] == pytest.approx(20.0)
    with pytest.raises(ValueError, match="unknown units"):
        core.read_eea_daily(write_eea(tmp_path, eea_records("BG0051A", 5, ["2026-10-01"], ["20"], unit="ppb")))


@pytest.mark.parametrize(
    "change, message",
    [
        ({"AggType": "hour"}, "AggType"),
        ({"Pollutant": 6001}, "does not match"),
        ({"Samplingpoint": "BG/SPO-BG9999Z_00005_100"}, "no location"),
        ({"Samplingpoint": "not a sampling point"}, "exactly one sampling point"),
    ],
)
def test_malformed_files_are_rejected(tmp_path, change, message):
    frame = eea_records("BG0051A", 5, ["2026-10-01", "2026-10-02"], ["20", "30"])
    for key, value in change.items():
        frame[key] = value
    with pytest.raises(ValueError, match=message):
        core.read_eea_daily(write_eea(tmp_path, frame))


def test_records_that_are_not_whole_days_are_rejected(tmp_path):
    frame = eea_records("BG0051A", 5, ["2026-10-01"], ["20"])
    frame["Start"] = pd.Timestamp("2026-10-01 06:00")
    with pytest.raises(ValueError, match="whole EEA days"):
        core.read_eea_daily(write_eea(tmp_path, frame))


def test_a_day_reported_twice(tmp_path):
    both_valid = eea_records("BG0051A", 5, ["2026-10-01", "2026-10-01"], ["20", "30"])
    with pytest.raises(ValueError, match="more than one valid record"):
        core.read_eea_daily(write_eea(tmp_path, both_valid))
    one_valid = eea_records("BG0051A", 5, ["2026-10-01", "2026-10-01"], ["99", "30"], validity=[-1, 1])
    out, counts = core.read_eea_daily(write_eea(tmp_path, one_valid))
    assert out["daily_mean"].tolist() == [30.0] and counts["records_duplicate_days_dropped"] == 1


def test_the_dst_day_is_one_ordinary_row_and_aware_days_use_utc_plus_one(tmp_path):
    naive = eea_records("BG0051A", 5, ["2026-10-24", "2026-10-25", "2026-10-26"], ["1", "2", "3"])
    out, _ = core.read_eea_daily(write_eea(tmp_path, naive))
    assert [d.isoformat() for d in out["day"]] == ["2026-10-24", "2026-10-25", "2026-10-26"]
    aware = eea_records("BG0051A", 5, ["2026-10-25"], ["2"])
    aware["Start"] = pd.Series([pd.Timestamp("2026-10-25 00:00", tz="Etc/GMT-1")])
    aware["End"] = pd.Series([pd.Timestamp("2026-10-26 00:00", tz="Etc/GMT-1")])
    assert core.read_eea_daily(write_eea(tmp_path, aware))[0]["day"].iloc[0].isoformat() == "2026-10-25"


def test_valid_hours_is_the_guaranteed_minimum_unless_the_source_reports_capture(tmp_path):
    unreported, counts = core.read_eea_daily(write_eea(tmp_path, eea_records("BG0051A", 5, ["2026-10-01"], ["20"])))
    assert unreported["valid_hours"].iloc[0] == core.MIN_VALID_HOURS and unreported["is_valid_day"].iloc[0]
    assert counts["data_capture_reported"] is False
    reported = eea_records("BG0051A", 5, ["2026-10-01", "2026-10-02", "2026-10-03"], ["20", "30", "40"], DataCapture=[0.9, 50, 1.0])
    out, counts = core.read_eea_daily(write_eea(tmp_path, reported))
    assert list(out["valid_hours"]) == [22, 12, 24]
    assert list(out["is_valid_day"]) == [True, False, True]  # 12 of 24 hours is not a valid day, and its mean is kept
    assert out["daily_mean"].tolist() == [20.0, 30.0, 40.0] and counts["data_capture_reported"] is True
    assert core.validate_frame(core.DAILY_MART, out) == []


def test_daily_rows_are_sorted_by_the_grain(tmp_path):
    _, eea_dir = tiny_landing(tmp_path)
    daily, _ = core.build_daily(eea_dir)
    keys = list(zip(daily["day"], daily["location_id"], daily["pollutant"]))
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert len(daily) == 4 + 2 + 2  # Kamenitsa PM10 with its gap, Kamenitsa PM2.5, Trakia PM10


# ---------------------------------------------------------------------------------------
# mart_weather_aq_hourly
# ---------------------------------------------------------------------------------------


def test_hours_after_the_capture_hour_are_forecast_and_the_rest_analysis(tmp_path):
    cams_snapshot(tmp_path, fetched_at="2026-10-07T14:33:21+00:00", start="2026-10-07 12:00")
    hourly, _ = core.build_hourly(tmp_path)
    kinds = dict(zip(hourly["valid_at_utc"].dt.strftime("%H"), hourly["source_type"]))
    assert kinds == {"12": "analysis", "13": "analysis", "14": "analysis", "15": "forecast", "16": "forecast"}
    assert set(hourly["location_id"]) == {"plovdiv"}


def test_units_come_from_the_file_and_wind_is_converted_to_metres_per_second(tmp_path):
    weather_snapshot(tmp_path, gusts=[3.6, 7.2], gust_unit="km/h")
    hourly, diagnostics = core.build_hourly(tmp_path)
    assert hourly["wind_gusts_10m"].tolist() == [1.0, 2.0]
    assert diagnostics["openmeteo_snapshots"][0]["units_in_file"]["wind_gusts_10m"] == "km/h"
    other = tmp_path / "other"
    weather_snapshot(other, gusts=[1.0, 2.0], gust_unit="m/s")
    assert core.build_hourly(other)[0]["wind_gusts_10m"].tolist() == [1.0, 2.0]
    third = tmp_path / "third"
    weather_snapshot(third, gusts=[1.0], gust_unit="furlongs/fortnight")
    with pytest.raises(ValueError, match="accepted units"):
        core.build_hourly(third)


def test_values_outside_the_schema_range_become_null_and_are_counted(tmp_path):
    cams_snapshot(tmp_path, pm10=[10.0, -3.0, 12.0])
    write_snapshot(
        tmp_path, "openmeteo", "forecast", "om-hot", "2026-10-07T14:33:20+00:00", "2026-10-07 12:00",
        {"temperature_2m": [20.0, 99.0, 21.0], "wind_gusts_10m": [3.6, 3.6, 3.6], "precipitation": [0.0, -1.0, 0.0]},
        {"temperature_2m": DEGREE_C, "wind_gusts_10m": "km/h", "precipitation": "mm"},
    )
    hourly, diagnostics = core.build_hourly(tmp_path)
    middle = hourly[hourly["valid_at_utc"] == pd.Timestamp("2026-10-07 13:00", tz="UTC")].iloc[0]
    assert np.isnan(middle["pm10"]) and np.isnan(middle["temperature_2m"]) and np.isnan(middle["precipitation"])
    assert np.isnan(middle["pm2_5"]) and middle["no2"] == 5.0  # the helper sets PM2.5 to half of PM10, so it is negative too
    assert diagnostics["cams_snapshots"][0]["values_outside_schema_range_set_to_null"] == {"pm10": 1, "pm2_5": 1, "no2": 0}
    assert diagnostics["openmeteo_snapshots"][0]["values_outside_schema_range_set_to_null"] == {
        "temperature_2m": 1, "wind_gusts_10m": 0, "precipitation": 1,
    }
    assert core.validate_frame(core.HOURLY_MART, hourly) == []


def test_sources_are_joined_on_hour_and_type_and_empty_hours_are_dropped(tmp_path):
    cams_snapshot(tmp_path, start="2026-10-07 12:00", pm10=[10.0, 11.0, 12.0])
    weather_snapshot(tmp_path, start="2026-10-07 14:00", gusts=[3.6, 7.2, 10.8])
    write_snapshot(  # CAMS hours with no value at all
        tmp_path, "cams", "air_quality", "cams-tail", "2026-10-07T14:40:00+00:00", "2026-10-07 20:00",
        {"pm10": [None, None], "pm2_5": [None, None], "nitrogen_dioxide": [None, None]},
        {"pm10": MU_UG_M3, "pm2_5": MU_UG_M3, "nitrogen_dioxide": MU_UG_M3},
    )
    hourly, diagnostics = core.build_hourly(tmp_path)
    indexed = hourly.set_index(["valid_at_utc", "source_type"])
    analysis_14 = indexed.loc[(pd.Timestamp("2026-10-07 14:00", tz="UTC"), "analysis")]
    assert analysis_14["pm10"] == 12.0 and analysis_14["wind_gusts_10m"] == 1.0
    assert np.isnan(indexed.loc[(pd.Timestamp("2026-10-07 12:00", tz="UTC"), "analysis"), "temperature_2m"])
    assert np.isnan(indexed.loc[(pd.Timestamp("2026-10-07 16:00", tz="UTC"), "forecast"), "pm10"])
    assert pd.Timestamp("2026-10-07 20:00", tz="UTC") not in set(hourly["valid_at_utc"])
    assert diagnostics["cams_snapshots"][-1]["hours_without_any_value_dropped"] == 2
    assert not hourly.duplicated(["valid_at_utc", "location_id", "source_type"]).any()


def test_the_latest_snapshot_decides_and_an_old_forecast_stays_a_forecast_row(tmp_path):
    cams_snapshot(tmp_path, name="a", fetched_at="2026-10-07T08:10:00+00:00", start="2026-10-07 08:00", pm10=[1.0, 2.0, 3.0, 4.0, 5.0], date="2026-10-07")
    cams_snapshot(tmp_path, name="b", fetched_at="2026-10-07T10:10:00+00:00", start="2026-10-07 09:00", pm10=[20.0, 30.0, 40.0, 50.0], date="2026-10-07")
    hourly, diagnostics = core.build_hourly(tmp_path)
    indexed = hourly.set_index(["valid_at_utc", "source_type"])
    at = lambda hour, kind: indexed.loc[(pd.Timestamp(f"2026-10-07 {hour}", tz="UTC"), kind), "pm10"]  # noqa: E731
    assert at("08:00", "analysis") == 1.0  # only the first snapshot knows this hour
    assert at("09:00", "analysis") == 20.0  # the later snapshot replaces the earlier analysis
    assert at("10:00", "analysis") == 30.0
    assert at("11:00", "forecast") == 40.0 and at("12:00", "forecast") == 50.0  # latest vintage of the forecast
    assert at("10:00", "forecast") == 3.0  # the earlier forecast of an hour that is now analysis is kept as its own row
    assert [m["fetched_at_utc"] for m in diagnostics["cams_snapshots"]] == ["2026-10-07T08:10:00Z", "2026-10-07T10:10:00Z"]


@pytest.mark.parametrize(
    "kwargs, message",
    [({"fetched_at": None}, "no capture time"), ({"offset": 7200}, "not UTC"), ({"fetched_at": "2026-10-07T14:33:21"}, "no time zone")],
)
def test_snapshots_without_a_usable_capture_time_or_with_local_times_are_rejected(tmp_path, kwargs, message):
    cams_snapshot(tmp_path, **kwargs)
    with pytest.raises(ValueError, match=message):
        core.build_hourly(tmp_path)


def test_no_snapshots_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        core.build_hourly(tmp_path)


# ---------------------------------------------------------------------------------------
# Schemas, files, determinism and the manifest
# ---------------------------------------------------------------------------------------


def build_tiny(tmp_path: Path, name: str = "run") -> Path:
    landing, eea_dir = tiny_landing(tmp_path / f"landing_{name}")
    out = prepare_out(tmp_path / f"out_{name}")
    core.build(landing, eea_dir, out, GENERATED_AT)
    return out


def test_both_marts_pass_their_contract_schema_and_have_the_fixture_column_types(tmp_path):
    out = build_tiny(tmp_path)
    for name in core.REQUIRED_MARTS:
        path = core.model_marts.mart_path(out, name)
        schema = pq.read_schema(path)
        assert schema.equals(core.ARROW_SCHEMAS[name].remove_metadata(), check_metadata=False), name
        assert b"pandas" not in (schema.metadata or {})
        assert core.validate_frame(name, pd.read_parquet(path)) == [], name


def test_a_mart_that_fails_its_schema_is_not_written(tmp_path):
    landing, eea_dir = tiny_landing(tmp_path / "landing")
    daily, _ = core.build_daily(eea_dir)
    hourly, _ = core.build_hourly(landing)
    broken = daily.copy()
    broken.loc[broken.index[0], "valid_hours"] = 3  # is_valid_day stays true: breaks the 18-of-24 rule
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="nothing written"):
        core.write_required_marts(out, hourly, broken)
    assert not out.exists() or not list(out.rglob("*.parquet"))
    wrong_type = hourly.copy()
    wrong_type["source_type"] = "measured"
    with pytest.raises(ValueError, match="source_type"):
        core.write_required_marts(out, wrong_type, daily)


def test_output_is_byte_identical_when_built_twice_in_different_places(tmp_path):
    first, second = build_tiny(tmp_path, "one"), build_tiny(tmp_path, "two")
    assert digest_tree(first) == digest_tree(second)
    names = digest_tree(first)
    assert {"manifest.json", "diagnostics/core_build_report.json"} <= set(names)
    assert "mart_weather_aq_hourly/latest/mart_weather_aq_hourly.parquet" in names


def test_manifest_is_valid_and_matches_the_files(tmp_path):
    out = build_tiny(tmp_path)
    assert core.validate_output(out) == []
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generated_at_utc"] == GENERATED_AT and manifest["schema_version"] == "1"
    assert list(manifest["variables"]) == ["pm2_5", "pm10", "no2", "temperature_2m", "wind_gusts_10m", "precipitation"]
    assert manifest["variables"]["wind_gusts_10m"] == {"unit": "m/s", "label": core.VARIABLES["wind_gusts_10m"]["label"]}
    assert list(manifest["marts"]) == ["mart_weather_aq_hourly", "mart_aq_daily", "mart_model_card", "mart_model_calibration"]
    hourly = manifest["marts"]["mart_weather_aq_hourly"]
    assert hourly["rows"] == 5 and hourly["min_valid_at_utc"] == "2026-10-07T12:00:00Z" and hourly["max_valid_at_utc"] == "2026-10-07T16:00:00Z"
    assert manifest["marts"]["mart_aq_daily"]["rows"] == 8
    assert "stations" not in manifest  # no station-keyed mart is declared
    report = json.loads((out / "diagnostics" / "core_build_report.json").read_text(encoding="utf-8"))
    assert report["authoritative_manifest"] == "manifest.json" and report["run_id"] == manifest["run_id"]


def test_manifest_carries_the_optional_analysis_and_daily_ranges(tmp_path):
    out = build_tiny(tmp_path)
    marts = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["marts"]
    hourly, daily = marts["mart_weather_aq_hourly"], marts["mart_aq_daily"]
    # Capture at 14:33: analysis up to 14:00, forecast at 15:00 and 16:00, so the two maxima differ.
    assert hourly["max_valid_at_utc"] == "2026-10-07T16:00:00Z"
    assert hourly["max_analysis_valid_at_utc"] == "2026-10-07T14:00:00Z"
    # Kamenitsa PM10 has a gap day inside 2026-10-01..04; the gap row counts as a day with a row.
    assert (daily["min_day"], daily["max_day"]) == ("2026-10-01", "2026-10-04")


def test_optional_range_fields_from_small_frames():
    hours = pd.date_range("2026-10-01", periods=4, freq="h", tz="UTC")
    mixed = pd.DataFrame({"valid_at_utc": hours, "source_type": ["analysis", "analysis", "forecast", "analysis"]})
    assert core.optional_range_fields(core.HOURLY_MART, mixed) == {"max_analysis_valid_at_utc": "2026-10-01T03:00:00Z"}
    only_forecast = mixed.assign(source_type="forecast")
    assert core.optional_range_fields(core.HOURLY_MART, only_forecast) == {}
    daily = pd.DataFrame({"day": [pd.Timestamp(d).date() for d in ["2013-01-02", "2013-01-01", "2025-12-31"]]})
    assert core.optional_range_fields(core.DAILY_MART, daily) == {"min_day": "2013-01-01", "max_day": "2025-12-31"}
    assert core.optional_range_fields(core.DAILY_MART, daily.iloc[0:0]) == {}
    assert core.optional_range_fields("mart_model_card", daily) == {}


def test_validate_output_checks_the_optional_range_fields_when_present_and_accepts_their_absence(tmp_path):
    out = build_tiny(tmp_path)
    path = out / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key, value in (
        ("mart_weather_aq_hourly", ("max_analysis_valid_at_utc", "2026-10-07T15:00:00Z")),
        ("mart_aq_daily", ("min_day", "2026-09-30")),
        ("mart_aq_daily", ("max_day", "2026-10-05")),
    ):
        tampered = copy.deepcopy(manifest)
        tampered["marts"][key][value[0]] = value[1]
        path.write_text(json.dumps(tampered), encoding="utf-8")
        text = "\n".join(core.validate_output(out))
        assert f"manifest {value[0]} {value[1]} differs from the file" in text, text
    # A badly formatted value is rejected by the manifest schema as well.
    malformed = copy.deepcopy(manifest)
    malformed["marts"]["mart_aq_daily"]["min_day"] = "2026-10-1"
    path.write_text(json.dumps(malformed), encoding="utf-8")
    assert any("min_day" in p for p in core.validate_output(out))
    # An older manifest without the fields stays valid.
    older = copy.deepcopy(manifest)
    for entry, keys in (("mart_weather_aq_hourly", ["max_analysis_valid_at_utc"]), ("mart_aq_daily", ["min_day", "max_day"])):
        for key in keys:
            del older["marts"][entry][key]
    path.write_text(json.dumps(older), encoding="utf-8")
    assert core.validate_output(out) == []


def test_run_id_follows_the_content_not_the_clock_or_the_place(tmp_path):
    one, two = build_tiny(tmp_path, "one"), build_tiny(tmp_path, "two")
    run_one = json.loads((one / "manifest.json").read_text(encoding="utf-8"))["run_id"]
    run_two = json.loads((two / "manifest.json").read_text(encoding="utf-8"))["run_id"]
    assert run_one == run_two and run_one.startswith("warehouse-") and len(run_one) == len("warehouse-") + 12
    changed_landing, changed_eea = tiny_landing(tmp_path / "landing_changed")
    write_eea(changed_landing, eea_records("BG0051A", 5, ["2026-10-01", "2026-10-02", "2026-10-04"], ["20.5", "30.25", "42"]))
    out = prepare_out(tmp_path / "out_changed")
    report = core.build(changed_landing, changed_eea, out, GENERATED_AT)
    assert report["run_id"] != run_one


def test_station_catalogue_is_added_with_station_keyed_marts_and_cross_checks_run(tmp_path):
    out = build_tiny(tmp_path)
    for name in ("mart_aq_trend_monthly", "mart_aq_risk_daily", "mart_aq_risk_drivers"):
        target = core.model_marts.mart_path(out, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / name / "latest" / f"{name}.parquet", target)
    manifest = core.assemble_manifest(out, GENERATED_AT, "warehouse-test")
    assert set(manifest["stations"]) == {"BG0051A", "BG0078A"}
    assert list(manifest["marts"])[:4] == ["mart_weather_aq_hourly", "mart_aq_daily", "mart_aq_trend_monthly", "mart_aq_risk_daily"]
    core.model_marts.write_json(out / "manifest.json", manifest)
    # The synthetic fixtures were not derived from these daily rows, so the cross-mart rules must object.
    problems = core.validate_output(out)
    assert any("labelled days" in p for p in problems), problems
    assert any("n_days" in p for p in problems), problems


def test_validate_output_reports_a_tampered_manifest_and_a_missing_file(tmp_path):
    out = build_tiny(tmp_path)
    path = out / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(manifest)
    tampered["marts"]["mart_aq_daily"]["rows"] = 999
    tampered["marts"]["mart_weather_aq_hourly"]["max_valid_at_utc"] = "2026-10-08T00:00:00Z"
    tampered["schema_version"] = "2"
    del tampered["variables"]["no2"]
    path.write_text(json.dumps(tampered), encoding="utf-8")
    text = "\n".join(core.validate_output(out))
    assert "manifest says 999 rows" in text and "manifest range differs" in text
    assert "schema_version" in text and "no2" in text
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert core.validate_output(out) == []
    renamed = core.model_marts.mart_path(out, "mart_aq_daily")
    renamed.rename(renamed.with_suffix(".moved"))
    assert any("no file matches" in p for p in core.validate_output(out))
    assert core.validate_output(tmp_path / "nowhere") == [f"{tmp_path / 'nowhere' / 'manifest.json'} does not exist"]


def test_cross_mart_rules_on_small_frames():
    daily = pd.DataFrame(
        {
            "day": [pd.Timestamp(d).date() for d in ["2026-10-01", "2026-10-02", "2026-10-03"]],
            "location_id": "plovdiv",
            "pollutant": "pm10",
            "daily_mean": [10.0, 20.0, np.nan],
            "valid_hours": [18, 18, 0],
            "is_valid_day": [True, True, False],
        }
    )
    manifest = {"variables": copy.deepcopy(core.VARIABLES), "stations": {"BG0051A": {"label": "K", "station_type": "background", "area_type": "urban", "location_id": "plovdiv"}}}
    hourly = pd.DataFrame({"valid_at_utc": [pd.Timestamp("2026-10-01", tz="UTC")], "location_id": "plovdiv", "source_type": "analysis", **{c: [1.0] for c in core.MEASURE_COLUMNS}})
    trend = pd.DataFrame(
        {"station_id": ["BG0051A"], "month": [pd.Timestamp("2026-10-01").date()], "observed_mean_ugm3": [15.0], "weather_adjusted_mean_ugm3": [np.nan],
         "adjusted_lo_ugm3": [np.nan], "adjusted_hi_ugm3": [np.nan], "n_days": [2], "pollutant": ["pm10"]}
    )
    risk = pd.DataFrame(
        {"station_id": "BG0051A", "target_date": [pd.Timestamp("2026-10-01").date(), pd.Timestamp("2026-10-02").date()], "model_id": "m", "label_id": "l", "actual_label": [1, 0]}
    )
    frames = {core.HOURLY_MART: hourly, core.DAILY_MART: daily, "mart_aq_trend_monthly": trend, "mart_aq_risk_daily": risk}
    assert core.cross_mart_problems(frames, manifest) == []

    wrong_trend = trend.assign(n_days=3)
    assert any("n_days" in p for p in core.cross_mart_problems({**frames, "mart_aq_trend_monthly": wrong_trend}, manifest))
    wrong_mean = trend.assign(observed_mean_ugm3=99.0)
    assert any("observed mean" in p for p in core.cross_mart_problems({**frames, "mart_aq_trend_monthly": wrong_mean}, manifest))
    late_label = risk.assign(target_date=[pd.Timestamp("2026-10-01").date(), pd.Timestamp("2026-10-03").date()])
    assert any("labelled days" in p for p in core.cross_mart_problems({**frames, "mart_aq_risk_daily": late_label}, manifest))
    undecided = risk.assign(target_date=[pd.Timestamp("2026-10-01").date(), pd.Timestamp("2026-10-03").date()], actual_label=[1, None])
    assert core.cross_mart_problems({**frames, "mart_aq_risk_daily": undecided}, manifest) == []
    unknown_station = {**manifest, "stations": {}}
    assert any("not declared" in p for p in core.cross_mart_problems(frames, unknown_station))
    stray_unit = {**manifest, "variables": {**manifest["variables"], "pm10": {"unit": "mg/m3", "label": "PM10"}}}
    assert any("unit" in p for p in core.cross_mart_problems(frames, stray_unit))


# ---------------------------------------------------------------------------------------
# Real output (skipped when absent)
# ---------------------------------------------------------------------------------------


@pytest.mark.skipif(not (core.DEFAULT_OUT_DIR / "manifest.json").exists(), reason="data/marts/manifest.json not present")
def test_real_manifest_and_marts_are_valid():
    assert core.validate_output(core.DEFAULT_OUT_DIR) == []
