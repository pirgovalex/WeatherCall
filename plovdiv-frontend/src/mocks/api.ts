/**
 * A small stand-in for the backend, built from the JSON tables exported from the
 * contract fixtures (scripts/export_mock_data.py). It follows the rules in
 * contracts/README.md: columnar responses, the error envelope, 404 for unknown
 * ids, 422 for bad parameters and 503 when a mart is not declared.
 */
import manifest from '../../contracts/fixtures/manifest.json'
import hourly from './data/hourly.json'
import daily from './data/daily.json'
import trend from './data/trend.json'
import risk from './data/risk.json'
import drivers from './data/drivers.json'
import modelCard from './data/model_card.json'
import calibration from './data/calibration.json'
import type { Scenario } from './scenario'

export interface MockResult {
  status: number
  body: unknown
}

type Params = URLSearchParams

const warehouse = {
  run_id: manifest.run_id,
  schema_version: manifest.schema_version,
  generated_at_utc: manifest.generated_at_utc,
}

const envelope = (status: number, code: string, message: string): MockResult => ({
  status,
  body: { error: { code, message } },
})

const ok = (body: Record<string, unknown>): MockResult => ({
  status: 200,
  body: { ...body, warehouse, attribution: manifest.attribution },
})

const OPTIONAL_MARTS: Record<string, string> = {
  trend: 'mart_aq_trend_monthly',
  risk: 'mart_aq_risk_daily',
  drivers: 'mart_aq_risk_drivers',
  card: 'mart_model_card',
  calibration: 'mart_model_calibration',
}

function unavailable(scenario: Scenario, mart: string): MockResult | null {
  return scenario === 'unavailable'
    ? envelope(503, 'SERVICE_UNAVAILABLE', `The mart ${mart} is not declared in the manifest.`)
    : null
}

function required(params: Params, names: string[]): MockResult | null {
  const missing = names.filter((n) => !params.get(n))
  return missing.length > 0
    ? envelope(422, 'VALIDATION_ERROR', `Missing required parameter: ${missing.join(', ')}.`)
    : null
}

const isDate = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v)

export function metaBody(scenario: Scenario): unknown {
  if (scenario !== 'unavailable') return manifest
  const marts = { ...manifest.marts } as Record<string, unknown>
  for (const mart of Object.values(OPTIONAL_MARTS)) delete marts[mart]
  const rest = Object.fromEntries(Object.entries(manifest).filter(([key]) => key !== 'stations'))
  return { ...rest, marts }
}

function series(params: Params, scenario: Scenario): MockResult {
  const bad = required(params, ['variable', 'location', 'from', 'to', 'resolution'])
  if (bad) return bad
  const variable = params.get('variable')!
  if (!(variable in manifest.variables)) return envelope(404, 'NOT_FOUND', `Variable '${variable}' was not found.`)
  if (params.get('location') !== 'plovdiv') return envelope(404, 'NOT_FOUND', `Location '${params.get('location')}' was not found.`)
  const from = Date.parse(params.get('from')!) / 1000
  const to = Date.parse(params.get('to')!) / 1000
  if (Number.isNaN(from) || Number.isNaN(to)) return envelope(422, 'VALIDATION_ERROR', 'from and to must be ISO-8601 timestamps.')
  if (from > to) return envelope(422, 'VALIDATION_ERROR', 'from must not be after to.')
  if (to - from > 366 * 86_400) return envelope(422, 'VALIDATION_ERROR', 'The range is longer than 366 days.')
  const resolution = params.get('resolution') === 'daily' ? 'daily' : 'hourly'
  const sourceType = params.get('source_type') ?? 'analysis'
  const column = (hourly as unknown as Record<string, (number | string | null)[]>)[variable] ?? []
  const times = hourly.valid_at_utc
  const sources = hourly.source_type

  const timestamps: number[] = []
  const values: (number | null)[] = []
  if (scenario !== 'empty') {
    for (let i = 0; i < times.length; i += 1) {
      const t = times[i]!
      if (t < from || t > to || sources[i] !== sourceType) continue
      timestamps.push(t)
      values.push((column[i] as number | null) ?? null)
    }
  }
  let outTimes = timestamps
  let outValues = values
  if (resolution === 'daily') {
    const byDay = new Map<number, number[]>()
    timestamps.forEach((t, i) => {
      const day = Math.floor(t / 86_400) * 86_400
      const bucket = byDay.get(day) ?? []
      if (values[i] !== null) bucket.push(values[i] as number)
      byDay.set(day, bucket)
    })
    outTimes = [...byDay.keys()].sort((a, b) => a - b)
    outValues = outTimes.map((d) => {
      const b = byDay.get(d)!
      return b.length ? b.reduce((s, v) => s + v, 0) / b.length : null
    })
  }
  return ok({
    variable,
    unit: manifest.variables[variable as keyof typeof manifest.variables].unit,
    location_id: 'plovdiv',
    resolution,
    source_type: sourceType,
    timestamps: outTimes,
    values: outValues,
  })
}

