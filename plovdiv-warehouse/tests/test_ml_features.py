"""Feature construction: no lookahead, calendar-day windows, label and feature sets."""

import numpy as np
import pandas as pd
import pytest
from ml_helpers import synthetic_daily_table

from plovdiv.ml import dataset, features

ALL_FEATURES = features.VARIANTS[features.PRIMARY_VARIANT]
ASOF = pd.Timestamp("2019-02-13")  # a Wednesday; the target day is Thursday 2019-02-14


@pytest.fixture(scope="module")
def daily():
    return synthetic_daily_table(seed=3)


@pytest.fixture(scope="module")
def table(daily):
    return features.build_model_table(daily)


def test_features_at_t_do_not_depend_on_pm_after_t_or_weather_after_t_plus_1(daily, table):
    altered = daily.copy()
    rng = np.random.default_rng(99)
    after_t = altered.index > ASOF
    altered.loc[after_t, ["pm10", "pm25"]] = rng.uniform(1, 500, size=(after_t.sum(), 2))
    weather_cols = list(dataset.WEATHER_COLUMNS)
    beyond = altered.index > ASOF + pd.Timedelta(days=1)
    altered.loc[beyond, weather_cols] = rng.uniform(1, 50, size=(beyond.sum(), len(weather_cols)))
    other = features.build_model_table(altered)
    pd.testing.assert_series_equal(
        table.loc[ASOF, ALL_FEATURES], other.loc[ASOF, ALL_FEATURES], check_names=False
    )


def test_the_label_depends_on_pm10_of_the_target_day_and_nothing_later(daily, table):
    target = ASOF + pd.Timedelta(days=1)
    altered = daily.copy()
    altered.loc[target, "pm10"] = 1000.0
    other = features.build_model_table(altered)
    assert other.loc[ASOF, "label"] == 1.0
    assert other.loc[ASOF, "pm10_target"] == 1000.0
    later = daily.copy()
    later.loc[later.index > target, "pm10"] = 1000.0
    again = features.build_model_table(later)
    assert again.loc[ASOF, "label"] == table.loc[ASOF, "label"]


def test_next_day_weather_columns_hold_the_weather_of_the_next_calendar_day(daily, table):
    expected = np.log1p(daily.loc[ASOF + pd.Timedelta(days=1), "precipitation_sum"])
    assert table.loc[ASOF, "log1p_precip_mm_t1"] == pytest.approx(expected)
    assert table.loc[ASOF, "log1p_precip_mm_t"] == pytest.approx(np.log1p(daily.loc[ASOF, "precipitation_sum"]))


def test_label_definition(table):
    rows = table[table["label"].notna()]
    assert ((rows["pm10_target"] > rows["label_threshold"]).astype(float) == rows["label"]).all()
    assert (rows["target_date"] == rows.index + pd.Timedelta(days=1)).all()
    assert rows["target_date"].min() == pd.Timestamp("2016-01-01")


def test_label_is_missing_when_the_target_day_is_missing(daily):
    altered = daily.copy()
    altered.loc[ASOF + pd.Timedelta(days=1), "pm10"] = np.nan
    other = features.build_model_table(altered)
    assert np.isnan(other.loc[ASOF, "label"])
    assert np.isnan(other.loc[ASOF, "label_gt50"])


def test_secondary_label_is_the_eu_limit_exceedance(table):
    rows = table[table["label_gt50"].notna()]
    assert ((rows["pm10_target"] > 50.0).astype(float) == rows["label_gt50"]).all()


def test_windows_count_calendar_days_not_rows(daily):
    altered = daily.copy()
    altered.loc[ASOF - pd.Timedelta(days=1), "pm10"] = np.nan
    other = features.build_model_table(altered)
    row = other.loc[ASOF]
    assert np.isnan(row["log_pm10_t_minus_1"])
    expected = np.log(np.mean([daily.loc[ASOF, "pm10"], daily.loc[ASOF - pd.Timedelta(days=2), "pm10"]]))
    assert row["log_pm10_mean3"] == pytest.approx(expected)


def test_rolling_means_cover_the_stated_number_of_days(daily, table):
    window3 = daily["pm10"].loc[ASOF - pd.Timedelta(days=2) : ASOF].mean()
    window7 = daily["pm10"].loc[ASOF - pd.Timedelta(days=6) : ASOF].mean()
    assert table.loc[ASOF, "log_pm10_mean3"] == pytest.approx(np.log(window3))
    assert table.loc[ASOF, "log_pm10_mean7"] == pytest.approx(np.log(window7))
    assert table.loc[ASOF, "log_pm10_t_minus_1"] == pytest.approx(
        np.log(daily.loc[ASOF - pd.Timedelta(days=1), "pm10"])
    )


def test_calendar_features_describe_the_target_day(table):
    thursday = table.loc[ASOF]
    assert thursday["is_weekend"] == 0.0
    assert thursday["heating_season"] == 1.0
    assert thursday["month_sin"] == pytest.approx(np.sin(2 * np.pi * 2 / 12))
    friday = table.loc[ASOF + pd.Timedelta(days=2)]  # as-of Friday, the target day is a Saturday
    assert friday["is_weekend"] == 1.0
    summer = table.loc[pd.Timestamp("2019-07-10")]
    assert summer["heating_season"] == 0.0
    # The month of the target day, not of the as-of day, is used at a month boundary.
    boundary = table.loc[pd.Timestamp("2019-09-30")]
    assert boundary["heating_season"] == 1.0


def test_persistence_flag_compares_today_with_the_threshold_of_today(daily, table):
    thresholds = dataset.monthly_thresholds(daily["pm10"]).set_index(["year", "month"])["threshold"]
    day = pd.Timestamp("2019-05-21")
    expected = float(daily.loc[day, "pm10"] > thresholds[(2019, 5)])
    assert table.loc[day, "bad_today"] == expected


def test_variant_feature_sets():
    with_next = features.VARIANTS["with_next_day_weather"]
    without = features.VARIANTS["no_next_day_weather"]
    assert set(features.WEATHER_NEXT_FEATURES) <= set(with_next)
    assert not set(features.WEATHER_NEXT_FEATURES) & set(without)
    assert set(without) < set(with_next)
    assert not set(features.WEATHER_T_FEATURES) & set(features.VARIANTS["no_weather"])
    assert features.VARIANTS["pm10_only"] == features.PM10_FEATURES + features.CALENDAR_FEATURES
    for columns in features.VARIANTS.values():
        assert len(columns) == len(set(columns))


def test_model_table_requires_every_calendar_day(daily):
    with pytest.raises(ValueError):
        features.build_model_table(daily.drop(daily.index[100]))


def test_usable_rows_need_label_split_and_all_features(table):
    mask = features.usable_rows(table)
    assert table.loc[mask, ALL_FEATURES].notna().all().all()
    assert table.loc[mask, "label"].notna().all()
    assert table.loc[mask, "split"].notna().all()
