# ADR-001: Dashboard architecture

**Status:** Accepted and implemented. Decision 3 is partly open: example responses are not yet owned by the contract.
**Date:** 2026-10-07
**Deciders:** Project owner

Numbering is per repository. The warehouse repository keeps its own ADR sequence.

## Context

The frontend is a read-only dashboard for the Plovdiv weather and air-quality warehouse. It is a plain Vite single-page application written in SolidJS, styled with Tailwind CSS and fetching data with TanStack Query. It consumes only the HTTP API described in `contracts/openapi.yaml`. It does not read Parquet and does not embed DuckDB.

Requirements:

- A variable selector, a date-range selector and one time-series chart.
- Gaps in the data render as gaps, not as interpolated lines.
- The `source_type` of the series is visible, so modelled values are never presented as measurements.
- Timestamps arrive as UTC epoch seconds and are shown in Europe/Sofia, correctly across the daylight-saving change on 2026-10-25.
- Loading, error (using the API error envelope) and empty states, plus a footer with attribution, run id and generation time from `/meta`.

### Facts that shaped the decisions

- The first captured responses show that the forecast and air-quality endpoints snap Plovdiv to different grid cells (42.25, 24.75 and 42.1, 24.8). One `location_id` therefore covers two cells.
- Wind is returned in km/h by default while the contract declares m/s. The UI trusts only the unit declared in the manifest.
- The installed toolchain was vite 6.4.4 and TypeScript 5.8.3. The chosen test tooling needs TypeScript 5.9 or newer (msw 3) and Node 22.12 or newer.

### Known contract gaps

| # | Gap | Handling |
|---|---|---|
| G1 | No example API responses in the fixtures (Parquet and manifest only). | Decision 3. |
| G2 | `/meta` lists neither locations nor the `source_type` values available per variable. | One constant for `plovdiv`; empty and 404 results are normal states. Candidate for contract v2. |
| G3 | Inclusivity of `to` is not stated. | Treat `to` as inclusive (the last fixture row is 23:00 and the example uses `T23:00:00Z`) and document it in `openapi.yaml`. |
| G4 | `resolution=daily` on `/series` is undefined for non-air-quality variables. | Use `hourly` only for now. |
| G5 | `source_type` is optional in the request and in the response. | Always send it explicitly. |
| G6 | `/series` documents 404 and 422 only. | Any other failure becomes an `ApiError` with code `NETWORK` or `UNKNOWN`. |

## Decision

Four layers with strictly one-way imports:

```
src/
  api/        generated types, fetch wrapper, ApiError
  queries/    TanStack Query hooks (no JSX)
  transform/  payload to chart series, time helpers
  components/ one component per file, props in and JSX out
  mocks/      MSW handlers for tests and optional mock mode
  App.tsx     composition only
```

### 1. Chart library: uPlot, with a written swap criterion

| | uPlot 1.6.32 | ECharts 6.1.0 |
|---|---|---|
| Null gaps | Native, `spanGaps` off by default | `connectNulls` off by default |
| Input | Epoch seconds | Milliseconds |
| Europe/Sofia axis | `tzDate` accepts an IANA zone | Local or UTC only, as far as known (to verify) |
| Size | Very small | Large |
| Tooltip | Needs a plugin | Built in |
| Last release | 2025-03-14 | 2026-05-19 |

uPlot is chosen because time-zone correctness and gaps are first-class there. After a spike of about three hours, switch to ECharts if the axis cannot show correct Plovdiv ticks across 2026-10-25, or if the tooltip plugin grows beyond roughly 100 lines. The chart sits behind one component with a library-neutral prop shape, so a swap touches one file. The uPlot stylesheet is imported from `node_modules` and is the single exception to the "no custom CSS" rule.

### 2. API types: generated types and a thin fetch wrapper

`openapi-typescript` produces types from `contracts/openapi.yaml` (`npm run gen:api`; CI fails if regeneration changes the output). A small hand-written wrapper builds query strings, parses the error envelope into `ApiError` and checks that `timestamps` and `values` have equal length and ascending order. A generated runtime client is deliberately not used.

### 3. Mock data: example responses owned by the contract

The warehouse fixture generator should also write example API responses (`meta`, a series, a daily air-quality payload, and the 404 and 422 envelopes) next to the Parquet fixtures. The frontend copies `contracts/` as before and serves those files through MSW. The same files can drive the backend's contract tests, so both sides test against identical payloads. Until that exists, a throwaway export from the Parquet fixtures keeps the frontend unblocked.

### 4. Time: `Intl` only, Europe/Sofia fixed

Display uses `Intl.DateTimeFormat` with `timeZone: 'Europe/Sofia'`. The date-range selector works in Plovdiv calendar days and converts them to UTC with a helper based on `Intl` offsets, never `new Date(y, m, d)` and never "add 86 400 seconds".