function aqDaily(params: Params, scenario: Scenario): MockResult {
  const bad = required(params, ['pollutant', 'location', 'from', 'to'])
  if (bad) return bad
  const pollutant = params.get('pollutant')!
  if (!['pm2_5', 'pm10', 'no2'].includes(pollutant)) return envelope(404, 'NOT_FOUND', `Pollutant '${pollutant}' was not found.`)
  if (params.get('location') !== 'plovdiv') return envelope(404, 'NOT_FOUND', `Location '${params.get('location')}' was not found.`)
  const from = params.get('from')!.slice(0, 10)
  const to = params.get('to')!.slice(0, 10)
  if (!isDate(from) || !isDate(to)) return envelope(422, 'VALIDATION_ERROR', 'from and to must be dates.')
  if (from > to) return envelope(422, 'VALIDATION_ERROR', 'from must not be after to.')
  const out = { days: [] as string[], mean: [] as (number | null)[], valid_hours: [] as number[], is_valid_day: [] as boolean[] }
  if (scenario !== 'empty') {
    daily.day.forEach((day, i) => {
      if (daily.pollutant[i] !== pollutant || day < from || day > to) return
      out.days.push(day)
      out.mean.push(daily.daily_mean[i] ?? null)
      out.valid_hours.push(daily.valid_hours[i]!)
      out.is_valid_day.push(daily.is_valid_day[i]!)
    })
  }
  return ok({ pollutant, location_id: 'plovdiv', ...out })
}

const stationIds = Object.keys(manifest.stations)

function aqTrend(params: Params, scenario: Scenario): MockResult {
  const down = unavailable(scenario, OPTIONAL_MARTS.trend!)
  if (down) return down
  const bad = required(params, ['station_id', 'from', 'to'])
  if (bad) return bad
  const station = params.get('station_id')!
  if (!stationIds.includes(station)) return envelope(404, 'NOT_FOUND', `Station '${station}' was not found.`)
  const from = params.get('from')!
  const to = params.get('to')!
  if (!isDate(from) || !isDate(to) || from > to) return envelope(422, 'VALIDATION_ERROR', 'from and to must be dates with from not after to.')
  const pollutant = params.get('pollutant') ?? 'pm10'
  const out = {
    months: [] as string[],
    observed_mean: [] as (number | null)[],
    weather_adjusted_mean: [] as (number | null)[],
    adjusted_lo: [] as (number | null)[],
    adjusted_hi: [] as (number | null)[],
    n_days: [] as number[],
  }
  if (scenario !== 'empty') {
    trend.month.forEach((month, i) => {
      if (trend.station_id[i] !== station || trend.pollutant[i] !== pollutant) return
      const [y, m] = month.split('-').map(Number) as [number, number]
      const lastDay = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10)
      if (month > to || lastDay < from) return
      out.months.push(month)
      out.observed_mean.push(trend.observed_mean_ugm3[i] ?? null)
      out.weather_adjusted_mean.push(trend.weather_adjusted_mean_ugm3[i] ?? null)
      out.adjusted_lo.push(trend.adjusted_lo_ugm3[i] ?? null)
      out.adjusted_hi.push(trend.adjusted_hi_ugm3[i] ?? null)
      out.n_days.push(trend.n_days[i]!)
    })
  }
  return ok({ station_id: station, pollutant, unit: manifest.variables.pm10.unit, ...out })
}

function aqRisk(params: Params, scenario: Scenario): MockResult {
  const down = unavailable(scenario, OPTIONAL_MARTS.risk!)
  if (down) return down
  const bad = required(params, ['station_id'])
  if (bad) return bad
  const station = params.get('station_id')!
  if (!stationIds.includes(station)) return envelope(404, 'NOT_FOUND', `Station '${station}' was not found.`)
  const latest = params.get('latest') === 'true'
  const modelId = params.get('model_id')
  if (modelId && !modelCard.model_id.includes(modelId)) return envelope(404, 'NOT_FOUND', `Model '${modelId}' was not found.`)
  const from = params.get('from')
  const to = params.get('to')
  if (latest && (from || to)) return envelope(422, 'VALIDATION_ERROR', 'from and to are not allowed with latest=true.')
  if (!latest) {
    const range = required(params, ['from', 'to'])
    if (range) return range
    if (!isDate(from!) || !isDate(to!) || from! > to!) return envelope(422, 'VALIDATION_ERROR', 'from and to must be dates with from not after to.')
  }
  const split = params.get('split')

  let rows: number[] = []
  if (scenario !== 'empty') {
    risk.station_id.forEach((s, i) => {
      if (s !== station) return
      if (split && risk.split[i] !== split) return
      if (modelId && risk.model_id[i] !== modelId) return
      if (!latest && (risk.target_date[i]! < from! || risk.target_date[i]! > to!)) return
      // The historical scenario hides live rows, so the newest row is a past evaluation row.
      if (scenario === 'historical' && risk.split[i] === 'live') return
      rows.push(i)
    })
  }
  if (latest && rows.length > 0) {
    rows.sort((a, b) => (risk.issued_at[a]! + risk.target_date[a]!).localeCompare(risk.issued_at[b]! + risk.target_date[b]!))
    rows = [rows[rows.length - 1]!]
  } else {
    rows.sort((a, b) => risk.target_date[a]!.localeCompare(risk.target_date[b]!))
  }
  const body: Record<string, unknown> = {
    station_id: station,
    target_dates: rows.map((i) => risk.target_date[i]),
    issued_at: rows.map((i) => risk.issued_at[i]),
    probability: rows.map((i) => risk.probability[i]),
    label_id: rows.map((i) => risk.label_id[i]),
    split: rows.map((i) => risk.split[i]),
    actual_label: rows.map((i) => risk.actual_label[i] ?? null),
    model_id: rows.map((i) => risk.model_id[i]),
  }
  if (latest) {
    const downDrivers = unavailable(scenario, OPTIONAL_MARTS.drivers!)
    if (downDrivers) return downDrivers
    const row = rows[0]
    const picked: number[] = []
    if (row !== undefined) {
      drivers.station_id.forEach((s, i) => {
        if (s === station && drivers.target_date[i] === risk.target_date[row] && drivers.model_id[i] === risk.model_id[row]) picked.push(i)
      })
      picked.sort((a, b) => drivers.rank[a]! - drivers.rank[b]!)
    }
    body.drivers = {
      feature: picked.map((i) => drivers.feature[i]),
      contribution: picked.map((i) => drivers.contribution[i]),
      rank: picked.map((i) => drivers.rank[i]),
    }
  }
  return ok(body)
}

