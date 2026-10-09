"""Label definition, thresholds, loading and split boundaries of the PM10 risk model."""

import gzip
import json

import numpy as np
import pandas as pd
import pytest
from ml_helpers import synthetic_days

from plovdiv.ml import dataset


def make_pm10(first="2013-01-01", last="2018-12-31", seed=1) -> pd.Series:
    days = synthetic_days(first, last).astype("datetime64[ns]")
    rng = np.random.default_rng(seed)
    return pd.Series(rng.gamma(4.0, 8.0, size=len(days)) + 5.0, index=days)


def month_values(series: pd.Series, year: int, month: int) -> np.ndarray:
    selected = series[(series.index.year == year) & (series.index.month == month)]
    return selected.dropna().to_numpy()


def test_threshold_is_the_75th_percentile_of_the_same_month_in_the_three_previous_years():
    pm10 = make_pm10()
    thresholds = dataset.monthly_thresholds(pm10).set_index(["year", "month"])["threshold"]
    history = np.concatenate([month_values(pm10, y, 3) for y in (2013, 2014, 2015)])
    assert thresholds[(2016, 3)] == pytest.approx(np.quantile(history, 0.75))


def test_threshold_ignores_the_current_and_later_years():
    pm10 = make_pm10()
    before = dataset.monthly_thresholds(pm10).set_index(["year", "month"])["threshold"]
    changed = pm10.copy()
    changed[changed.index.year >= 2016] *= 10.0
    after = dataset.monthly_thresholds(changed).set_index(["year", "month"])["threshold"]
    assert after[(2016, 5)] == before[(2016, 5)]
    # The 2017 threshold sees the changed 2016 data, as it should.
    assert after[(2017, 5)] != before[(2017, 5)]


def test_threshold_changes_when_a_history_year_changes():
    pm10 = make_pm10()
    before = dataset.monthly_thresholds(pm10).set_index(["year", "month"])["threshold"]
    changed = pm10.copy()
    changed[(changed.index.year == 2015) & (changed.index.month == 7)] += 100.0
    after = dataset.monthly_thresholds(changed).set_index(["year", "month"])["threshold"]
    assert after[(2016, 7)] > before[(2016, 7)]
    assert after[(2016, 8)] == before[(2016, 8)]


def test_first_usable_year_has_three_full_history_years():
    thresholds = dataset.monthly_thresholds(make_pm10()).set_index(["year", "month"])["threshold"]
    assert thresholds.loc[2013:2015].isna().all()
    assert thresholds.loc[2016:2018].notna().all()


def test_a_history_month_with_too_few_valid_days_gives_no_threshold():
    pm10 = make_pm10()
    pm10[(pm10.index.year == 2014) & (pm10.index.month == 2)] = np.nan
    thresholds = dataset.monthly_thresholds(pm10).set_index(["year", "month"])["threshold"]
    assert np.isnan(thresholds[(2016, 2)]) and np.isnan(thresholds[(2017, 2)])
    assert not np.isnan(thresholds[(2018, 2)])


def test_threshold_for_a_day_uses_its_own_year_and_month():
    pm10 = make_pm10()
    thresholds = dataset.monthly_thresholds(pm10)
    days = pd.DatetimeIndex(["2016-12-31", "2017-01-01"])
    looked_up = dataset.threshold_for_days(days, thresholds)
    table = thresholds.set_index(["year", "month"])["threshold"]
    assert looked_up.iloc[0] == table[(2016, 12)]
    assert looked_up.iloc[1] == table[(2017, 1)]


def test_climatology_rate_uses_only_the_previous_year_of_the_month():
    pm10 = make_pm10()
    thresholds = dataset.monthly_thresholds(pm10)
    day = pd.DatetimeIndex(["2017-06-15"])
    rate = dataset.climatology_rate(pm10, thresholds, day).iloc[0]
    threshold = thresholds.set_index(["year", "month"])["threshold"][(2017, 6)]
    expected = np.mean(month_values(pm10, 2016, 6) > threshold)
    assert rate == pytest.approx(expected)


def test_split_boundaries_on_the_target_date():
    days = pd.DatetimeIndex(
        [
            "2015-12-31",
            "2016-01-01",
            "2020-12-31",
            "2021-01-01",
            "2022-12-31",
            "2023-01-01",
            "2025-12-31",
            "2026-01-01",
        ]
    )
    names = dataset.assign_split(days).tolist()
    assert names[0] != names[0]  # NaN before the first period
    assert names[1:7] == ["train", "train", "validation", "validation", "test", "test"]
    assert names[7] != names[7]


def test_split_periods_do_not_overlap_and_follow_each_other():
    periods = list(dataset.SPLITS.values())
    for (_, end), (start, _) in zip(periods, periods[1:]):
        assert pd.Timestamp(end) < pd.Timestamp(start)
        assert pd.Timestamp(start) - pd.Timestamp(end) == pd.Timedelta(days=1)


def test_load_eea_daily_casts_values_and_drops_invalid_rows(tmp_path):
    frame = pd.DataFrame(
        {
            "Start": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]),
            "Value": ["12.500000000000000000", "30.0", "-1.0", "not a number"],
            "Validity": [1, 0, 1, 1],
        }
    )
    path = tmp_path / "x.parquet"
    frame.to_parquet(path, index=False)
    series = dataset.load_eea_daily(path)
    assert series.index.tolist() == [pd.Timestamp("2020-01-01")]
    assert series.iloc[0] == 12.5


def test_load_eea_daily_rejects_a_day_reported_twice(tmp_path):
    frame = pd.DataFrame(
        {
            "Start": pd.to_datetime(["2020-01-01", "2020-01-01"]),
            "Value": ["1.0", "2.0"],
            "Validity": [1, 1],
        }
    )
    path = tmp_path / "x.parquet"
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError):
        dataset.load_eea_daily(path)


def test_load_weather_reads_plain_and_gzipped_responses(tmp_path):
    days = ["2020-01-01", "2020-01-02", "2020-01-03"]
    payload = {
        "daily": {"time": days, **{c: [1.0, 2.0, 3.0] for c in dataset.WEATHER_COLUMNS}},
    }
    plain = tmp_path / "a.json"
    plain.write_text(json.dumps(payload))
    zipped = tmp_path / "a.json.gz"
    with gzip.open(zipped, "wb") as handle:
        handle.write(json.dumps(payload).encode())
    one = dataset.load_weather(plain)
    two = dataset.load_weather(zipped)
    pd.testing.assert_frame_equal(one, two)
    assert one.index[0] == pd.Timestamp("2020-01-01")
    assert list(one.columns) == list(dataset.WEATHER_COLUMNS)


def test_daily_table_is_contiguous_and_keeps_missing_days_as_nan():
    days = pd.DatetimeIndex(["2020-01-01", "2020-01-02", "2020-01-04"]).astype("datetime64[ns]")
    pm10 = pd.Series([10.0, 20.0, 40.0], index=days)
    weather = pd.DataFrame(
        {c: 1.0 for c in dataset.WEATHER_COLUMNS},
        index=pd.date_range("2020-01-01", "2020-01-04", freq="D").astype("datetime64[ns]"),
    )
    table = dataset.build_daily_table(pm10, pm10.copy(), weather)
    assert len(table) == 4
    assert np.isnan(table.loc["2020-01-03", "pm10"])
    assert table.loc["2020-01-04", "pm10"] == 40.0
