# Contract fixtures

Everything in this directory is synthetic. The numbers are plausible in shape, so that
downstream code can be developed and tested, but they are not measurements, and the model
metrics, probabilities, driver contributions and calibration bins are not the results of a
trained model.

The files are produced by `contracts/make_fixtures.py` with a fixed seed. A test compares the
checked-in bytes with a fresh run, so edit the generator and regenerate rather than editing a
file here.

## Contents

| Path | Rows | What it holds |
|---|---|---|
| `manifest.json` | | Run metadata, variable catalogue, station catalogue and mart globs |
| `mart_weather_aq_hourly/` | 336 | 14 days of hourly values for `plovdiv` (2026-10-14 to 2026-10-27) |
| `mart_aq_daily/` | 42 | Daily means per pollutant derived from the hourly mart |
| `mart_aq_trend_monthly/` | 50 | Monthly observed and weather-adjusted means for two stations |
| `mart_aq_risk_daily/` | 78 | Daily exceedance probabilities for two stations |
| `mart_aq_risk_drivers/` | 320 | Top 5 feature contributions per test and live prediction |
| `mart_model_card/` | 2 | One row per model |
| `mart_model_calibration/` | 20 | Ten calibration bins per model |

## Edge cases kept on purpose

- Hourly: a six-hour gap on 2026-10-20, scattered NULLs, and the 25-hour Europe/Sofia day on 2026-10-25.
- Daily: 2026-10-23 has 17 valid PM10 hours, so it is not a valid day.
- Trend: `BG0078A` (traffic) has fewer rows than `BG0051A`. It has a month with no valid days
  (all means NULL, `n_days` 0) and a month with low coverage and a wide interval.
  The partial month 2026-10 is included.
- Risk: `BG0078A` has no train or validation rows. `actual_label` is NULL for the two newest live
  days and for 2026-10-23, which has no valid PM10 day. Keys are dates, so 2026-10-25 is a single
  ordinary row per station; `issued_at` of the following day is shown as 08:00 +02:00 in
  Europe/Sofia, after the clock change.
- Model card: the older model has no persistence baseline (NULL) and no rows in the risk mart.
- Calibration: the last bin of the current model is empty (`n` 0, NULL rates).

`fixtures_invalid/` holds deliberately wrong hourly and daily data and a wrong manifest. It is
used only to prove that validation fails.
