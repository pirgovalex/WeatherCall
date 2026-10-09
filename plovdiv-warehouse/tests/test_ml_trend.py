"""Weather-adjusted monthly means: behaviour on synthetic series with a known weather effect."""

import numpy as np
import pandas as pd
import pytest

from plovdiv.ml import trend

DAYS = pd.date_range("2013-01-01", "2025-12-31", freq="D")
WIND_COEF_HEATING = -0.12  # change of log PM10 per m/s of wind in the heating season
WIND_COEF_OTHER = -0.04
TEMP_COEF = 0.01
ANOMALY_YEAR = 2018
ANOMALY_WIND = 3.0  # m/s added to the daily maximum wind in the anomaly year


def make_weather(seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(DAYS)
    doy = DAYS.dayofyear.to_numpy()
    temp = 13 + 11 * np.sin(2 * np.pi * (doy - 110) / 365.25) + rng.normal(0, 3.0, n)
    wind = np.clip(rng.gamma(4.0, 1.0, n), 0.5, 20.0)
    wind = wind + np.where(DAYS.year == ANOMALY_YEAR, ANOMALY_WIND, 0.0)
    return pd.DataFrame(
        {
            "temp_mean_c": temp,
            "temp_range_c": np.clip(rng.normal(9.0, 3.0, n), 1.0, None),
            "log1p_precip_mm": np.log1p(np.clip(rng.gamma(0.4, 4.0, n) - 0.5, 0, None)),
            "wind_max_ms": wind,
            "radiation_mj_m2": np.clip(12 + 10 * np.sin(2 * np.pi * (doy - 80) / 365.25), 1, None)
            + rng.normal(0, 1.0, n),
            "rh_mean_pct": np.clip(rng.normal(70, 12, n), 20, 100),
            "pressure_mean_hpa": 1000 + rng.normal(0, 6, n),
        },
        index=DAYS,
    )


def make_pm10(weather: pd.DataFrame, yearly_log_trend: float, seed: int = 2) -> pd.Series:
    """PM10 whose only weather dependence is wind (season-specific) and temperature."""
    rng = np.random.default_rng(seed)
    n = len(DAYS)
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = 0.5 * noise[i - 1] + rng.normal(0, 0.2)
    month = DAYS.month.to_numpy()
    heating = trend.heating_season(month)
    season = 0.5 * np.cos(2 * np.pi * (DAYS.dayofyear.to_numpy() - 15) / 365.25)
    wind_effect = np.where(heating, WIND_COEF_HEATING, WIND_COEF_OTHER) * (weather["wind_max_ms"].to_numpy() - 4.0)
    temp_effect = TEMP_COEF * (weather["temp_mean_c"].to_numpy() - 13.0)
    time_effect = yearly_log_trend * (DAYS.year.to_numpy() - 2013)
    log_y = 3.4 + season + wind_effect + temp_effect + time_effect + noise
    return pd.Series(np.exp(log_y), index=DAYS)


def yearly(result: trend.TrendResult, column: str) -> pd.Series:
    m = result.monthly
    usable = m["adjusted_mean"].notna()
    return m[usable].groupby(pd.DatetimeIndex(m.loc[usable, "month"]).year)[column].mean()


@pytest.fixture(scope="module")
def weather():
    return make_weather()


@pytest.fixture(scope="module")
def flat_result(weather):
    """No trend over time, but one year with unusually strong wind."""
    return trend.weather_adjusted_monthly(make_pm10(weather, 0.0), weather, n_replicates=40)


@pytest.fixture(scope="module")
def declining_result(weather):
    return trend.weather_adjusted_monthly(make_pm10(weather, -0.05), weather, n_replicates=40)


def test_weather_anomaly_year_is_removed_when_there_is_no_trend(flat_result):
    observed = yearly(flat_result, "observed_mean")
    adjusted = yearly(flat_result, "adjusted_mean")
    others = [y for y in observed.index if y != ANOMALY_YEAR]
    # The windy year looks clearly cleaner in the observations.
    assert observed[ANOMALY_YEAR] < 0.88 * observed[others].mean()
    # After adjustment it is back in line with the other years.
    assert abs(adjusted[ANOMALY_YEAR] / adjusted[others].mean() - 1.0) < 0.05
    assert adjusted.max() / adjusted.min() < 1.12
    assert observed.max() / observed.min() > adjusted.max() / adjusted.min()


def test_true_decline_is_kept_and_anomaly_year_sits_on_the_trend(declining_result):
    adjusted = yearly(declining_result, "adjusted_mean")
    observed = yearly(declining_result, "observed_mean")
    years = adjusted.index.to_numpy(dtype=float)
    slope = np.polyfit(years, np.log(adjusted.to_numpy()), 1)[0]
    assert slope == pytest.approx(-0.05, abs=0.008)
    fit = np.polyval(np.polyfit(years, np.log(adjusted.to_numpy()), 1), float(ANOMALY_YEAR))
    assert abs(np.log(adjusted[ANOMALY_YEAR]) - fit) < 0.05
    # The observed value of the anomaly year is pulled well below the same line.
    fit_obs = np.polyval(np.polyfit(years, np.log(observed.to_numpy()), 1), float(ANOMALY_YEAR))
    assert np.log(observed[ANOMALY_YEAR]) - fit_obs < -0.08


def test_interval_contains_the_estimate_and_is_ordered(flat_result):
    m = flat_result.monthly.dropna(subset=["adjusted_mean"])
    assert len(m) == 156
    assert (m["adjusted_lo"] <= m["adjusted_mean"]).all()
    assert (m["adjusted_mean"] <= m["adjusted_hi"]).all()
    assert (m["adjusted_hi"] > m["adjusted_lo"]).all()


def test_adjusted_equals_observed_when_weather_is_the_climatology(weather):
    """If every day has its month's long-run weather, there is nothing to adjust."""
    flat_weather = weather.copy()
    for column in flat_weather.columns:
        flat_weather[column] = flat_weather.groupby(flat_weather.index.month)[column].transform("mean")
    pm10 = make_pm10(weather, -0.03)
    result = trend.weather_adjusted_monthly(pm10, flat_weather, n_replicates=5)
    m = result.monthly
    assert np.allclose(m["adjusted_mean"], m["observed_mean"], atol=1e-3)


def test_month_with_few_days_has_no_adjusted_value_and_empty_month_is_kept(weather):
    pm10 = make_pm10(weather, 0.0)
    pm10 = pm10[~((pm10.index.year == 2020) & (pm10.index.month == 3) & (pm10.index.day > 10))]
    pm10 = pm10[~((pm10.index.year == 2021) & (pm10.index.month == 7))]
    result = trend.weather_adjusted_monthly(pm10, weather, n_replicates=10)
    m = result.monthly.set_index(pd.DatetimeIndex(result.monthly["month"]))
    short = m.loc["2020-03-01"]
    assert short["n_days"] == 10
    assert not np.isnan(short["observed_mean"])
    assert np.isnan(short[["adjusted_mean", "adjusted_lo", "adjusted_hi"]].astype(float)).all()
    empty = m.loc["2021-07-01"]
    assert empty["n_days"] == 0 and np.isnan(empty["observed_mean"]) and np.isnan(empty["adjusted_mean"])
    assert len(m) == 156  # the empty month stays as a row


def test_same_seed_gives_identical_output_and_other_seed_changes_the_interval(weather):
    pm10 = make_pm10(weather, 0.0)
    a = trend.weather_adjusted_monthly(pm10, weather, n_replicates=10, seed=3)
    b = trend.weather_adjusted_monthly(pm10, weather, n_replicates=10, seed=3)
    c = trend.weather_adjusted_monthly(pm10, weather, n_replicates=10, seed=4)
    pd.testing.assert_frame_equal(a.monthly, b.monthly)
    assert np.array_equal(a.replicates, b.replicates, equal_nan=True)
    assert not a.monthly["adjusted_lo"].equals(c.monthly["adjusted_lo"])
    # The point estimate does not depend on the bootstrap.
    pd.testing.assert_series_equal(a.monthly["adjusted_mean"], c.monthly["adjusted_mean"])


def test_non_positive_values_are_not_used(weather):
    pm10 = make_pm10(weather, 0.0)
    clean = trend.weather_adjusted_monthly(pm10, weather, n_replicates=5)
    broken = pm10.copy()
    broken.iloc[100] = 0.0
    result = trend.weather_adjusted_monthly(broken, weather, n_replicates=5)
    assert result.monthly["n_days"].sum() == clean.monthly["n_days"].sum() - 1


def test_yearly_summary_reports_the_share_of_the_fall_that_remains(declining_result):
    summary = trend.yearly_summary(declining_result, 2013, 2025)
    change = summary["change"]
    assert change["adjusted_first"] > change["adjusted_last"]
    # With a purely time-driven decline the weather explains little of the fall.
    assert 0.8 < change["remaining_share_of_fall"] < 1.25
    assert change["remaining_share_lo"] <= change["remaining_share_of_fall"] * 1.05
    assert change["adjusted_change_pct_lo"] <= change["adjusted_change_pct_hi"]
    assert set(summary["years"]["months"]) == {12}


def test_missing_weather_columns_are_rejected(weather):
    with pytest.raises(ValueError, match="missing columns"):
        trend.weather_adjusted_monthly(make_pm10(weather, 0.0), weather.drop(columns=["wind_max_ms"]))
