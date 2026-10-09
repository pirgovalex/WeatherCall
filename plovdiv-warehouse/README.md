# Plovdiv Weather and Air Quality Warehouse

A data pipeline and warehouse that keeps a permanent, queryable history of weather and air-quality forecasts for Plovdiv, Bulgaria.

Forecast services overwrite their own output every few hours. Once a newer forecast is published, the older one is gone, and so is the ability to ask how good the forecast actually was. This project captures every forecast as it is issued, stores it unmodified, and models it into a small star schema so that observations, forecasts and forecast errors can be compared over time.

## What it does

- Collects hourly weather forecasts (ECMWF IFS through Open-Meteo) and air-quality forecasts (CAMS Europe) several times a day.
- Stores each raw response unchanged, so any later step can be rebuilt from the original data.
- Models the data in DuckDB with dbt: dimensions plus two fact tables, one for observations and one for forecasts.
- Labels every value with its provenance: `station`, `analysis`, `reanalysis_provisional`, `reanalysis_final` or `forecast`. Modelled values are never presented as measurements.
- Publishes Parquet data marts and a `manifest.json` as the only interface to downstream applications.

## Architecture

```
Open-Meteo / CAMS Europe
        |
        v
  landing/        raw gzip JSON, immutable, partitioned by source, endpoint and fetch date
        |
        v
  bronze/         typed Parquet, one row per event
        |
        v
  DuckDB + dbt    staging, dimensions, fct_observation_hourly, fct_forecast_hourly
        |
        v
  marts/          Parquet marts + manifest.json (the data contract)
        |
        v
  plovdiv-backend (FastAPI, read-only)  -->  plovdiv-frontend (SolidJS dashboard)
```

Design principles:

- **Raw data is immutable.** Everything downstream can be rebuilt from `landing/`.
- **Revisions are rows, not updates.** A provisional value and its later correction both exist, and as-of views return what was known at any past time.
- **UTC everywhere in the warehouse.** Local time (Europe/Sofia) is applied only for display. The autumn clock change makes a local day 25 hours long.
- **Regulatory rules are explicit.** Daily air-quality means follow the EEA rule that at least 18 of 24 hourly values must be valid.
- **Tiny scale on purpose.** One city, hourly data, a few hundred thousand rows per year. The project is about correctness and modelling, not volume.

## Repositories

| Repository | Purpose |
|---|---|
| `plovdiv-warehouse` (this one) | Ingestion, modelling, the data contract and its fixtures |
| `plovdiv-backend` | Read-only analytics API over the published marts |
| `plovdiv-frontend` | Dashboard that consumes the API |

This repository owns the contract in `contracts/`. The other two copy it and must not edit it.

## Data contract

`contracts/` contains:

| File | Purpose |
|---|---|
| `version.py` | Single definition of `SCHEMA_VERSION` |
| `manifest.schema.json` | JSON Schema for `manifest.json` |
| `openapi.yaml` | OpenAPI 3.0.3 description of the API |
| `schemas.py` | Pandera schemas for every mart |
| `make_fixtures.py` | Deterministic fixture generator (fixed seed, byte-identical output) |
| `fixtures/`, `fixtures_invalid/` | Valid, synthetic fixtures, and a deliberately mismatched set used to prove validation fails |
| `README.md` | Contract notes: versioning, marts, time conventions, endpoint rules, known gaps |

The contract has two required marts, `mart_weather_aq_hourly` and `mart_aq_daily`, and five optional ones:
`mart_aq_trend_monthly`, `mart_aq_risk_daily`, `mart_aq_risk_drivers`, `mart_model_card` and
`mart_model_calibration`. They are optional in `manifest.json`, so the schema version stays `"1"` and a manifest
that lists only the two original marts is still valid. The optional marts back the endpoints `/api/v1/aq/trend`,
`/api/v1/aq/risk`, `/api/v1/model/card` and `/api/v1/model/calibration`; an endpoint answers 503 when its mart is
not in the manifest. See `contracts/README.md` for the details.

