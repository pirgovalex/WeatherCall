"""Live risk row: UTD daily means, forecast weather aggregation, refusals and the published row."""

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

pytest.importorskip("sklearn")

from ml_helpers import eea_frame, synthetic_days, synthetic_series, write_landed_inputs  # noqa: E402

from contracts import schemas  # noqa: E402
from plovdiv.ml import dataset, live, marts, metrics, train  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
ISSUED = pd.Timestamp("2026-03-11T03:00:00Z")  # 04:00 EEA time, after the as-of day 2026-03-10 ended


# ---------------------------------------------------------------------------------------
# Synthetic landed files in the format of the real ones
# ---------------------------------------------------------------------------------------


def utd_frame(first="2026-01-01", last_hour="2026-03-11 01:00", value=30.0, seed=1) -> pd.DataFrame:
    """Hourly UTD records from `first` 00:00 to `last_hour` (start times), EEA day convention."""
    rng = np.random.default_rng(seed)
    starts = pd.date_range(first, last_hour, freq="h")
    values = value * np.exp(rng.normal(0, 0.2, size=len(starts)))
    return pd.DataFrame(
        {
            "Samplingpoint": "BG/SPO-BG0051A_00005_100",
            "Pollutant": 5,
            "Start": starts,
            "End": starts + pd.Timedelta(hours=1),
            "Value": [f"{v:.18f}" for v in values],
            "Unit": "ug.m-3",
            "AggType": "hour",
            "Validity": 1,
            "Verification": 1,
            "ResultTime": starts + pd.Timedelta(hours=1),
            "DataCapture": None,
            "FkObservationLog": "x",
        }
    )


