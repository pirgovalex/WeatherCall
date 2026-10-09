# Plovdiv Weather Dashboard

A read-only dashboard for the Plovdiv weather and air-quality warehouse. It is a single-page application built with SolidJS and Vite, styled with Tailwind CSS, fetching data with TanStack Query and drawing charts with uPlot.

The dashboard talks only to the HTTP API described in `contracts/openapi.yaml`. It does not read Parquet files and does not embed a database.

## Pages

| Page | Content | Endpoints |
|---|---|---|
| Now | Latest readings per variable, with the `source_type` shown. A card for the next-day PM10 exceedance risk: probability meter, plain-language label, signed driver bars, model id, split and target date. | `/api/v1/meta`, `/api/v1/series`, `/api/v1/aq/risk?latest=true`, `/api/v1/model/card` |
| History | PM10 trend, observed against weather-adjusted with an uncertainty band; station switcher (Kamenitsa background, Trakia traffic); daily air-quality chart. | `/api/v1/aq/trend`, `/api/v1/aq/daily` |
| Forecasts | Calibration reliability chart, performance table against the climatology and persistence baselines, and an empty state for forecast verification. | `/api/v1/model/calibration`, `/api/v1/model/card` |
| Method | Data sources and attribution, the model card in prose with its limitations, and how to read each page. | `/api/v1/model/card`, `/api/v1/meta` |

Behaviour that applies throughout:

- Missing values appear as gaps, never as interpolated lines.
- Times are transferred in UTC and shown in Europe/Sofia, including the 25-hour day on 2026-10-25. Only `Intl` is used; there is no date library.
- A risk row is labelled as historical, and is never described as tomorrow's forecast, when its split is not `live` or its target date is more than three days before today in Europe/Sofia. A separate notice appears when the row was scored with historical weather instead of a forecast (`uses_forecast_weather` is false).
- Every data component has loading, empty, error-envelope and 503 states.
- Each chart has a text summary, keyboard stepping with arrow keys, and a data table behind a disclosure.
- Light and dark themes follow the system setting and can be overridden from the header. Colours are CSS variables defined in `src/index.css`.
- The footer shows attribution, the warehouse run id, the generation time and the contract version.

## Getting started

Requires Node.js 22.12 or newer.

```powershell
npm install
npm run dev
```

The development server runs on http://localhost:5173.

### With mock data (no backend)

```powershell
$env:VITE_USE_MOCKS = "1"; npm run dev
```

Mock mode starts an MSW service worker that answers every `/api/v1` request from the JSON tables in `src/mocks/data/`. Those tables are exported from the contract Parquet fixtures, so no value is invented. Add `?mock=<scenario>` to the address to reach other states:

| Scenario | Effect |
|---|---|
| `default` | Normal responses. |
| `historical` | Live risk rows are hidden, so the newest risk row is a past evaluation row. |
| `empty` | Data endpoints return empty results. |
| `unavailable` | The optional marts are dropped from `/meta` and their endpoints return 503. |
| `error` | Every data request returns a 500 error envelope. |
| `slow` | Responses are delayed to show loading states. |

### With the backend

By default the Vite dev server and `vite preview` proxy `/api` and `/healthz` to `http://127.0.0.1:8000`, so the browser sees one origin. Use `DEV_API_TARGET` to point the proxy elsewhere. Set `VITE_API_BASE_URL` only when the API is served from a different origin and handles CORS itself. See `.env.example`.

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Development server. |
| `npm run build` | Type-check, then production build into `dist/`. |
| `npm run preview` | Serve the production build. |
| `npm run lint` | ESLint, including the rule against inline styles. |
| `npm run typecheck` | `tsc -b`. |
| `npm test` | Vitest, once. `npm run test:watch` watches. |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from `contracts/openapi.yaml`. |
| `npm run export:mocks` | Regenerate `src/mocks/data/*.json` from the contract fixtures. Needs Python with `pandas` and `pyarrow`. |

## Project layout

```
src/
  api/          generated types, typed fetch wrapper, response checks
  queries/      TanStack Query hooks, retry policy, run-id context
  transform/    payloads to chart specs and view models, time helpers
  components/   layout, ui, charts, and one folder per page
  pages/        Now, History, Forecasts, Method
  mocks/        MSW handlers, fake backend, scenarios, exported JSON tables
  theme/        light and dark theme handling
  test/         test setup and render helpers
scripts/        export_mock_data.py
contracts/      copy of the warehouse data contract (do not edit)
docs/           UI guidelines and architecture decision records
```

Imports run one way: `api`, then `queries`, then `transform`, then `components` and `pages`. `components/charts/UPlotChart.tsx` is the only file that imports uPlot; everything else passes a library-neutral chart spec.

## Testing

Tests use Vitest, jsdom, `@solidjs/testing-library` and MSW. The API is served by the same handlers as mock mode. Because `msw/node` conflicts with the `browser` export condition used by the Solid plugin, `src/mocks/testServer.ts` resolves requests with MSW's `getResponse` against a stubbed `fetch`. uPlot is replaced by a small fake in component tests.

Covered: time helpers including the 25-hour day, payload transforms, the API client and its error handling, the risk historical rules, every page in its loading, empty, error, 503 and success states, and project rules (no inline styles, no Parquet or DuckDB in the browser, no date library, query keys include the run id).

## Documentation

- [UI guidelines](docs/ui-guidelines.md)
- [ADR-001: Dashboard architecture](docs/adr/001-dashboard-architecture.md)

## Data sources

Weather and air-quality values come from the warehouse, which takes them from Open-Meteo (CC BY 4.0, https://open-meteo.com) and Copernicus Atmosphere Monitoring Service (CAMS) Europe. Station observations come from the European Environment Agency (verified daily PM10 and PM2.5 for Kamenitsa, PM10 for Trakia). The fixtures in `contracts/fixtures` and the mock data derived from them are synthetic. Every series states whether it is `analysis`, `forecast` or `observed`. The Method page carries the full attribution text.

## Contract

`contracts/` is a verbatim copy of the warehouse repository's contract. Do not edit it here.
