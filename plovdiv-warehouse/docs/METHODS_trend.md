# Weather-adjusted PM10 trend: methods note

This note describes how `mart_aq_trend_monthly` is computed (`src/plovdiv/ml/trend.py`, built by
`python -m plovdiv.ml.marts`) and what it shows for the two Plovdiv stations. It is a descriptive
adjustment, not a causal attribution.

## Question

Concentrations of PM10 fell strongly between 2013 and 2025. Part of any difference between two
years is weather: a stagnant, cold winter gives high values whatever the emissions. The trend mart
shows each month as it was observed and as it would have been under the long-run average weather of
that calendar month. A change between years that remains in the adjusted series is not explained by
the weather variables below.

## Data

| Input | Content |
|---|---|
| Station files | EEA daily means of PM10 (`data/landing/source=eea/endpoint=e1a_daily/fetch_date=2026-10-07`), stations BG0051A (Kamenitsa, 2013 to 2025) and BG0078A (Trakia, 2017 to 2025). A day is valid when `Validity == 1` and the value is positive: 4672 valid days for BG0051A, 3148 for BG0078A |
| Weather | ERA5 daily values from Open-Meteo for one grid point (42.144 N, 24.701 E, 166 m), Europe/Sofia days, `archive_2013-01-01_2025-12-31.json.gz`. Both stations use the same weather |

Weather variables: mean temperature, temperature range (maximum minus minimum), log(1 + precipitation),
maximum 10 m wind speed, shortwave radiation sum, mean relative humidity, mean surface pressure.

Station days and weather days are joined on the calendar date. The day boundaries of the two sources
can differ by an hour; this is ignored.

## Method

1. **Regression.** One ordinary least squares fit per station of the natural log of the daily value on
   * 12 month-of-year indicators (no common intercept), and
   * the seven weather variables, standardised, each with a separate slope in the heating season
     (October to March) and in the other months.

   There is no year term and no trend term. All valid days of all years enter the fit. The fit explains
   about half of the variance of log PM10 (R-squared 0.52 for both stations).

2. **Adjustment.** For a day with weather `x` in calendar month `m`, the adjusted value is

   `adjusted = observed * exp( slope * (x_clim(m) - x) )`

   where `x_clim(m)` is the mean of each weather variable over all days of calendar month `m` in the whole
   2013 to 2025 weather record, and `slope` is the heating or non-heating slope vector. This is the day's
   residual plus the fitted value at the climatological weather, back-transformed: the month effect cancels
   and the day's own residual is kept, so no separate smearing constant is needed (the retained residuals
   are the smearing). A windy day is raised, a stagnant day is lowered.

3. **Monthly values.** `observed_mean_ugm3` is the mean of the valid daily values of the month and `n_days`
   their number. `weather_adjusted_mean_ugm3` is the mean of the adjusted daily values of the same days.
   A month with fewer than 15 valid days keeps its observed mean and `n_days` but has no adjusted value and
   no interval (the three are NULL together, as the schema requires). A month with no valid day stays as a
   row with `n_days` 0 and all values NULL.

4. **Interval.** `adjusted_lo_ugm3` and `adjusted_hi_ugm3` are the 5th and 95th percentile (a 90 percent
   interval) of 1000 block-bootstrap replicates, with seed 20261007. In each replicate, the valid days of
   every station-month are resampled in blocks of seven consecutive days, with replacement, within that month;
   the regression is refitted on the resampled days; and the monthly adjusted means are recomputed. The
   interval therefore combines the uncertainty of the weather coefficients and the day-to-day scatter within
   a month, and respects short-range dependence between days. If a percentile bound fell on the wrong side of
   the point estimate it would be moved to the estimate; this did not happen for any month.

5. **Rounding.** Values are rounded to 4 decimals (ug/m3) so that the Parquet bytes do not depend on
   floating point noise between machines.

## Result

Yearly values are means over the months that have an adjusted value, for the observed series too, so both
columns cover the same months. Units are ug/m3. Intervals are 90 percent bootstrap intervals for the
yearly mean of the adjusted series.

### Kamenitsa (BG0051A, urban background), 2013 to 2025