def write_utd(path: Path, frame: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def capture_payload(first_hour: str, hours: int, fetched_at: str, wind_unit="m/s", temp=10.0) -> dict:
    times = pd.date_range(first_hour, periods=hours, freq="h")
    n = len(times)
    idx = np.arange(n)
    return {
        "latitude": 42.25,
        "longitude": 24.75,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "hourly_units": {
            "time": "iso8601", "temperature_2m": "°C", "relative_humidity_2m": "%",
            "wind_speed_10m": wind_unit, "wind_gusts_10m": wind_unit, "precipitation": "mm",
            "surface_pressure": "hPa",
        },
        "hourly": {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
            "temperature_2m": (temp + 5 * np.sin(idx / 24 * 2 * np.pi)).round(2).tolist(),
            "relative_humidity_2m": (60 + (idx % 24)).astype(float).tolist(),
            "wind_speed_10m": (1.0 + (idx % 24) / 10).round(2).tolist(),
            "wind_gusts_10m": (2.0 + (idx % 24) / 10).round(2).tolist(),
            "precipitation": [0.1 if i % 24 == 3 else 0.0 for i in idx],
            "surface_pressure": (1000 + (idx % 24) / 10).round(2).tolist(),
        },
        "_audit": {"fetched_at_utc": fetched_at, "run_id": "test"},
    }


def write_capture(root: Path, name: str, payload: dict) -> Path:
    fetched = payload["_audit"]["fetched_at_utc"][:10]
    path = root / "source=openmeteo" / "endpoint=forecast" / f"fetched_date={fetched}" / f"{name}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as handle:
        handle.write(json.dumps(payload).encode("utf-8"))
    return path


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """Synthetic E1a and ERA5 history (2013-2025), the three model files and the evaluation marts."""
    root = tmp_path_factory.mktemp("live")
    eea_dir, weather_file = write_landed_inputs(root / "inputs")
    days = synthetic_days("2017-01-01", "2025-12-31")
    second = synthetic_series(synthetic_days(), 5)["pm10"][-len(days):] * 1.3
    eea_frame(days, second, dataset.PM10_CODE).to_parquet(
        eea_dir / f"SPO-BG0078A_{dataset.PM10_CODE}_100.parquet", index=False
    )  # second station of the trend mart
    models_dir = root / "models"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(metrics, "BOOTSTRAP_REPLICATES", 20)
        train.run(eea_dir, weather_file, models_dir, "2026-01-02")
    return {"root": root, "eea_dir": eea_dir, "weather_file": weather_file, "models_dir": models_dir}


@pytest.fixture()
def landing(trained, tmp_path):
    """A landing folder with the layout the live command reads."""
    root = tmp_path / "landing"
    e1a = root / "source=eea" / "endpoint=e1a_daily" / "fetch_date=2026-01-02"
    e1a.mkdir(parents=True)
    for p in trained["eea_dir"].glob("*.parquet"):
        (e1a / p.name).write_bytes(p.read_bytes())
    era5 = root / "source=openmeteo" / "endpoint=archive_daily_era5" / "fetch_date=2026-01-02"
    era5.mkdir(parents=True)
    (era5 / "archive_2013-01-01_2025-12-31.json.gz").write_bytes(trained["weather_file"].read_bytes())
    write_utd(root / "source=eea" / "endpoint=utd_hourly" / "fetch_date=2026-03-11" / "SPO-BG0051A_00005_100.parquet", utd_frame())
    # Local day 2026-03-10 (EET, UTC+2) is 2026-03-09 22:00 to 2026-03-10 22:00 UTC.
    write_capture(root, "older", capture_payload("2026-03-07T00:00", 240, "2026-03-10T23:30:00+00:00", temp=5.0))
    write_capture(root, "newer", capture_payload("2026-03-08T00:00", 240, "2026-03-11T02:00:00+00:00", temp=8.0))
    write_capture(root, "too_early", capture_payload("2026-03-08T00:00", 240, "2026-03-10T12:00:00+00:00", temp=99.0))
    return root


# ---------------------------------------------------------------------------------------
# UTD hourly -> daily
# ---------------------------------------------------------------------------------------


def test_utd_daily_means_apply_the_18_of_24_rule_and_drop_invalid_hours(tmp_path):
    frame = utd_frame("2026-02-01", "2026-02-03 23:00", value=20.0)
    day2 = frame["Start"].dt.date == pd.Timestamp("2026-02-02").date()
    # Day 2: 7 hours missing from the file -> 17 hours, not a valid day.
    frame = frame.drop(frame.index[day2][:7])
    day1 = frame["Start"].dt.date == pd.Timestamp("2026-02-01").date()
    day3 = frame["Start"].dt.date == pd.Timestamp("2026-02-03").date()
    # Day 1: 6 hours flagged invalid (Validity -1, value 0) -> 18 valid hours, still a valid day.
    frame.loc[frame.index[day1][:6], ["Validity", "Value"]] = [-1, "0E-18"]
    # Day 3: two hours unverified, one negative value.
    frame.loc[frame.index[day3][:2], "Verification"] = 3
    frame.loc[frame.index[day3][5], "Value"] = "-1.0"
    hourly = live.load_utd_hourly(write_utd(tmp_path / "utd.parquet", frame))
    daily = live.utd_daily_means(hourly)

    d1, d2, d3 = (pd.Timestamp(d) for d in ("2026-02-01", "2026-02-02", "2026-02-03"))
    assert daily.loc[d1, "valid_hours"] == 18
    expected = pd.to_numeric(frame.loc[frame.index[day1][6:], "Value"]).mean()
    assert daily.loc[d1, "pm10"] == pytest.approx(expected)
    assert daily.loc[d1, "verification"] == "verified"
    assert daily.loc[d2, "valid_hours"] == 17 and np.isnan(daily.loc[d2, "pm10"])
    assert daily.loc[d3, "valid_hours"] == 23
    assert daily.loc[d3, "unverified_hours"] == 2
    assert daily.loc[d3, "verification"] == "unverified"
    assert live.latest_closed_day(hourly) == d3


def test_utd_day_is_the_date_of_the_hour_start():
    """EEA day convention: the hour 23:00-24:00 belongs to the day it starts in."""
    hourly = pd.DataFrame(
        {
            "start": pd.date_range("2026-02-01 23:00", periods=25, freq="h"),
            "value": [100.0] + [10.0] * 24,
            "valid": True,
            "verification": 1,
        }
    )
    hourly["end"] = hourly["start"] + pd.Timedelta(hours=1)
    daily = live.utd_daily_means(hourly)
    assert daily.loc[pd.Timestamp("2026-02-02"), "pm10"] == pytest.approx(10.0)
    assert daily.loc[pd.Timestamp("2026-02-01"), "valid_hours"] == 1
    assert live.latest_closed_day(hourly) == pd.Timestamp("2026-02-02")


def test_utd_daily_means_feed_the_existing_rolling_rules(trained):
    """With a missing UTD day, the 3-day mean uses 2 of 3 days and day t-1 is missing, as in training."""
    frame = utd_frame()
    gap = frame["Start"].dt.normalize() == pd.Timestamp("2026-03-09")
    frame.loc[gap, "Validity"] = -1
    utd = live.utd_daily_means(live.load_utd_hourly(write_utd(trained["root"] / "gap.parquet", frame)))
    e1a = dataset.load_eea_daily(trained["eea_dir"] / "SPO-BG0051A_00005_100.parquet")
    asof = pd.Timestamp("2026-03-10")
    weather = {c: 1.0 for c in dataset.WEATHER_COLUMNS if c != "shortwave_radiation_sum"}
    row = live.live_feature_row(e1a, utd, dataset.load_weather(trained["weather_file"]), asof, weather)
    pm = utd["pm10"]
    assert row["log_pm10_t"] == pytest.approx(np.log(pm[asof]))
    assert np.isnan(row["log_pm10_t_minus_1"])
    assert row["log_pm10_mean3"] == pytest.approx(np.log(np.mean([pm[asof], pm[asof - pd.Timedelta(days=2)]])))
    window = pm.loc[asof - pd.Timedelta(days=6) : asof].dropna()
    assert len(window) == 6
    assert row["log_pm10_mean7"] == pytest.approx(np.log(window.mean()))
    assert np.isnan(row["log_pm25_t"]) and np.isnan(row["radiation_mj_m2_t"])
    assert pd.Timestamp(row["target_date"]) == pd.Timestamp("2026-03-11")


def test_e1a_values_win_over_utd_on_overlapping_days():
    e1a = pd.Series([11.0, 12.0], index=pd.to_datetime(["2025-12-30", "2025-12-31"]))
    utd = pd.DataFrame({"pm10": [99.0, 13.0]}, index=pd.to_datetime(["2025-12-31", "2026-01-01"]))
    combined = live.combined_pm10(e1a, utd)
    assert combined.to_dict() == {
        pd.Timestamp("2025-12-30"): 11.0, pd.Timestamp("2025-12-31"): 12.0, pd.Timestamp("2026-01-01"): 13.0,
    }


# ---------------------------------------------------------------------------------------
# Forecast weather -> ERA5-style daily values
# ---------------------------------------------------------------------------------------


def test_weather_aggregation_follows_the_local_day_and_the_era5_definitions(tmp_path):
    path = write_capture(tmp_path, "c", capture_payload("2026-03-08T00:00", 96, "2026-03-11T02:00:00+00:00"))
    capture = live.load_capture(path)
    day = pd.Timestamp("2026-03-10")
    hours = live.local_day_hours(day)
    assert len(hours) == 24 and hours[0] == pd.Timestamp("2026-03-09T22:00Z")
    block = capture.hourly.loc[hours]
    values = live.daily_weather(capture.hourly, day)
    assert values["temperature_2m_mean"] == pytest.approx(block["temperature_2m"].mean())
    assert values["temperature_2m_max"] == pytest.approx(block["temperature_2m"].max())
    assert values["temperature_2m_min"] == pytest.approx(block["temperature_2m"].min())
    assert values["precipitation_sum"] == pytest.approx(block["precipitation"].sum())
    assert values["wind_speed_10m_max"] == pytest.approx(block["wind_speed_10m"].max())
    assert values["relative_humidity_2m_mean"] == pytest.approx(block["relative_humidity_2m"].mean())
    assert values["surface_pressure_mean"] == pytest.approx(block["surface_pressure"].mean())
    assert "shortwave_radiation_sum" not in values


def test_weather_aggregation_handles_dst_days_and_missing_hours(tmp_path):
    assert len(live.local_day_hours(pd.Timestamp("2026-10-25"))) == 25
    assert len(live.local_day_hours(pd.Timestamp("2026-03-29"))) == 23
    payload = capture_payload("2026-03-08T00:00", 96, "2026-03-11T02:00:00+00:00")
    payload["hourly"]["surface_pressure"][60] = None  # 2026-03-10 12:00 UTC
    capture = live.load_capture(write_capture(tmp_path, "gap", payload))
    assert live.daily_weather(capture.hourly, pd.Timestamp("2026-03-10")) is None
    assert live.daily_weather(capture.hourly, pd.Timestamp("2026-03-12")) is None  # past the end


def test_wind_in_km_per_hour_is_converted_and_unknown_units_are_refused(tmp_path):
    kmh = capture_payload("2026-03-08T00:00", 96, "2026-03-11T02:00:00+00:00", wind_unit="km/h")
    ms = capture_payload("2026-03-08T00:00", 96, "2026-03-11T02:00:00+00:00")
    a = live.load_capture(write_capture(tmp_path, "kmh", kmh)).hourly["wind_speed_10m"]
    b = live.load_capture(write_capture(tmp_path, "ms", ms)).hourly["wind_speed_10m"]
    assert np.allclose(a * 3.6, b)
    ms["hourly_units"]["surface_pressure"] = "Pa"
    with pytest.raises(ValueError, match="unit"):
        live.load_capture(write_capture(tmp_path, "pa", ms))


def test_the_most_recent_capture_taken_after_the_day_ended_is_used(landing):
    paths = live.capture_files(landing)
    capture, values = live.choose_capture(paths, pd.Timestamp("2026-03-10"), ISSUED)
    assert capture.path.name == "newer.json.gz"
    # A capture taken after the issue time is not used.
    capture, _ = live.choose_capture(paths, pd.Timestamp("2026-03-10"), pd.Timestamp("2026-03-11T01:00Z"))
    assert capture.path.name == "older.json.gz"


# ---------------------------------------------------------------------------------------
# Refusals: no row on missing, incomplete or stale inputs
# ---------------------------------------------------------------------------------------


@pytest.fixture()
def marts_dir(trained, tmp_path):
    """Evaluation marts built from the synthetic models, plus a manifest.json to refresh."""
    out = tmp_path / "marts"
    frames, diagnostics = marts.build_all(
        trained["models_dir"], trained["eea_dir"], trained["weather_file"], n_replicates=5
    )
    marts.write_marts(frames, out, diagnostics, "2026-03-01T00:00:00Z", "run-test")
    partial = json.loads((out / "manifest.partial.json").read_text(encoding="utf-8"))
    manifest = {k: v for k, v in partial.items() if k not in ("partial", "partial_reason", "missing_required_marts", "blocked_marts")}
    marts.write_json(out / "manifest.json", manifest)
    return out


def run_cli(landing, trained, marts_dir, tmp_path, issued=ISSUED, extra=()):
    store = tmp_path / "live" / "store.parquet"
    argv = [
        "--landing-dir", str(landing), "--models-dir", str(trained["models_dir"]),
        "--marts-dir", str(marts_dir), "--store", str(store), "--issued-at", issued.isoformat(), *extra,
    ]
    code = live.main(argv)
    report = json.loads((store.parent / "last_run.json").read_text(encoding="utf-8"))
    return code, store, report


def _risk_bytes(marts_dir):
    return marts.mart_path(marts_dir, "mart_aq_risk_daily").read_bytes()


def _refused(code, store, report, marts_dir, before, match):
    assert code == 2
    assert report["status"] == "refused"
    assert match in report["reason"], report["reason"]
    assert not store.exists()
    assert _risk_bytes(marts_dir) == before


def test_refuses_when_no_capture_covers_the_as_of_day(landing, trained, marts_dir, tmp_path):
    for path in live.capture_files(landing):
        if path.name != "too_early.json.gz":
            path.unlink()
    before = _risk_bytes(marts_dir)
    _refused(*run_cli(landing, trained, marts_dir, tmp_path), marts_dir, before, "no Open-Meteo capture")


def test_refuses_stale_station_data(landing, trained, marts_dir, tmp_path, capsys):
    before = _risk_bytes(marts_dir)
    late = pd.Timestamp("2026-03-12T06:00:00Z")  # the target day 2026-03-11 ended at 23:00 UTC
    _refused(*run_cli(landing, trained, marts_dir, tmp_path, issued=late), marts_dir, before, "stale station data")
    assert "NO LIVE ROW" in capsys.readouterr().err


def test_refuses_an_incomplete_as_of_day(landing, trained, marts_dir, tmp_path):
    frame = utd_frame()
    day = frame["Start"].dt.normalize() == pd.Timestamp("2026-03-10")
    frame.loc[frame.index[day][:7], "Validity"] = -1  # 17 valid hours
    utd = next(landing.glob("source=eea/endpoint=utd_hourly/*/*.parquet"))
    write_utd(utd, frame)
    before = _risk_bytes(marts_dir)
    _refused(*run_cli(landing, trained, marts_dir, tmp_path), marts_dir, before, "17 valid hours")


def test_refuses_a_missing_feature(landing, trained, marts_dir, tmp_path):
    """The 7-day mean needs 4 of 7 days: with 4 invalid days before t there is no row."""
    frame = utd_frame()
    days = frame["Start"].dt.normalize()
    gap = (days >= pd.Timestamp("2026-03-05")) & (days <= pd.Timestamp("2026-03-08"))
    frame.loc[gap, "Validity"] = -1
    write_utd(next(landing.glob("source=eea/endpoint=utd_hourly/*/*.parquet")), frame)
    before = _risk_bytes(marts_dir)
    _refused(*run_cli(landing, trained, marts_dir, tmp_path), marts_dir, before, "log_pm10_mean7")


def test_refuses_without_station_file_or_model(landing, trained, marts_dir, tmp_path):
    before = _risk_bytes(marts_dir)
    no_models = tmp_path / "no_models"
    no_models.mkdir()
    code, store, report = run_cli(landing, {"models_dir": no_models}, marts_dir, tmp_path)
    _refused(code, store, report, marts_dir, before, "pm10_risk_logreg_live.json does not exist")
    for path in landing.glob("source=eea/endpoint=utd_hourly/*/*.parquet"):
        path.unlink()
    _refused(*run_cli(landing, trained, marts_dir, tmp_path), marts_dir, before, "no landed UTD hourly file")


def test_refuses_an_issue_time_before_the_as_of_day_ended(landing, trained, marts_dir, tmp_path):
    before = _risk_bytes(marts_dir)
    early = pd.Timestamp("2026-03-10T20:00:00Z")
    _refused(*run_cli(landing, trained, marts_dir, tmp_path, issued=early), marts_dir, before, "before the end of the as-of day")


# ---------------------------------------------------------------------------------------
# The published row
# ---------------------------------------------------------------------------------------


def test_live_row_is_scored_stored_and_published_under_the_contract(landing, trained, marts_dir, tmp_path):
    evaluation = pd.read_parquet(marts.mart_path(marts_dir, "mart_aq_risk_daily"))
    code, store_path, report = run_cli(landing, trained, marts_dir, tmp_path)
    assert code == 0, report
    row = report["row"]
    assert row["asof_date"] == "2026-03-10" and row["target_date"] == "2026-03-11"
    assert row["split"] == "live" and row["model_id"] == "pm10_risk_logreg_live"
    assert row["issued_at"] == "2026-03-11T03:00:00Z"
    assert row["predicted_label"] == int(row["probability"] >= row["threshold"])
    assert report["inputs"]["weather_capture"].endswith("newer.json.gz")

    # The probability is the one of the saved pipeline on the same features.
    document = marts.read_json(trained["models_dir"] / marts.LIVE_MODEL_FILE)
    saved = train.load_model(trained["models_dir"] / "pm10_risk_logreg_live.joblib")
    x = np.array([[report["features"][n] for n in document["feature_names"]]])
    assert saved["pipeline"].predict_proba(x)[0, 1] == pytest.approx(row["probability"], abs=1e-12)

    risk = pd.read_parquet(marts.mart_path(marts_dir, "mart_aq_risk_daily"))
    assert schemas.mart_aq_risk_daily_schema.validate(risk) is not None
    live_rows = risk[risk["split"] == "live"]
    assert len(live_rows) == 1
    published = live_rows.iloc[0]
    assert published["target_date"] == pd.Timestamp("2026-03-11").date()
    assert published["issued_at"] == ISSUED
    assert pd.isna(published["actual_label"])
    assert published["label_id"] == dataset.LABEL_ID
    # The evaluation rows are untouched.
    # actual_label reads back as float once the column holds a NULL; the values are the same.
    pd.testing.assert_frame_equal(
        risk[risk["split"] != "live"].reset_index(drop=True), evaluation, check_dtype=False
    )

    drivers = pd.read_parquet(marts.mart_path(marts_dir, "mart_aq_risk_drivers"))
    schemas.mart_aq_risk_drivers_schema.validate(drivers)
    mine = drivers[drivers["model_id"] == "pm10_risk_logreg_live"].sort_values("rank")
    assert list(mine["rank"]) == [1, 2, 3, 4, 5]
    assert set(mine["feature"]) <= set(document["feature_names"])

    card = pd.read_parquet(marts.mart_path(marts_dir, "mart_model_card"))
    assert "pm10_risk_logreg_live" in set(card["model_id"])

    manifest = json.loads((marts_dir / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["marts"]["mart_aq_risk_daily"]
    assert entry["rows"] == len(risk) and entry["max_target_date"] == "2026-03-11"
    assert manifest["marts"]["mart_aq_risk_drivers"]["rows"] == len(drivers)
    assert "+live-" in manifest["run_id"] and manifest["generated_at_utc"] == "2026-03-11T03:00:00Z"
    schema = json.loads((REPO_ROOT / "contracts" / "manifest.schema.json").read_text(encoding="utf-8"))
    sub = schema["properties"]["marts"]["properties"]["mart_aq_risk_daily"]
    from jsonschema import Draft7Validator

    assert not list(Draft7Validator(sub).iter_errors(entry))

    # A later issue for the same target day replaces the row; the store keeps one row per key.
    later = ISSUED + pd.Timedelta(hours=6)
    code, _, _ = run_cli(landing, trained, marts_dir, tmp_path, issued=later)
    assert code == 0
    store = pd.read_parquet(store_path)
    assert len(store) == 1 and store["issued_at"].iloc[0] == later
    risk = pd.read_parquet(marts.mart_path(marts_dir, "mart_aq_risk_daily"))
    assert (risk["split"] == "live").sum() == 1


def test_live_split_is_allowed_by_every_contract_artifact():
    assert "live" in schemas.VALID_SPLITS
    api = yaml.safe_load((REPO_ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    params = {p["name"]: p for p in api["paths"]["/api/v1/aq/risk"]["get"]["parameters"]}
    assert "live" in params["split"]["schema"]["enum"]
    split_items = api["components"]["schemas"]["AqRiskResponse"]["properties"]["split"]["items"]
    assert "live" in split_items["enum"]


def test_marts_rebuild_keeps_the_live_rows(landing, trained, marts_dir, tmp_path):
    code, store_path, _ = run_cli(landing, trained, marts_dir, tmp_path)
    assert code == 0
    frames, _ = marts.build_all(
        trained["models_dir"], trained["eea_dir"], trained["weather_file"], n_replicates=5, live_store=store_path
    )
    risk = frames["mart_aq_risk_daily"]
    assert (risk["split"] == "live").sum() == 1
    assert marts.validate("mart_aq_risk_daily", marts.to_table("mart_aq_risk_daily", risk)) == []
    drivers = frames["mart_aq_risk_drivers"]
    assert marts.validate("mart_aq_risk_drivers", marts.to_table("mart_aq_risk_drivers", drivers)) == []
    assert len(drivers[drivers["model_id"] == "pm10_risk_logreg_live"]) == 5


def test_tampered_store_is_rejected(landing, trained, marts_dir, tmp_path):
    code, store_path, _ = run_cli(landing, trained, marts_dir, tmp_path)
    store = pd.read_parquet(store_path)
    store.loc[0, "probability"] = store.loc[0, "probability"] / 2
    documents = {d["model"]: d for d in marts.model_documents(trained["models_dir"])}
    with pytest.raises(ValueError, match="does not reproduce"):
        marts.live_driver_rows(store, documents)