The hourly and daily fixtures cover a 14-day window with a six-hour gap, NULL values and the 25-hour local day on
2026-10-25. The other fixtures hold synthetic station, risk and model data, including a station with fewer rows and
NULL cases. `contracts/fixtures/README.md` describes them.

## Status

| Area | State |
|---|---|
| Raw forecast capture (Open-Meteo, CAMS Europe) | Working, with retry, atomic writes and a Task Scheduler script (`scripts/register_capture_task.ps1`, every 6 hours while the PC is on) |
| Data contract, schemas and fixtures | Complete and tested |
| Next-day PM10 risk model (`src/plovdiv/ml`, `models/`) | Trained on station and reanalysis history, see `models/README.md` |
| Model and trend marts (`python -m plovdiv.ml.marts`) | Built and validated against the contract. Real data, one station for the risk marts, two for the trend |
| Required marts and `manifest.json` (`python -m plovdiv.marts.core`) | Built and validated, from real landed files, by a **direct landing-to-mart path** that stands in for bronze and dbt until they exist (see below). `manifest.json` is the authoritative manifest |
| Bronze layer, dbt models | Built. |
| Orchestration (Dagster), backfills, revision sweeps | Planned |
| Station observations (EEA) | Verified daily files landed once for three series (2013 to 2025), and unverified hourly PM10 for 2026 landed once for two stations (not yet used by a mart). No scheduled EEA download yet |
| Live next-day risk row | Not built. `docs/LIVE_FORECAST_PATH.md` lists what is missing |

## Docker Setup (Recommended)

To ensure complete isolation and project integrity, you can run the ingestion pipeline via Docker:

```bash
docker-compose up -d ingest
```
This automatically schedules the 6-hour capture tasks in a protected, read-only code environment.

## Getting started

Requires Python 3.12 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Run the tests:

```powershell
python -m pytest
```

The tests of the risk model and the marts also need `scikit-learn` and `joblib` (the `ml` extra). Tests that need the
real landed files and the trained model skip when those are absent.

Capture one set of forecasts into `data/landing/`:

```powershell
python src\plovdiv\extract\fetch_forecasts.py
```

The script captures each source independently, so a failure in one does not lose the other. It exits with a non-zero status if either source failed or the wind-unit check did not pass.

Schedule the capture every six hours on Windows (the task wakes the PC and runs missed starts when it comes back):

```powershell
.\scripts\register_capture_task.ps1
```

Regenerate the fixtures:

```powershell
python contracts\make_fixtures.py
```

## Model and trend marts

The optional marts of the contract that can be built from data already in the repository are produced by one
command:

```powershell
$env:PYTHONPATH = "src"
python -m plovdiv.ml.marts
```

It reads `models/` and the latest partition under `data/landing/` (options: `--models-dir`, `--eea-dir`,
`--weather-file`, `--out-dir`, `--replicates`, `--generated-at`, `--strict`), validates every mart with the
Pandera schemas in `contracts/schemas.py`, and writes `data/marts/<mart>/latest/<mart>.parquet`. A mart that
fails its schema is not written; the reason is printed and stored in `data/marts/diagnostics/build_report.json`.
The output is deterministic: sorted rows, fixed seeds, rounded values, fixed Parquet settings, no pandas
metadata and no wall-clock time, so two runs on the same inputs write identical bytes (tested).

| Mart | Rows | Content |
|---|---|---|
| `mart_aq_trend_monthly` | 264 | Monthly observed and weather-adjusted PM10 for BG0051A (Kamenitsa, 2013 to 2025) and BG0078A (Trakia, 2017 to 2025), with a 90 percent block-bootstrap interval. Months with fewer than 15 valid days have no adjusted value. Method and results: `docs/METHODS_trend.md` |
| `mart_aq_risk_daily` | 3540 | Next-day probability of a "bad PM10 day" for BG0051A, two models, validation (2021 to 2022) and test (2023 to 2025) days. Training days are in-sample and are not published. There is no live row: no live prediction exists yet |
| `mart_aq_risk_drivers` | 5320 | The five largest contributions to the log-odds (coefficient times standardised value) for each test day of the primary model. Contributions plus intercept reproduce the model's logit (tested to 1e-6) |
| `mart_model_calibration` | 20 | Ten reliability bins on the test years for each of the two models |
| `mart_model_card` | 2 | One row per model: windows, test metrics, baselines, threshold and limitations (see below for the dates) |

