# Data contract

This directory is the contract between the warehouse and its consumers. The warehouse owns it.
`plovdiv-backend` and `plovdiv-frontend` keep verbatim copies and must not edit them; to change the
contract, change it here, regenerate the fixtures and copy the directory again.

## Files

| File | Purpose |
|---|---|
| `version.py` | Single definition of `SCHEMA_VERSION` |
| `manifest.schema.json` | JSON Schema (draft-07) for `manifest.json` |
| `openapi.yaml` | OpenAPI 3.0.3 description of the API |
| `schemas.py` | Pandera schemas for every mart |
| `make_fixtures.py` | Deterministic fixture generator (fixed seed, byte-identical output, LF line endings) |
| `fixtures/` | Valid, synthetic fixtures: `manifest.json`, one Parquet file per mart, and a README |
| `fixtures_invalid/` | Deliberately wrong hourly and daily data and a wrong manifest, used to prove validation fails |

## Versioning

`SCHEMA_VERSION` is `"1"`. It is checked in `manifest.schema.json` (`const`), in `openapi.yaml`
(`info.version`) and by every consumer at startup.

Adding optional marts, optional manifest fields, endpoints or optional response fields does not change
the version: a manifest written for the earlier contract stays valid. Renaming or removing a field,
changing a type or the meaning of a column, or making an optional part required does change it, and
then every place that checks the version in all three repositories must be updated together.

One practical consequence of the additive change: `marts` in `manifest.schema.json` does not allow
unknown entries. A consumer that still holds the earlier copy of the contract rejects a manifest that
declares the new marts. Copy the contract to the consumers before publishing a run that includes them.

## Marts

| Mart | Grain | In the manifest |
|---|---|---|
| `mart_weather_aq_hourly` | `location_id`, `valid_at_utc`, `source_type` | Required |
| `mart_aq_daily` | `day`, `location_id`, `pollutant` | Required |
| `mart_aq_trend_monthly` | `station_id`, `pollutant`, `month` | Optional |
| `mart_aq_risk_daily` | `station_id`, `target_date`, `model_id` | Optional, needs `mart_model_card` |
| `mart_aq_risk_drivers` | `station_id`, `target_date`, `model_id`, `feature` | Optional, needs `mart_aq_risk_daily` |
| `mart_model_card` | `model_id` | Optional |
| `mart_model_calibration` | `model_id`, `bin_lo` | Optional, needs `mart_model_card` |

Every mart is published as `<mart>/latest/*.parquet`; the manifest gives the glob and the row count.
`mart_aq_trend_monthly` also declares `min_month` and `max_month`, and `mart_aq_risk_daily` declares
`min_target_date` and `max_target_date`.

The manifest has an optional `stations` catalogue keyed by `station_id` (`label`, `station_type`,
`area_type`, `location_id`). It is required whenever the trend, risk or drivers mart is declared, and it
defines which station ids are known.

### Optional range fields of the two required marts

Two entries of `marts` carry optional range fields. They are additive: `SCHEMA_VERSION` stays `"1"`, a manifest
without them stays valid, and a consumer that does not know them ignores them.

| Entry | Field | Meaning |
|---|---|---|
| `mart_weather_aq_hourly` | `max_analysis_valid_at_utc` | Latest `valid_at_utc` of a row whose `source_type` is `analysis`, in the format of `max_valid_at_utc` (`YYYY-MM-DDTHH:MM:SSZ`). Left out when the mart has no analysis row |
| `mart_aq_daily` | `min_day`, `max_day` | First and last EEA day (`YYYY-MM-DD`) that has a row, gap rows included |

Why they exist: `max_valid_at_utc` is the end of the forecast hours, so the newest measured-or-analysed hour cannot be
read from it, and the daily mart (EEA station means) need not overlap the hourly window in time. A consumer that wants
"the latest real reading" uses `max_analysis_valid_at_utc` when present and falls back to `max_valid_at_utc`; one that
wants the span of the daily mart uses `min_day` and `max_day` when present and falls back to the hourly range.
When the fields are present they must equal what the Parquet files contain; the warehouse checks this
(`plovdiv.marts.core.validate_output`). `/api/v1/meta` passes them through unchanged.

### Changes to the contract

| Change | Version | Notes |
|---|---|---|
| Optional `max_analysis_valid_at_utc` (hourly) and `min_day`, `max_day` (daily) in `manifest.json` and in `MartRangeHourly` and `MartRangeDaily` of `openapi.yaml` | stays `"1"` | Additive. The synthetic fixture manifest carries them; only `fixtures/manifest.json` changed, no Parquet file |
| Optional trend, risk, drivers, model card and calibration marts, the `stations` catalogue and four endpoints | stays `"1"` | See above |

### Time conventions

- Warehouse keys are UTC. Europe/Sofia is applied only for display.
- `day` and `target_date` are EEA days (UTC+1, fixed), the same convention as `mart_aq_daily.day`. They are
  plain dates, so 2026-10-25, the 25-hour local day in Europe/Sofia, is one ordinary row.
