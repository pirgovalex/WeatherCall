# Required marts and manifest: direct landing-to-mart path

`mart_weather_aq_hourly` and `mart_aq_daily` are required by `contracts/manifest.schema.json`. The bronze layer and the
dbt models that are meant to produce them are not built, so `python -m plovdiv.marts.core` builds them by reading the
landing files directly. It is a stop-gap with the same output paths and shapes as the planned dbt marts, and it is
replaced, not extended, when bronze and dbt exist.

```powershell
cd D:\UNI\plovdiv-warehouse
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m plovdiv.ml.marts     # model, risk and trend marts (needs models/)
.\.venv\Scripts\python.exe -m plovdiv.marts.core   # required marts, manifest.json and the checks
```

`manifest.json` in `data/marts/` is the authoritative manifest. `manifest.partial.json` was written by
`plovdiv.ml.marts` before the required marts existed; it is kept for reference and is not valid against the schema.

## Optional range fields in the manifest

Besides the counts and the hourly range, the builder writes three optional fields, read from the Parquet files (schema
version stays "1"; see `contracts/README.md`):

| Entry | Field | Value for the capture of 2026-10-07 |
|---|---|---|
| `mart_weather_aq_hourly` | `max_analysis_valid_at_utc` | 2026-10-07T14:00:00Z (the last `analysis` hour; `max_valid_at_utc` is 2026-10-13T23:00:00Z, the end of the forecast hours) |
| `mart_aq_daily` | `min_day`, `max_day` | 2013-01-01 and 2025-12-31 (the EEA series does not overlap the hourly window) |

The field is left out when it cannot be stated (no analysis hour, no daily row). `validate_output` compares each field
that is present with the files. Rebuilding changes only `manifest.json`; the Parquet files stay byte-identical.

## Inputs (never modified)

| Mart | Landing path | Notes |
|---|---|---|
| `mart_aq_daily` | `source=eea/endpoint=e1a_daily/fetch_date=<latest>/SPO-*.parquet` | EEA E1a daily station means, one file per sampling point and pollutant |
| `mart_weather_aq_hourly` | `source=cams/endpoint=air_quality/fetched_date=*/*.json.gz` and `source=openmeteo/endpoint=forecast/fetched_date=*/*.json.gz` | Raw API responses with the capture time in `_audit.fetched_at_utc` |

`source=openmeteo/endpoint=archive_daily_era5` is daily data and does not belong in an hourly mart; it feeds the trend and
risk marts only.

## mart_aq_daily

- **Grain.** One row per `day`, `location_id` and `pollutant`, from the first to the last reported day of each series.
- **Day.** The EEA day (UTC+1): the date of the record's `Start`. Days are plain dates, so 2026-10-25, the 25-hour local day
  in Europe/Sofia, is one ordinary row.
- **Valid and not valid.** A record is valid when `Validity` is 1 and `Value` is numeric and not negative. This is the same
  rule as the loader of the risk model, so the trend `n_days` and the daily mart agree (checked). Any other record, and any
  day without a record inside the series, is published as `daily_mean` NULL, `valid_hours` 0, `is_valid_day` false. A gap is
  data; nothing is interpolated.
- **Units.** `Unit` is read per record and converted to ug/m3; an unknown unit stops the build.
- **Naming.** Pollutant codes 5, 6001 and 8 become `pm10`, `pm2_5` and `no2`.
- **Several records for one day.** Two valid records for a day stop the build. A valid record wins over a not valid one;
  otherwise the most recently produced record is kept.

### Where the contract does not fit the source

These are limits of the contract, not choices of the builder. None of them is hidden: each shows in the build report
(`data/marts/diagnostics/core_build_report.json`) and here.

1. **`valid_hours` cannot be filled with a count.** The contract wants the number of valid hourly readings (0 to 24) and
   defines `is_valid_day` as `valid_hours >= 18`. EEA E1a gives a daily mean and, in the landed files, an empty
   `DataCapture`. A day with Validity 1 has already met the EEA rule of 18 of 24 hourly values, so 18 is the largest number
   that is certainly true. The builder publishes `valid_hours` 18 for such a day, or `round(DataCapture * 24)` when the
   source reports data capture. Read it as "at least 18". The contract has no way to say "not reported" because
   `valid_hours` is not nullable and the 18-of-24 rule is checked on every row.
2. **The daily mart has no station column.** Its grain is `day`, `location_id`, `pollutant`, but the data are per station and
   two stations measure PM10 in Plovdiv. The urban background station Kamenitsa (BG0051A, PM10 and PM2.5) is published as the
   reference of location `plovdiv`, which is the location the hourly mart and the API use. The traffic station Trakia
   (BG0078A, PM10) is published as location `plovdiv-trakia`. The API serves a location only when it is in the hourly mart,
   so `plovdiv-trakia` is stored but not reachable through `/api/v1/aq/daily` until the backend or the contract changes.
   The trend and risk marts are keyed by station and are not affected.
3. **The hourly mart has no forecast issue time or run id.** Its grain is `valid_at_utc`, `location_id`, `source_type`, so
   one forecast vintage fits per hour. With several landed snapshots the latest decides each hour and `source_type`. An
   earlier forecast of an hour that has since become `analysis` stays as a `forecast` row. Older vintages remain in
   `data/landing/` and are the input of the planned `mart_forecast_verification`.