Limits of these marts:

- **Weather stand-in.** The primary model (`pm10_risk_logreg`) uses ERA5 actual weather of the target day in
  place of a forecast, so its scores are an upper bound. The second model (`pm10_risk_logreg_no_next_day_weather`)
  uses only information known at the end of the previous day.
- **Relative label.** A bad day is a day above the 75th percentile of the same calendar month in the three previous
  years, so about a quarter of days are positive by construction.
- **One station** for the risk marts, and a gap in PM2.5 during 2016 that moves the first usable training days to 2017.
- **Issue time.** `issued_at` is a convention of the offline evaluation: the end of day t in UTC, which is 00:00 UTC
  of the target date. It is not the time a live system produced the value.
- **Drivers** are published for the test split only, as `contracts/README.md` describes.
- **Stations.** The trend and risk marts use the station catalogue in the manifest. The station and area types are
  those stated for the project; no coordinates are stored because the contract has no field for them.

### Model card dates

`trained_at` in `mart_model_card` is the date the fitting ran (2026-10-07). It can fall after the test years because
the windows, not that date, decide which rows were used: the model was fitted on 2016 to 2020 only, tuned on 2021 to
2022, and scored on 2023 to 2025. The contract checks the ordering of the three windows and that `trained_at` is not
before the training window ended.

### Partial manifest

`manifest.schema.json` requires `mart_weather_aq_hourly` and `mart_aq_daily`, which this builder does not produce. It
writes `data/marts/manifest.partial.json` with the fields it can state (schema version, run id, generation time,
attribution, station catalogue, and the marts it wrote). The file says it is partial and is not valid against the
schema. It is kept as a record of the earlier state and is superseded by `manifest.json` (next section); consumers must
read `manifest.json` only. `generated_at_utc` defaults to the fetch date of the weather input at 00:00 UTC so that the
file is reproducible; pass `--generated-at` to set another value.

## Required marts and the manifest

Until the bronze layer and the dbt models exist, the two required marts are built by a small deterministic builder that
reads the landing files directly. This is a stop-gap: the same marts, paths and manifest will come from dbt later.

```powershell
$env:PYTHONPATH = "src"
python -m plovdiv.ml.marts      # optional marts first (model, risk, trend), as above
python -m plovdiv.marts.core    # required marts, then data/marts/manifest.json, then the checks
```

Options: `--landing-dir`, `--eea-dir`, `--out-dir`, `--generated-at` (default: the latest fetch date of the inputs at
00:00 UTC) and `--check-only` (validate an existing output without building). The command exits with status 1 when
the output is not valid.

| Mart | Source | Content |
|---|---|---|
| `mart_aq_daily` | EEA E1a daily station means | One row per day, location and pollutant, from the first to the last reported day of each series. Measured values; a missing day or a record that is not valid (Validity other than 1, a non-numeric or negative value) is a row with NULL mean, `valid_hours` 0 and `is_valid_day` false |
| `mart_weather_aq_hourly` | CAMS Europe air quality and Open-Meteo (ECMWF IFS) weather snapshots | One row per hour and `source_type`. Hours up to the hour of the capture are `analysis`, later hours are `forecast`. Modelled grid values, never measurements |