- `month` is the first day of the month.
- `issued_at` and `trained_at` are UTC timestamps. Their names do not carry the `_utc` suffix used by
  `valid_at_utc` and `generated_at_utc`; the schema enforces the time zone.
- A probability must be issued before its target EEA day has ended.

### The new marts

`mart_aq_trend_monthly`: `observed_mean_ugm3` is the mean of the valid daily means in the month. It is
NULL exactly when `n_days` is 0. `weather_adjusted_mean_ugm3` is the mean after removing the estimated
effect of weather, and `adjusted_lo_ugm3` and `adjusted_hi_ugm3` bound it; the three are NULL together.

`mart_aq_risk_daily`: `probability` is between 0 and 1. `split` is `train`, `validation`, `test` or `live`.
`actual_label` is 0 or 1 and is NULL until the outcome is known, or when the day cannot be decided (for
example fewer than 18 valid hours). The label text is not repeated per row; it lives in `mart_model_card`
under `label_id`. There is one row per station, target date and model; if re-issued predictions are ever
published, `issued_at` would have to join the key.

`mart_aq_risk_drivers`: `contribution` is signed and on the log-odds scale; `rank` is by absolute
contribution, 1 being the largest. Only the top-ranked features are published, for the `test` and `live`
splits, so the contributions need not add up to the full log-odds.

`mart_model_card`: one row per model, with the training, validation and test windows (non-overlapping, in
that order; `trained_at` is the time the fitting ran and is not earlier than the end of the training window), the label, the feature list, test
metrics, two baseline AUCs, the decision threshold, `uses_forecast_weather` and a plain-language
limitations text. A metric that was not computed is NULL.

`mart_model_calibration`: reliability bins on the test split. Bins are contiguous from 0 to 1 and half-open
except the last. A bin with `n` of 0 has NULL `predicted_mean` and `observed_rate`.

## API

All responses use the columnar style of the existing endpoints, carry the `warehouse` block and
`attribution`, and report errors as `{"error": {"code": "...", "message": "..."}}`.

| Endpoint | Query parameters | Response |
|---|---|---|
| `GET /api/v1/aq/trend` | `station_id`, `from`, `to`, optional `pollutant` (default `pm10`) | Parallel arrays: `months`, `observed_mean`, `weather_adjusted_mean`, `adjusted_lo`, `adjusted_hi`, `n_days`; plus `unit` |
| `GET /api/v1/aq/risk` | `station_id`; `from` and `to` unless `latest=true`; optional `split`, `model_id`, `latest` | Parallel arrays: `target_dates`, `issued_at`, `probability`, `label_id`, `split`, `actual_label`, `model_id`; plus `drivers` (`feature`, `contribution`, `rank`) when `latest=true` |
| `GET /api/v1/model/card` | `model_id` | One object: `windows`, `metrics`, `baselines`, `features`, `threshold`, `label_text`, `limitations`, ... |
| `GET /api/v1/model/calibration` | `model_id` | Parallel arrays: `bin_lo`, `bin_hi`, `predicted_mean`, `observed_rate`, `n` |

Rules shared by the new endpoints:

- `from` and `to` are calendar dates (YYYY-MM-DD, UTC) and `to` is inclusive. The span is at most 366 days
  for `/aq/risk` and 7320 days for `/aq/trend`. There is no pagination; the span cap bounds the response.
- A month is returned by `/aq/trend` when any part of it falls inside the range.
- With `latest=true`, `from` and `to` are not allowed. The response holds the row with the most recent
  `issued_at` and its drivers.
- 404 `NOT_FOUND`: unknown station or unknown model. A known station or model with no matching rows is a
  200 with empty arrays.
- 422 `VALIDATION_ERROR`: missing or malformed parameter, `from` after `to`, span too long, or `from`/`to`
  combined with `latest=true`. Validation errors are 422, as on the existing endpoints.
- 503 `SERVICE_UNAVAILABLE`: a mart the request needs is not declared in the manifest. The mart name is in
  the message.

## Placeholder: forecast verification

A later mart, `mart_forecast_verification`, is reserved for comparing archived forecasts with what was
observed. It is a placeholder only: this contract defines no schema, manifest entry, fixture or endpoint for it,
and the name is reserved so that nothing else uses it. When it is defined it will be added as another optional
mart.

## Known gaps

Numbering follows ADR-001 in `plovdiv-frontend`.

| Gap | State |
|---|---|
| G1 No example API responses in the fixtures | Open. `tests/test_contract_aq_risk.py` builds reference payloads for the new endpoints from the fixtures and validates them against the response schemas |
| G2 `/meta` lists no locations or `source_type` values per variable | Open. Stations are now listed in the manifest and in `/meta`; locations and source types are not |
| G3 Inclusivity of `to` | Resolved. Stated as inclusive on every endpoint |
| G4 `resolution=daily` on `/series` undefined for non-air-quality variables | Open |
| G5 `source_type` optional in request and response | Open |
| G6 `/series` documents only 404 and 422 | Open. The new endpoints also document 503 |

Not covered yet: there is no endpoint for the drivers of a past day; drivers are returned only with
`latest=true`.
