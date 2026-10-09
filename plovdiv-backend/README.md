# Plovdiv Analytics API

A read-only HTTP API over the Plovdiv weather and air-quality marts published by `plovdiv-warehouse`. It is built with FastAPI and reads the Parquet marts through DuckDB.

## Endpoints

The API follows `contracts/openapi.yaml`, copied from the warehouse repository. FastAPI generates its own OpenAPI document (version 3.1), and the test suite checks that it covers every path, parameter, enum and response code of the contract.

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Liveness check |
| `GET /api/v1/meta` | Contents of the current `manifest.json`: schema version, run id, attribution, variable catalogue, data ranges |
| `GET /api/v1/series` | Columnar time series for one variable: UTC epoch seconds in `timestamps`, `null` for gaps in `values` |
| `GET /api/v1/aq/daily` | Daily air-quality means with `valid_hours` and `is_valid_day` as parallel arrays |
| `GET /api/v1/aq/trend` | Monthly observed and weather-adjusted means for one station and pollutant, with an interval around the adjusted mean |
| `GET /api/v1/aq/risk` | Daily exceedance probability per station, as a date range or the latest issued row (`latest=true`) with its drivers |
| `GET /api/v1/model/card` | Model card: windows, label, features, test metrics, baselines, threshold and limitations |
| `GET /api/v1/model/calibration` | Reliability-diagram bins for one model, computed on the test split |

Example:

```
GET /api/v1/series?variable=pm2_5&location=plovdiv&from=2026-10-14T00:00:00Z&to=2026-10-27T23:00:00Z&resolution=hourly&source_type=analysis
```

Behaviour worth knowing:

- `from` and `to` must be ISO-8601 timestamps in UTC (`Z` or `+00:00`). `to` is inclusive. The maximum range is 366 days.
- If `source_type` is omitted, `analysis` is used. A valid `source_type` with no data returns empty arrays, not an error.
- `resolution=daily` on `/series` returns the mean of the non-null hourly values for each UTC day. `/aq/daily` returns the pipeline's EEA-day aggregates instead.
- Every data response carries a `warehouse` block (run id, schema version, generation time) and the `attribution` strings.
- Errors always use `{"error": {"code": "...", "message": "..."}}`. Unknown variables, locations and pollutants return 404. Malformed, out-of-range or injection-style parameters return 422 before any query runs.

### Trend, risk and model endpoints

These four endpoints read optional marts. They are served only when the manifest declares the mart they need (`mart_aq_trend_monthly`, `mart_aq_risk_daily` plus `mart_aq_risk_drivers` for `latest=true`, `mart_model_card`, `mart_model_calibration`). When a needed mart is not declared the endpoint returns 503 `SERVICE_UNAVAILABLE` with the mart name in the message, and `/healthz`, `/api/v1/meta` and the older endpoints keep working. A run without the new marts is therefore valid.

```
GET /api/v1/aq/trend?station_id=BG0051A&from=2025-05-01&to=2026-10-31&pollutant=pm10
GET /api/v1/aq/risk?station_id=BG0078A&from=2026-10-14&to=2026-10-28&split=live
GET /api/v1/aq/risk?station_id=BG0078A&latest=true
GET /api/v1/model/card?model_id=pm10_exceed_gbm_v1
GET /api/v1/model/calibration?model_id=pm10_exceed_gbm_v1
```