The command then writes `data/marts/manifest.json` (counts and ranges are read back from the Parquet files; `run_id` is a
hash of the input and output bytes, not of the clock; it also states `max_analysis_valid_at_utc` for the hourly mart, because
`max_valid_at_utc` is the end of the forecast hours, and `min_day` and `max_day` for the daily mart, which does not overlap
the hourly window) and checks the whole output: `manifest.json` against
`contracts/manifest.schema.json`, every Parquet file against its Pandera schema, and the rules that tie the marts
together (variables and units, stations, model ids, drivers against risk rows, labelled days against valid PM10 days,
`n_days` of the trend against the valid days of the daily mart). Two runs on the same inputs write identical bytes
(tested). `manifest.json` is the authoritative manifest; `manifest.partial.json` is only kept for reference.

What the contract cannot say about this data, and what the builder does about it, is in `docs/CORE_MARTS.md`. In short:

- **`valid_hours` is a lower bound.** The source reports daily means and no hourly count. A valid EEA day already met the
  rule of 18 of 24 hourly values, so a valid day is published with `valid_hours` 18 (or the reported data capture times 24
  when the source gives it). Treat the value as "at least", not as a count.
- **One daily series per location.** The daily mart has no station column. Kamenitsa (BG0051A) is the reference of
  location `plovdiv`; Trakia (BG0078A) is published as location `plovdiv-trakia`.
- **No forecast issue time.** The hourly mart keeps the latest snapshot for each hour and `source_type`; older forecast
  vintages stay in `data/landing/` and are not in the mart.
- **Unit corrections.** Units are read from the files. The landed Open-Meteo snapshot reports wind in km/h (it predates the
  `wind_speed_unit=ms` request), so it is converted to m/s.

## Outages and recovery

Every capture attempt, successful or not, is appended to `data/ops/ingest_runs.jsonl` with its start and finish time, status and landing file. To see when capture stopped and resumed:

```powershell
python -m plovdiv.ops.gaps
```

For each gap the report gives the last successful capture, the time capture resumed (or that it has not), the number of missed runs, whether the program did not run at all (`no_run`) or ran and failed (`failed`), and the window to backfill.

What can be rebuilt depends on the data:

| Data | After a gap |
|---|---|
| Analysis-like recent values | Every snapshot includes the last 3 days (weather) or 7 days (CAMS). A shorter gap is covered by the next snapshot. A longer one is backfilled from the archive APIs |
| Forecast vintages issued during the gap | Lost. Open-Meteo's Previous Runs API can restore some weather leads for 2024 onward. CAMS vintages cannot be restored |

Gaps are therefore kept as data: the forecast-error tables must treat a missing vintage as missing, never as zero or as an interpolated value.

## Configuration

Settings are read from `.env` (see `.env.example`):

| Variable | Meaning |
|---|---|
| `LATITUDE`, `LONGITUDE` | Requested point. The services snap it to their own grid, and the echoed coordinates are stored with each response |
| `WEATHER_API_URL`, `AQ_API_URL` | Open-Meteo endpoints |
| `USER_AGENT` | Identifies the project in requests |
| `OPENMETEO_API_KEY` | Optional |

## Repository layout

```
contracts/    data contract, schemas, fixture generator
src/plovdiv/  configuration, extraction, the PM10 risk model and model marts (src/plovdiv/ml), required marts and manifest (src/plovdiv/marts)
models/       trained PM10 risk model, metrics and predictions (see models/README.md)
docs/         architecture decision records, the trend methods note (docs/METHODS_trend.md) and the required-marts note (docs/CORE_MARTS.md)
scripts/      operational scripts
tests/        test suite
dbt/          dbt project (planned)
data/         landing, bronze and marts (not tracked)
```

## Data sources and attribution

- Weather and air-quality data: [Open-Meteo](https://open-meteo.com/), licensed under CC BY 4.0.
- Station observations: European Environment Agency (EEA).
- Air-quality forecasts: Copernicus Atmosphere Monitoring Service (CAMS) Europe. Contains modified Copernicus Atmosphere Monitoring Service information.

Values from these sources are gridded model output, not station measurements. They are only ground truth if station data is loaded alongside them, and the `source_type` column always says which kind a row is.