| Year | Months | Observed | Adjusted | Adjusted 90% interval |
|---|---|---|---|---|
| 2013 | 12 | 62.3 | 51.8 | 49.3 to 54.3 |
| 2014 | 12 | 43.1 | 35.4 | 33.5 to 37.3 |
| 2015 | 12 | 39.7 | 33.1 | 31.5 to 34.7 |
| 2016 | 12 | 42.3 | 34.6 | 32.5 to 36.4 |
| 2017 | 12 | 44.9 | 44.9 | 41.4 to 48.5 |
| 2018 | 12 | 38.9 | 38.8 | 36.9 to 40.5 |
| 2019 | 12 | 35.8 | 36.9 | 35.1 to 39.0 |
| 2020 | 12 | 32.0 | 32.9 | 31.2 to 34.7 |
| 2021 | 12 | 31.0 | 31.8 | 30.5 to 33.0 |
| 2022 | 12 | 29.5 | 30.7 | 29.5 to 31.9 |
| 2023 | 12 | 27.8 | 29.2 | 28.2 to 30.3 |
| 2024 | 12 | 29.7 | 28.9 | 27.9 to 30.2 |
| 2025 | 12 | 28.2 | 25.8 | 25.0 to 26.6 |

* The observed yearly mean fell from 62.3 in 2013 to 28.2 in 2025 (minus 55 percent).
* The weather-adjusted mean fell from 51.8 to 25.8 (minus 50 percent, 90 percent interval minus 53 to
  minus 47 percent).
* About three quarters of the observed fall remains after the adjustment (76 percent, interval 68 to 84
  percent). Weather explains the rest: in 2013 to 2016 it raised concentrations (adjusted values 17 to 18
  percent below observed), and in 2025 it raised them slightly (observed 9 percent above adjusted), while
  2019 to 2024 were close to average. 2017 is high in both series, so that year is not a weather effect.

### Trakia (BG0078A, traffic), 2017 to 2025

| Year | Months | Observed | Adjusted | Adjusted 90% interval |
|---|---|---|---|---|
| 2017 | 12 | 55.7 | 55.1 | 51.2 to 58.8 |
| 2018 | 12 | 49.2 | 49.1 | 46.9 to 51.0 |
| 2019 | 12 | 44.7 | 45.7 | 43.4 to 47.9 |
| 2020 | 10 | 43.0 | 44.7 | 42.5 to 47.2 |
| 2021 | 12 | 39.5 | 40.5 | 38.3 to 42.1 |
| 2022 | 12 | 38.7 | 39.6 | 38.1 to 41.1 |
| 2023 | 12 | 33.0 | 33.8 | 32.5 to 35.3 |
| 2024 | 12 | 30.9 | 29.9 | 28.8 to 31.3 |
| 2025 | 12 | 33.8 | 30.0 | 28.9 to 31.2 |

* Observed fall 2017 to 2025: 55.7 to 33.8 (minus 39 percent). Adjusted: 55.1 to 30.0 (minus 46 percent,
  interval minus 49 to minus 41 percent). Here weather made 2025 look worse than average, so the adjusted
  fall is larger than the observed one. Two months of 2020 have fewer than 15 valid days.

## Limits

* Only seven daily reanalysis variables are used, from a grid cell of about 25 km. Boundary layer height,
  temperature inversions, wind direction, snow cover and local meteorology of the station are not in the
  model, so part of the weather effect stays in the adjusted series.
* A remaining fall is not a measured fall in emissions. Changes of instrument, calibration or data
  processing, the siting of the station, and changes of local sources are not separated from it.
* The model is log-linear with seasonal slopes. The interval reflects sampling variation under this
  model, not the choice of the model. The fit is the same for all years, so a change in the relation
  between weather and concentration over time would be absorbed into the adjusted trend.
* The climatological weather is the 2013 to 2025 mean, so the adjusted values refer to the average weather
  of that period; a different reference period shifts all adjusted values by a similar factor but not the
  differences between years.
* Yearly means average the monthly values with equal weight and cover only months with at least 15 valid
  days. The 90 percent intervals are bootstrap percentile intervals and are not guaranteed to cover.
* Daily values are the means reported by Bulgaria; the 18-of-24 hourly completeness rule cannot be checked
  from the landed daily files, which carry no data capture value.

## Reproduce

```bash
python -m plovdiv.ml.marts            # all marts; writes diagnostics/build_report.json with these tables
python -m pytest tests/test_ml_trend.py
```

`tests/test_ml_trend.py` checks the method on synthetic series with a known weather effect: a year with
strong wind looks clean in the observed series and is brought back in line by the adjustment, and a true
decline of 5 percent per year is recovered.