function cardIndex(params: Params): { index: number } | { error: MockResult } {
  const bad = required(params, ['model_id'])
  if (bad) return { error: bad }
  const index = modelCard.model_id.indexOf(params.get('model_id')!)
  if (index < 0) return { error: envelope(404, 'NOT_FOUND', `Model '${params.get('model_id')}' was not found.`) }
  return { index }
}

function card(params: Params, scenario: Scenario): MockResult {
  const down = unavailable(scenario, OPTIONAL_MARTS.card!)
  if (down) return down
  const found = cardIndex(params)
  if ('error' in found) return found.error
  const i = found.index
  const c = modelCard
  return ok({
    model_id: c.model_id[i],
    algorithm: c.algorithm[i],
    trained_at: c.trained_at[i],
    windows: {
      train_start: c.train_start[i],
      train_end: c.train_end[i],
      validation_start: c.validation_start[i],
      validation_end: c.validation_end[i],
      test_start: c.test_start[i],
      test_end: c.test_end[i],
    },
    label_id: c.label_id[i],
    label_text: c.label_text[i],
    features: c.features[i],
    uses_forecast_weather: c.uses_forecast_weather[i],
    threshold: c.threshold[i],
    metrics: {
      roc_auc_test: c.roc_auc_test[i] ?? null,
      pr_auc_test: c.pr_auc_test[i] ?? null,
      brier_test: c.brier_test[i] ?? null,
      precision_test: c.precision_test[i] ?? null,
      recall_test: c.recall_test[i] ?? null,
    },
    baselines: {
      roc_auc_test_climatology: c.baseline_roc_auc_test_climatology[i] ?? null,
      roc_auc_test_persistence: c.baseline_roc_auc_test_persistence[i] ?? null,
    },
    limitations: c.limitations[i],
  })
}

function calibrationBody(params: Params, scenario: Scenario): MockResult {
  const down = unavailable(scenario, OPTIONAL_MARTS.calibration!)
  if (down) return down
  const found = cardIndex(params)
  if ('error' in found) return found.error
  const model = modelCard.model_id[found.index]
  const rows = calibration.model_id.map((m, i) => (m === model ? i : -1)).filter((i) => i >= 0)
  return ok({
    model_id: model,
    bin_lo: rows.map((i) => calibration.bin_lo[i]),
    bin_hi: rows.map((i) => calibration.bin_hi[i]),
    predicted_mean: rows.map((i) => calibration.predicted_mean[i] ?? null),
    observed_rate: rows.map((i) => calibration.observed_rate[i] ?? null),
    n: rows.map((i) => calibration.n[i]),
  })
}

export const MOCK_ROUTES: Record<string, (params: Params, scenario: Scenario) => MockResult> = {
  '/api/v1/series': series,
  '/api/v1/aq/daily': aqDaily,
  '/api/v1/aq/trend': aqTrend,
  '/api/v1/aq/risk': aqRisk,
  '/api/v1/model/card': card,
  '/api/v1/model/calibration': calibrationBody,
}

/** Resolve a data request for the given scenario. The `error` scenario fails every data request. */
export function resolveMock(pathname: string, params: Params, scenario: Scenario): MockResult | null {
  const route = MOCK_ROUTES[pathname]
  if (!route) return null
  if (scenario === 'error') return envelope(500, 'INTERNAL_ERROR', 'The mock API is set to fail every data request.')
  return route(params, scenario)
}