| Plovdiv day | UTC from | UTC to (exclusive) | Hours |
|---|---|---|---|
| 2026-10-24 | 2026-10-23T21:00Z | 2026-10-24T21:00Z | 24 |
| 2026-10-25 | 2026-10-24T21:00Z | 2026-10-25T22:00Z | 25 |
| 2026-10-26 | 2026-10-25T22:00Z | 2026-10-26T22:00Z | 24 |

### 5. Data fetching

Query keys are `['series', runId, variable, location, sourceType, from, to]`, where `runId` comes from `/meta`, so a new pipeline run invalidates cached series. Series have a long `staleTime`, `/meta` about a minute, 4xx responses are not retried, and `placeholderData: keepPreviousData` avoids an empty chart while a new range loads.

### 6. Testing and enforcement

| Concern | Mechanism |
|---|---|
| Payload to series (epoch preserved, nulls as gaps, equal lengths) | Vitest, pure functions |
| Plovdiv 25-hour day | Vitest on the time helpers, using the table above |
| Loading, error, empty and success | `@solidjs/testing-library`, jsdom and MSW, with `uplot` mocked |
| Attribution footer and `source_type` label | Component tests fed by MSW |
| No inline styles | ESLint `no-restricted-syntax` on `JSXAttribute[name.name='style']`, plus a grep test |
| No DuckDB or Parquet in the browser | Test over `package.json` and `src/` |
| Production build has no console errors | Playwright against `vite preview` |

### 7. Development topology

The Vite dev server proxies `/api` and `/healthz` to `http://127.0.0.1:8000`, so the browser sees a single origin. `VITE_API_BASE_URL` defaults to empty. Mock mode is opt-in with `VITE_USE_MOCKS=1`.

## Consequences

- Easier: swapping the chart, running the UI without a backend, catching contract drift in CI, and reasoning about the 25-hour day.
- Harder: the contract repository needs a small addition (Decision 3), and TypeScript, and possibly Node, must be upgraded.
- To revisit: contract v2 for G2 to G4, and a way to show that weather and air quality come from different grid cells.
- Until station data is loaded, the `source_type` label must read `analysis` or `forecast`. The UI must never imply ground truth.

## Implementation notes

Recorded after the dashboard was built (Now, History, Forecasts and Method pages).

- **Decision 1 (chart library).** uPlot handled every chart: hourly series, the observed against weather-adjusted trend with a band, the daily chart and the calibration chart with its diagonal. The Plovdiv axis is correct across 2026-10-25 using `tzDate` with the IANA zone, with custom 24-hour tick labels. The tooltip is a plugin of about 60 lines. ECharts was not needed and is not a dependency. `UPlotChart.tsx` is the only file that imports uPlot.
- **Decision 3 (mock data).** The contract does not yet ship example responses, so `scripts/export_mock_data.py` exports the Parquet fixtures to JSON tables in `src/mocks/data/`, and `src/mocks/api.ts` builds contract-shaped responses from them. The interim approach stays in place until the contract repository adds example payloads.
- **Decision 5 (data fetching).** Query keys carry the `runId` from `/meta` for every endpoint, and data queries stay disabled until it is known. The retry policy lives in `createQueryClient`: no retry on 4xx or 503, two retries on network failure. The data window for default ranges comes from the mart ranges in `/meta`, not from the clock.
- **Decision 6 (testing).** `msw/node` could not be used because it conflicts with the `browser` export condition applied by the Solid plugin. `src/mocks/testServer.ts` uses MSW's `getResponse` against a stubbed `fetch`. Mock mode in the browser uses the normal service worker.
- **Scope beyond the original requirements.** The page set grew to four pages. Light and dark themes use CSS variables, and mock scenarios are selected with `?mock=`.

### Additional contract gaps found

| # | Gap | Handling |
|---|---|---|
| G7 | No endpoint lists models. | The current model is taken from the latest risk row of the first station, then `/model/card` and `/model/calibration` are fetched for it. |
| G8 | `mart_aq_daily` has no min and max in `/meta`. | The hourly window is used for default daily ranges. |
| G9 | Only ROC-AUC is reported for the climatology and persistence baselines. | The performance table shows other baseline metrics as not available. |
| G10 | Drivers are returned only with `latest=true`. | Driver bars are shown for the latest row only. |
| G11 | The warehouse README does not mention ERA5, and lists EEA as planned. | The Method page states ERA5 as served by Open-Meteo and EEA as planned. Wording should be confirmed against the warehouse. |

## Action items

Status after the build:

1. Done: Node 22 and TypeScript `~5.9`.
2. Open (contract repository): example API responses, and the statement on `to` inclusivity (G3). The frontend treats `to` as inclusive.
3. Done: ESLint, Vitest, jsdom, testing-library, MSW and the `gen:api` script.
4. Done: `transform/` and the time helpers, with tests.
5. Done: the chart spike. uPlot satisfies the criterion, so no ADR-002 is needed for a swap.
6. Done: pages built against MSW and the dev proxy configured. Not yet exercised against a running backend.
