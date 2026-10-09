# Next-day PM10 risk model (Kamenitsa, EEA BG0051A)

A small logistic regression that estimates, from data known at the end of day t, the probability that the PM10 daily mean on day t+1 is a "bad day" at the Kamenitsa station in Plovdiv. It is meant to be readable: 23 standardised features, one coefficient each.

## Files

| File | Content |
|---|---|
| `pm10_risk_logreg.joblib` | Fitted scikit-learn pipeline (standard scaler + logistic regression) with its feature names, threshold and label id. Primary model, uses weather of day t+1 |
| `pm10_risk_logreg.json` | The same model as plain numbers: feature names in order, scaler mean and scale, coefficients (standardised and on the raw scale), intercept, C, label definition, periods, threshold, scikit-learn version, input file names, sizes and SHA-256, training date |
| `pm10_risk_logreg_no_next_day_weather.joblib` / `.json` | Same, without weather of day t+1 (only inputs available at the end of day t) |
| `pm10_risk_logreg_live.joblib` / `.json` | Variant `live`: PM10 history, calendar and weather of day t without radiation; no PM2.5, no weather of day t+1. Its inputs are all landed for 2026, so `python -m plovdiv.ml.live` scores it for the live row. Test ROC-AUC 0.849 |
| `pm10_risk_metrics.json` | All metrics: model, ablations, baselines, class balance, calibration bins, bootstrap interval, coefficient ranking, label thresholds |
| `pm10_risk_daily_predictions.parquet` | Probability and actual label per target day for the validation and test years (`probability`, `probability_no_next_day_weather`, `probability_live`) |

The live files were added to an existing training output: the primary and second model files were left as they were, `ablation.live` was inserted into the metrics file and `probability_live` into the predictions. A full retrain with scikit-learn 1.9.1 reproduces the existing numbers to within 2e-12 (only `sklearn_version` changes).

Probability from the JSON weights alone: `1 / (1 + exp(-(intercept + sum(coefficient_i * (x_i - mean_i) / scale_i))))`.

## Definitions

**Label** (`pm10_gt_p75_same_month_prev3y_v1`). y(t+1) = 1 if PM10 on day t+1 is above the 75th percentile of the PM10 daily means of the same calendar month in the three calendar years before the year of t+1. Only earlier years enter the percentile, so the threshold follows the long-term decline of concentrations. The first usable target year is 2016 (station data start in 2013). The thresholds used are listed in `pm10_risk_metrics.json`.

**Secondary label** (reporting only, never trained on): PM10 on day t+1 above 50 ug/m3 (EU daily limit).

**Features** (day t is the last day of input):

- PM10 and PM2.5 of day t, PM10 of day t-1, 3-day and 7-day mean PM10. All as natural logs of ug/m3. A rolling mean needs at least 2 of 3 and 4 of 7 valid days.
- Calendar of the target day: month as sine and cosine, weekend flag, heating-season flag (October to March).
- ERA5 weather of day t: mean temperature, temperature range (max minus min), log(1 + precipitation), maximum 10 m wind speed, shortwave radiation sum, mean relative humidity, mean surface pressure.
- The same seven weather values for day t+1 (suffix `_t1`), taken from ERA5 actuals as a stand-in for a perfect weather forecast.

Rows with any missing feature are dropped (train 290, validation 18, test 22 rows; PM2.5 is missing for most of 2016, so in practice the training rows start in 2017). Every model variant is scored on the same rows.

**Split** (by target date, no random split): train 2016-2020, validation 2021-2022, test 2023-2025. Earlier years are used only as history for rolling quantities and thresholds.

**Fitting.** The scaler and the coefficients are fitted on the training rows only. C is chosen from `0.001, 0.01, 0.1, 1, 10, 100` by ROC-AUC on the validation rows (ties go to the smaller C). The decision threshold maximises F1 on the validation rows. Nothing is fitted on validation or test rows beyond these two choices. Estimator: L2 penalty, `class_weight="balanced"`, lbfgs, `max_iter=10000`.