- **Dates.** `from` and `to` are calendar dates (`YYYY-MM-DD`, UTC) and both are inclusive. Timestamps are rejected with 422 on these endpoints. Target dates follow the EEA day convention of `/aq/daily`.
- **Station and model ids.** A station id must be in the manifest `stations` catalogue and a model id in `mart_model_card`; a well-formed but unknown id is 404, a malformed one is 422. A known station or model with no matching rows is 200 with empty arrays.
- **Order of checks.** Parameters are validated first (422), then the manifest is checked for the mart (503), then the station or model (404).
- **`/aq/trend`.** A month is returned when any part of it lies in `from..to`; months are keyed by their first day and ascending. `pollutant` defaults to `pm10`; a value outside `pm2_5`, `pm10`, `no2` is 422. A month with no valid days has `n_days` 0 and null means.
- **`/aq/risk`.** In range mode `from` and `to` are required and rows are ordered by target date (then model id). With `latest=true`, `from` and `to` are not allowed (422) and the response holds the row with the newest `issued_at`, ties going to the latest target date and then to the lowest model id. `split` and `model_id` narrow that choice. `drivers` (`feature`, `contribution`, `rank`, ordered by rank) is present only with `latest=true`; it is empty when no row matches or when the row has no published drivers (for example the train and validation splits). `latest=false` is the same as omitting it.
- **`/model/calibration`.** Bins are ordered by `bin_lo`. An empty bin has null `predicted_mean` and `observed_rate`.
- **Nulls.** NULL and NaN in the marts, and infinities, are returned as JSON `null` wherever the contract allows null (means, interval bounds, `actual_label`, metrics, baselines, calibration rates). Rows of the risk mart with a non-finite `probability`, and driver rows with a non-finite `contribution`, would not fit the response schema and are left out.
- **Time stamps.** `issued_at` and `trained_at` are ISO-8601 UTC with a trailing `Z`; fractional seconds appear only when the stored value has them.

Limits:

| Endpoint | Limit |
|---|---|
| `/aq/trend` | `to` may be at most 7320 days after `from`. No pagination |
| `/aq/risk` | `to` may be at most 366 days after `from`. No pagination; the size is bounded by the span and the number of models |
| `/aq/risk` drivers | At most 50 drivers (a safety cap; the mart publishes only the top-ranked features) |
| `/model/calibration` | At most 1000 bins (a safety cap; the mart publishes a handful) |
| `/model/card` | One row |

## Design

- **Read-only by construction.** DuckDB cannot open an in-memory database read-only, so each request opens its own connection to an empty catalog file in read-only mode. Data is read directly from Parquet with bound parameters. Variable names, which cannot be bound, are checked against the manifest catalogue and a strict identifier pattern before they reach a query.
- **Startup checks.** The service refuses to start if `manifest.json` is missing, does not validate against `manifest.schema.json`, has a `schema_version` that differs from the copied contract, or points at Parquet files that do not exist. The same applies to every optional mart the manifest declares: its glob must stay inside the marts directory, match files, and the files must have the columns the endpoints read. The manifest schema itself requires `stations` when a station-keyed mart is declared, and requires the model card for the risk and calibration marts.
- **New pipeline runs are picked up without a restart.** When the manifest changes on disk it is validated and reloaded. If the new manifest is invalid, the previous run keeps being served and the problem is logged. Optional marts appear and disappear with the manifest: a run that adds a mart enables its endpoint, and a run that drops it turns the endpoint into a 503.

## Getting started

Requires Python 3.12 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Run the API (bound to localhost). By default it serves the fixtures in `contracts/fixtures`:

```powershell
python -m uvicorn src.main:app --port 8000
```

Serve real marts instead:

```powershell
$env:MARTS_DIR = "D:\UNI\plovdiv-warehouse\data\marts"
python -m uvicorn src.main:app --port 8000
```

Run the tests:

```powershell
python -m pytest
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MARTS_DIR` | `contracts/fixtures` | Directory containing `manifest.json` and the mart folders |
| `ALLOWED_ORIGINS` | localhost on ports 3000 and 5173 | Comma-separated CORS origins. Set it explicitly outside development |
| `ENV` | `development` | The interactive docs at `/docs` and `/redoc` are served only in development |
| `RELOAD` | `false` | Set to `true` to enable auto-reload when running `python src/main.py` |

## Contract

`contracts/` is a verbatim copy of the warehouse repository's contract. Do not edit it here. To update it, change the warehouse repository and copy the directory again.
