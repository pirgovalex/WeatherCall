"""Weather-adjusted monthly means of daily PM10.

Question answered: how would the monthly mean PM10 of each month have looked if the weather of
that month had been the long-run average weather of the same calendar month? Differences between
years that remain after this adjustment are not explained by the weather variables used here.

Method (details and limits in docs/METHODS_trend.md)

1. Fit one ordinary least squares regression of log(daily PM10) on
   * month-of-year effects (12 dummies, no common intercept),
   * seven daily weather variables, each with a separate slope in the heating season
     (October to March) and outside it,
   and no year term, using all valid days of all years.
2. Each valid day is moved to climatological weather: its value is multiplied by
   exp(slope . (climatological weather of its calendar month - weather of the day)).
   This equals "residual of the day plus the fitted value at the climatological weather,
   back-transformed", because the day's own residual is kept and the month effect cancels.
   The monthly adjusted mean is the mean of these moved daily values, so no separate
   smearing constant is needed: the retained residuals are the smearing.
3. The interval is a percentile interval from a block bootstrap. In every replicate the days of
   each station-month are resampled in blocks of seven consecutive days (with replacement,
   within the same month), the regression is refitted on the resampled days and the monthly
   adjusted means are recomputed. It therefore reflects both the uncertainty of the weather
   coefficients and the day-to-day scatter inside a month.
4. A month with fewer than MIN_VALID_DAYS valid days gets no adjusted value and no interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from plovdiv.ml import features

WEATHER_FEATURES = list(features.WEATHER_BASE)
MIN_VALID_DAYS = 15
BLOCK_DAYS = 7
N_REPLICATES = 1000
SEED = 20261007
INTERVAL_PERCENTILES = (5.0, 95.0)
DECIMALS = 4

_N_MONTH_TERMS = 12


def heating_season(month: np.ndarray) -> np.ndarray:
    """True for October to March, the heating season used by the risk model as well."""
    month = np.asarray(month)
    return (month >= 10) | (month <= 3)


@dataclass
class TrendResult:
    """Monthly table, bootstrap replicates and fit diagnostics for one station and pollutant."""

    monthly: pd.DataFrame
    replicates: np.ndarray
    model: dict = field(default_factory=dict)
    n_clipped_to_estimate: int = 0


def _design(month: np.ndarray, z: np.ndarray) -> np.ndarray:
    heat = heating_season(month).astype(float)[:, None]
    month_dummies = (month[:, None] == np.arange(1, 13)[None, :]).astype(float)
    return np.hstack([month_dummies, z * heat, z * (1.0 - heat)])


def _fit(design: np.ndarray, log_y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(design, log_y, rcond=None)
    return beta


def _slopes(beta: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    k = (len(beta) - _N_MONTH_TERMS) // 2
    start = _N_MONTH_TERMS
    return beta[start : start + k], beta[start + k : start + 2 * k]


def _log_factor(beta: np.ndarray, heat: np.ndarray, delta: np.ndarray) -> np.ndarray:
    heating_slopes, other_slopes = _slopes(beta)
    slopes = np.where(heat[:, None], heating_slopes[None, :], other_slopes[None, :])
    return np.sum(delta * slopes, axis=1)


def _group_means(group: np.ndarray, values: np.ndarray, n_groups: int) -> np.ndarray:
    counts = np.bincount(group, minlength=n_groups)
    sums = np.bincount(group, weights=values, minlength=n_groups)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)


def _blocks(group: np.ndarray, day_of_month: np.ndarray):
    """Row index arrays of the resampling blocks, the group of each block and block counts."""
    key = group * 8 + (day_of_month - 1) // BLOCK_DAYS
    order = np.argsort(key, kind="stable")
    sorted_key = key[order]
    boundaries = np.flatnonzero(np.diff(sorted_key)) + 1
    block_rows = np.split(order, boundaries)
    block_group = group[np.array([rows[0] for rows in block_rows])]
    blocks_in_group = np.bincount(block_group)
    first_block = np.concatenate([[0], np.cumsum(blocks_in_group)[:-1]])
    return block_rows, block_group, blocks_in_group, first_block


def weather_adjusted_monthly(
    daily: pd.Series,
    weather: pd.DataFrame,
    *,
    n_replicates: int = N_REPLICATES,
    seed: int = SEED,
    min_days: int = MIN_VALID_DAYS,
) -> TrendResult:
    """Observed and weather-adjusted monthly means of a daily series.

    `daily` holds the valid daily means (one per day, indexed by calendar day). `weather` has
    one row per calendar day and the columns of WEATHER_FEATURES; it should cover the whole
    available record, because the climatological weather is the mean over all of its days.
    Only days with a positive value and complete weather are used, for the observed mean as
    well, so that both columns describe the same days.
    """
    missing = [c for c in WEATHER_FEATURES if c not in weather.columns]
    if missing:
        raise ValueError(f"weather is missing columns {missing}")
    weather = weather[WEATHER_FEATURES].astype(float)
    if weather.index.has_duplicates or daily.index.has_duplicates:
        raise ValueError("daily series and weather need one row per day")

    scale_mean = weather.mean().to_numpy()
    scale_sd = weather.std(ddof=0).to_numpy()
    if np.any(scale_sd <= 0):
        raise ValueError("a weather variable is constant")
    clim = (
        weather.groupby(weather.index.month).mean().reindex(range(1, 13)).to_numpy() - scale_mean
    ) / scale_sd
    if np.isnan(clim).any():
        raise ValueError("weather record does not cover all calendar months")

    joined = pd.DataFrame({"y": daily.astype(float)}).join(weather, how="inner").dropna()
    joined = joined[joined["y"] > 0].sort_index()
    if joined.empty:
        raise ValueError("no valid days")

    days = pd.DatetimeIndex(joined.index)
    month = days.month.to_numpy()
    year = days.year.to_numpy()
    y = joined["y"].to_numpy()
    log_y = np.log(y)
    z = (joined[WEATHER_FEATURES].to_numpy() - scale_mean) / scale_sd
    heat = heating_season(month)
    delta = clim[month - 1] - z

    first_month = pd.Timestamp(year=int(year[0]), month=int(month[0]), day=1)
    last_month = pd.Timestamp(year=int(year[-1]), month=int(month[-1]), day=1)
    months = pd.date_range(first_month, last_month, freq="MS")
    n_groups = len(months)
    group = (year - first_month.year) * 12 + (month - first_month.month)

    design = _design(month, z)
    beta = _fit(design, log_y)
    adjusted_days = y * np.exp(_log_factor(beta, heat, delta))
    n_days = np.bincount(group, minlength=n_groups)
    observed = _group_means(group, y, n_groups)
    adjusted = _group_means(group, adjusted_days, n_groups)
    estimable = n_days >= min_days
    adjusted = np.where(estimable, adjusted, np.nan)

    # Block bootstrap within station-months.
    block_rows, block_group, blocks_in_group, first_block = _blocks(group, days.day.to_numpy())
    rng = np.random.default_rng(seed)
    replicates = np.full((n_replicates, n_groups), np.nan)
    n_blocks = len(block_rows)
    for r in range(n_replicates):
        u = rng.random(n_blocks)
        nb = blocks_in_group[block_group]
        chosen = first_block[block_group] + np.minimum((u * nb).astype(int), nb - 1)
        rows = np.concatenate([block_rows[c] for c in chosen])
        beta_r = _fit(design[rows], log_y[rows])
        moved = y[rows] * np.exp(_log_factor(beta_r, heat[rows], delta[rows]))
        means = _group_means(group[rows], moved, n_groups)
        replicates[r] = np.where(estimable, means, np.nan)

    lo_pct, hi_pct = INTERVAL_PERCENTILES
    lo = np.full(n_groups, np.nan)
    hi = np.full(n_groups, np.nan)
    if estimable.any():
        lo[estimable] = np.percentile(replicates[:, estimable], lo_pct, axis=0)
        hi[estimable] = np.percentile(replicates[:, estimable], hi_pct, axis=0)
    # The interval must contain the estimate; a percentile interval can miss it when the
    # bootstrap distribution is biased. Count how often the bound had to be moved.
    clipped = int(np.sum(estimable & ((lo > adjusted) | (hi < adjusted))))
    lo = np.where(estimable, np.minimum(lo, adjusted), np.nan)
    hi = np.where(estimable, np.maximum(hi, adjusted), np.nan)

    monthly = pd.DataFrame(
        {
            "month": months,
            "n_days": n_days.astype("int64"),
            "observed_mean": observed,
            "adjusted_mean": adjusted,
            "adjusted_lo": lo,
            "adjusted_hi": hi,
        }
    )
    for column in ("observed_mean", "adjusted_mean", "adjusted_lo", "adjusted_hi"):
        monthly[column] = monthly[column].round(DECIMALS)

    fitted = design @ beta
    residual = log_y - fitted
    total = log_y - log_y.mean()
    heating_slopes, other_slopes = _slopes(beta)
    model = {
        "n_days_used": int(len(y)),
        "first_day": str(days[0].date()),
        "last_day": str(days[-1].date()),
        "r_squared_log": float(1.0 - np.sum(residual**2) / np.sum(total**2)),
        "residual_sd_log": float(np.sqrt(np.mean(residual**2))),
        "slope_per_sd": {
            name: {
                "heating_season": float(heating_slopes[i]),
                "other_months": float(other_slopes[i]),
            }
            for i, name in enumerate(WEATHER_FEATURES)
        },
        "weather_sd": {name: float(scale_sd[i]) for i, name in enumerate(WEATHER_FEATURES)},
        "n_replicates": int(n_replicates),
        "seed": int(seed),
        "block_days": BLOCK_DAYS,
        "min_valid_days": int(min_days),
    }
    return TrendResult(monthly=monthly, replicates=replicates, model=model, n_clipped_to_estimate=clipped)


def yearly_summary(result: TrendResult, first_year: int, last_year: int) -> dict:
    """Yearly observed and adjusted means and the change between two years.

    Only months with an adjusted value enter, in the observed mean too, so both are means over
    the same months. Intervals are percentile intervals of the yearly mean over the bootstrap
    replicates. `remaining_share_of_fall` is the fall of the adjusted mean divided by the fall
    of the observed mean (1 means weather explains none of the fall).
    """
    monthly = result.monthly
    usable = monthly["adjusted_mean"].notna().to_numpy()
    years = pd.DatetimeIndex(monthly["month"]).year.to_numpy()
    lo_pct, hi_pct = INTERVAL_PERCENTILES
    rows = []
    year_means: dict[int, np.ndarray] = {}
    for year in sorted(set(years[usable])):
        mask = usable & (years == year)
        rep = np.nanmean(result.replicates[:, mask], axis=1)
        year_means[int(year)] = rep
        rows.append(
            {
                "year": int(year),
                "months": int(mask.sum()),
                "observed_mean_ugm3": float(monthly.loc[mask, "observed_mean"].mean()),
                "adjusted_mean_ugm3": float(monthly.loc[mask, "adjusted_mean"].mean()),
                "adjusted_lo_ugm3": float(np.percentile(rep, lo_pct)),
                "adjusted_hi_ugm3": float(np.percentile(rep, hi_pct)),
            }
        )
    table = pd.DataFrame(rows)
    out = {"years": table}
    if first_year in year_means and last_year in year_means:
        a = table.set_index("year").loc[first_year]
        b = table.set_index("year").loc[last_year]
        obs_fall = a["observed_mean_ugm3"] - b["observed_mean_ugm3"]
        adj_fall = a["adjusted_mean_ugm3"] - b["adjusted_mean_ugm3"]
        # Fall of the adjusted mean in each replicate, relative to the first year.
        rep_first, rep_last = year_means[first_year], year_means[last_year]
        rep_change_pct = 100.0 * (rep_last - rep_first) / rep_first
        rep_share = (rep_first - rep_last) / obs_fall
        out["change"] = {
            "first_year": first_year,
            "last_year": last_year,
            "observed_first": float(a["observed_mean_ugm3"]),
            "observed_last": float(b["observed_mean_ugm3"]),
            "adjusted_first": float(a["adjusted_mean_ugm3"]),
            "adjusted_last": float(b["adjusted_mean_ugm3"]),
            "observed_change_pct": float(100.0 * (b["observed_mean_ugm3"] / a["observed_mean_ugm3"] - 1.0)),
            "adjusted_change_pct": float(100.0 * (b["adjusted_mean_ugm3"] / a["adjusted_mean_ugm3"] - 1.0)),
            "adjusted_change_pct_lo": float(np.percentile(rep_change_pct, lo_pct)),
            "adjusted_change_pct_hi": float(np.percentile(rep_change_pct, hi_pct)),
            "remaining_share_of_fall": float(adj_fall / obs_fall),
            "remaining_share_lo": float(np.percentile(rep_share, lo_pct)),
            "remaining_share_hi": float(np.percentile(rep_share, hi_pct)),
        }
    return out