**Day alignment.** ERA5 days are Europe/Sofia local calendar days. EEA daily values are the daily means as reported by Bulgaria. They are joined on the calendar date, so the alignment is approximate.

## Results (test years 2023-2025, 1064 days, 24.5 % bad days)

| Model | ROC-AUC | PR-AUC | Brier | Precision | Recall |
|---|---|---|---|---|---|
| Logistic, with next-day weather (primary) | 0.877 | 0.723 | 0.117 | 0.659 | 0.540 |
| Logistic, no next-day weather | 0.847 | 0.687 | 0.127 | 0.670 | 0.513 |
| Logistic, no weather | 0.830 | 0.648 | 0.134 | 0.619 | 0.510 |
| Baseline: PM10-only logistic | 0.825 | 0.643 | 0.135 | 0.631 | 0.490 |
| Baseline: monthly climatology | 0.536 | 0.275 | 0.195 | 0.252 | 0.506 |
| Baseline: persistence | 0.728 | 0.449 | 0.201 | 0.590 | 0.590 |

Precision and recall are at the threshold chosen on validation (maximum F1). The test ROC-AUC of the primary model has a 95 % moving-block bootstrap interval of 0.841 to 0.905 (14-day blocks, 1000 replicates); the no-next-day model has 0.809 to 0.879. The intervals of neighbouring rows overlap, so small differences (for example between "no weather" and "PM10-only") are not evidence of an effect.

The score of the model with next-day weather is an optimistic upper bound: it uses ERA5 actuals for day t+1, where a real forecast would be imperfect. The no-next-day model only uses what is known at the end of day t and is the one to use when no forecast is available.

Baselines: monthly climatology predicts the share of days in the same month of the previous year that exceed this year's threshold; persistence predicts a bad day when today is a bad day by the same definition; the PM10-only logistic uses the four PM10 features and the calendar features.

The label is relative to a rolling monthly threshold, so about a quarter of days are positive by construction, but the observed rates are 17.0 % (train), 21.4 % (validation) and 24.5 % (test). Against the secondary label (PM10 above 50 ug/m3, 9.9 % of test days) the primary model reaches ROC-AUC 0.798 and PR-AUC 0.308; it was not trained for that event.

Class weighting makes the probabilities larger than the observed rates in the middle range; use the calibration bins in the metrics file before reading the probability as a frequency.

## Reproduce

Landed inputs (read-only): `data/landing/source=eea/endpoint=e1a_daily/fetch_date=2026-10-07/` (Kamenitsa PM10 and PM2.5) and `data/landing/source=openmeteo/endpoint=archive_daily_era5/fetch_date=2026-10-07/archive_2013-01-01_2025-12-31.json.gz` (ERA5 archive, 42.15 N 24.75 E, Europe/Sofia, wind in m/s).

```powershell
pip install -e ".[ml]"
python -m plovdiv.ml.train --trained-at 2026-10-07
python -m pytest tests/test_ml_dataset.py tests/test_ml_features.py tests/test_ml_train.py
```

Input files are sorted, seeds are fixed and the solver is deterministic, so a second run with the same inputs, library versions and `--trained-at` writes identical files. Options: `--eea-dir`, `--weather-file`, `--out-dir`.

The `.joblib` files need the scikit-learn version recorded in the JSON (1.7.2 for the files in this folder); the JSON weights do not.

## Limits

- One station, 2016 to 2025 targets; about 1500 training rows after dropping incomplete days.
- Weather is reanalysis at a roughly 25 km grid (grid point 42.144 N, 24.701 E, 166 m), not a station measurement and not a forecast.
- The label depends on the last three years of data, so the meaning of "bad day" drifts as concentrations change.
- The weather features are daily summaries. Boundary layer height and hourly detail were not used.
- Day boundaries of the two sources can differ by an hour.
- Test years were not used to tune anything, but the model design (feature set, label rule) was fixed before seeing results and not revisited.