4. **The variable catalogue holds only a unit and a label** (`additionalProperties: false`). The source and the kind
   (measured or modelled) of a variable are not in the manifest; they are in `source_type` on each hourly row and in the
   README. The six names are fixed by the schema, so Ozone, CO, SO2, pressure and CAPE, which the captures also hold, are not
   published.

## mart_weather_aq_hourly

- **Rows.** One per hour, `location_id` `plovdiv` and `source_type`. CAMS (`pm10`, `pm2_5`, `nitrogen_dioxide` as `no2`) and
  Open-Meteo (`temperature_2m`, `wind_gusts_10m`, `precipitation`) are joined on hour and `source_type`. A column a source
  does not cover for an hour is NULL.
- **analysis and forecast.** An hour up to the hour of the capture is `analysis` (the recent values every snapshot carries
  with `past_days`); a later hour is `forecast`. A snapshot taken at 14:33 UTC gives analysis up to 14:00 and forecast from
  15:00. The capture time is read from the file; a file without it stops the build.
- **Grid cells.** CAMS is at 42.1 N, 24.8 E and Open-Meteo at 42.25 N, 24.75 E (both 167 m). They are different cells, and
  the contract has no column for the cell, so both are `plovdiv`. The coordinates are in the build report.
- **Units.** Read from `hourly_units` and converted to ug/m3, degC, m/s and mm. A unit the builder does not know stops the build.
- **Range.** A value outside the schema range (a negative concentration, precipitation or gust, a temperature outside
  -60 to 60) becomes NULL and is counted in the report. None occurred in the landed files.
- **Rows without any value** (for example the last hours of a CAMS response) are dropped before the join.
- **Precipitation** is the sum of the preceding hour, as Open-Meteo defines it.

## What the landed files contained (capture of 2026-10-07)

| Finding | Detail |
|---|---|
| Wind unit | The Open-Meteo snapshot declares `km/h` for `wind_gusts_10m`, although the capture code requests `ms`. The file has no `request_params` in `_audit`, so it predates that change. The values are converted (divided by 3.6); the current capture code has a unit guard that records such a file as a unit mismatch. |
| CAMS horizon | 2026-09-30 00:00 to 2026-10-11 00:00 UTC. The 23 hours after that are empty in the response. 183 analysis hours and 82 forecast hours (about 3.4 days ahead of the capture). |
| Open-Meteo horizon | 2026-10-04 00:00 to 2026-10-13 23:00 UTC: 87 analysis hours and 153 forecast hours (about 6.4 days ahead). |
| Weather before 2026-10-04 | NULL in the analysis rows from 2026-09-30 to 2026-10-03: the weather capture asks for 3 past days, CAMS for 7. |
| Variables not in the contract | `lifted_index` is empty in the whole response; humidity, pressure, CAPE, ozone, CO and SO2 are not published. |
| EEA validity | Every landed record has Validity 1 and Verification 1; there was nothing to exclude by flag. Gaps are days with no record. |
| EEA gaps | Kamenitsa PM10: 76 missing days of 4748 (longest run 7 days; 31 of them in 2013). Kamenitsa PM2.5: 444 of 4748, including a run of 275 days in 2016. Trakia PM10: 139 of 3287, including a run of 62 days (81 missing days in 2020). |
| EEA data capture | `DataCapture` is empty in all three files, so `valid_hours` is the lower bound of 18 for every valid day. |

## Checks

`python -m plovdiv.marts.core` ends by validating the output (also available alone as `--check-only`):

- `manifest.json` against `contracts/manifest.schema.json`; the generation time is an ISO-8601 UTC timestamp.
- Every Parquet file against its Pandera schema in `contracts/schemas.py`, row counts and ranges against the manifest.
- Hourly measure columns and daily pollutants are declared in `variables`, with the units of the contract.
- Stations used by the trend, risk and drivers marts are declared; their location is in the hourly mart.
- Risk rows point at a model in the card with the same label; calibration and drivers point at the card; every drivers row has
  a risk row.
- A risk row with an outcome falls on a day with a valid PM10 value in `mart_aq_daily` for the station's location.
- Each trend row has the `n_days` and the mean of the valid days of `mart_aq_daily`.

## Determinism

Files are read in sorted order, rows are sorted by the grain, values are rounded to four decimals, Parquet is written with
fixed settings and without pandas metadata. `run_id` is `warehouse-` and twelve hexadecimal characters of a SHA-256 over the
bytes of the input files and of every mart file. `generated_at_utc` is the latest fetch date of the inputs at 00:00 UTC unless
`--generated-at` is given. Running the command twice on the same inputs writes identical bytes.

## Replacing this path

When bronze and dbt exist, `mart_aq_daily` and `mart_weather_aq_hourly` come from the dbt models, and the checks and the
manifest assembly in `plovdiv.marts.core` (`validate_output`, `assemble_manifest`) can stay as the last step of the pipeline.
The contract limits above are the list to settle first: a nullable or separate data-capture field, a station key on the daily
mart, and an issue time on forecast rows. Each is a change of the contract, and the first two change its version.
