# Path to a live "tomorrow" risk row

Status: built for the `live` model variant (see "Live row" below). The sections after it record the original gap
analysis for the 23-input models, which still cannot be scored live (no PM2.5, no radiation history).

## Live row (`python -m plovdiv.ml.live`)

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m plovdiv.ml.live            # after each capture; exit 2 = no row, reason printed
.\.venv\Scripts\python.exe -m plovdiv.ml.live --dry-run  # score and print only
```

| Step | Rule |
|---|---|
| Model | `models/pm10_risk_logreg_live.json`: PM10 history, calendar, weather of day t without radiation. Test ROC-AUC 0.849 |
| As-of day t | Newest EEA day (UTC+1, date of the hour's `Start`) whose last hour is in the newest UTD file. Target is t+1 |
| PM10 daily mean | UTD hours with Validity 1 and a non-negative value; mean when at least 18 of 24 hours are valid (the `mart_aq_daily.is_valid_day` rule). Verification is kept per day in the run report (`verified`, `preliminary`, `unverified`) |
| PM10 history | E1a verified daily means (to 2025-12-31) joined with the UTD means (E1a wins on overlap); rolling features and the label threshold come from `features.build_model_table` unchanged (2 of 3, 4 of 7 valid days) |
| Weather of day t | Most recent Open-Meteo capture taken after the Europe/Sofia day t ended and holding all its hours; hourly values aggregated as the ERA5 archive daily variables (mean, max, min, sum, max of hourly wind, means of humidity and pressure) |
| Refusal | No row, `data/live/last_run.json` with the reason, exit 2: no UTD file or model, as-of day under 18 valid hours, a missing feature, no capture covering day t, issue time after the target day ended (stale station file) or before day t ended |
| Output | Upsert into `data/live/pm10_risk_live.parquet` (one row per station, target day, model; features and inputs kept), then the `live` rows of `mart_aq_risk_daily` and `mart_aq_risk_drivers` are replaced and `manifest.json` counts are refreshed when it exists. `python -m plovdiv.ml.marts` re-adds the stored live rows on a rebuild |

`issued_at` of a live row is the time the command ran; evaluation rows keep the end-of-day convention.
`actual_label` stays NULL; nothing fills it in yet.

### Forecast weather against ERA5

ECMWF IFS hourly values from the Open-Meteo historical forecast API (same model and point as the capture),
aggregated with the live rule, against the landed ERA5 daily values for 2023-2025 (670 complete days):

| Variable | Bias (IFS - ERA5) | MAE | Correlation |
|---|---|---|---|
| Mean temperature | -0.17 degC | 0.50 | 0.998 |
| Max temperature | -0.03 degC | 0.59 | 0.998 |
| Min temperature | -0.57 degC | 0.95 | 0.991 |
| Precipitation | -0.03 mm | 0.73 | 0.865 |
| Max wind | -0.45 m/s | 0.75 | 0.843 |
| Mean humidity | +3.1 % | 4.4 | 0.959 |
| Mean pressure | -0.08 hPa | 0.17 | 1.000 |

Scoring the 1064 test days with these values in place of ERA5 day-t weather moves the probability by 0.015 on
average (95th percentile 0.067), ROC-AUC 0.849 to 0.848, Brier 0.126 to 0.128, and flips 21 labels at the 0.54
threshold. The difference is not material, so no correction is applied. The four landed capture days
(2026-10-05 to 2026-10-08) show the same signs (humidity +9 %, minimum temperature -1.4 degC), with only four days.

### Still open for the live row

- The UTD hourly file is landed once (fetch_date=2026-10-08); no scheduled job refreshes it, so the command refuses
  as stale until a newer file is landed. The live row of 2026-10-09 was produced from a file downloaded to a scratch
  folder with `--utd-file`.
- `actual_label` of live rows is never filled, and `mart_aq_daily` has no 2026 days to check it against.

## Original gap analysis

## What the model needs on day t (today)

The primary model, `pm10_risk_logreg`, takes 23 inputs (see `models/pm10_risk_logreg.json`):

| Group | Inputs | Source for a live row |
|---|---|---|
| PM history | log PM10 today, yesterday, 3-day and 7-day means; log PM2.5 today | EEA hourly unverified (UTD) data, daily means built from complete days |
| Calendar | month sine and cosine, weekend, heating season | derived |
| Weather today | mean and range of temperature, precipitation, strongest wind, radiation, humidity, pressure | Open-Meteo forecast capture, past hours of today |
| Weather tomorrow | the same seven values for t+1 | Open-Meteo forecast capture, hours of tomorrow |

The saved metrics for the model that uses next-day weather (ROC-AUC 0.877) were measured with ERA5 values standing
in for tomorrow's forecast. A real forecast is worse, so a live score should come with the second model,
`pm10_risk_logreg_no_next_day_weather` (ROC-AUC 0.847), or with a retrain on archived forecasts once enough exist.

## Landed so far

| Input | Location under `data/landing` | Status |
|---|---|---|
| PM10 hourly, 2026-01-01 to 2026-10-08, Kamenitsa and Trakia | `source=eea/endpoint=utd_hourly/fetch_date=2026-10-08` | landed; not yet used by any mart |
| Open-Meteo hourly forecast, 7 days ahead | `source=openmeteo/endpoint=forecast` | captured by the scheduled job, 6-hourly |
| CAMS PM and gases, about 3.4 days ahead | `source=cams/endpoint=air_quality` | captured by the same job |

The UTD files carry Validity and Verification flags: Verification 1 is verified, 3 is unverified. At the time of download,
3 of 6,650 Kamenitsa hours and 43 of 6,673 Trakia hours are unverified, and 43 hours at
Kamenitsa and 54 at Trakia have Validity -1 and must not be used.

## Still missing

1. PM2.5 hourly for Kamenitsa (pollutant code 6001, same download service). The model input `log_pm25_t` cannot be built without it.
2. Shortwave radiation in the weather capture. Added to the capture request on 2026-10-08; captures before that date lack it, so the first usable forecast vintage is the first one taken after the change.
3. A daily aggregation of hourly forecast weather into the seven daily inputs, matching the definitions in `src/plovdiv/ml/features.py` (ERA5 daily wind maximum and Open-Meteo hourly maxima differ slightly; the difference must be measured and, if material, corrected).
4. A way to represent unverified station values. `mart_aq_daily` has no verification column. Either extend the contract with an optional column or keep unverified days in a separate mart.
5. A scoring step that writes one `mart_aq_risk_daily` row with `split = live`, an issue time, and the model id, and a manifest that marks the run as live.
6. A continuously running capture. The scheduled task only runs while the PC is on and the user is signed in. Gaps cannot be recovered for CAMS forecasts.

## Observation worth keeping

On 2026-10-07 the Kamenitsa station recorded a PM10 daily mean of 26.7 ug/m3 and the Trakia station 34.5, while the
CAMS analysis for the Plovdiv grid cell averaged 19.4. The modelled value ran well below the stations that day. Over
2026-10-04 to 2026-10-07 the gap is similar in sign each day. Station data should therefore stay the reference, and the
dashboard keeps labelling CAMS and Open-Meteo values as modelled grid values.
