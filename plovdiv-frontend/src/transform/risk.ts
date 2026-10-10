import type { AqRiskResponse, ModelCardResponse } from '../api/types'
import { MODEL_FEATURE_LABELS } from './featureLabels'
import { addDays, daysBetween, todayPlovdiv } from './time'

export interface Driver {
  feature: string
  contribution: number
  rank: number
}

export interface RiskRow {
  stationId: string
  targetDate: string
  issuedAt: string
  probability: number
  labelId: string
  split: string
  actualLabel: number | null
  modelId: string
  drivers: Driver[]
}

/** The row returned with latest=true, or null when the station has none. */
export function toLatestRisk(res: AqRiskResponse): RiskRow | null {
  const i = res.target_dates.length - 1
  if (i < 0) return null
  const drivers: Driver[] = []
  const d = res.drivers
  if (d) {
    for (let k = 0; k < d.feature.length; k += 1) {
      drivers.push({ feature: d.feature[k]!, contribution: d.contribution[k]!, rank: d.rank[k]! })
    }
    drivers.sort((a, b) => a.rank - b.rank)
  }
  return {
    stationId: res.station_id,
    targetDate: res.target_dates[i]!,
    issuedAt: res.issued_at[i]!,
    probability: res.probability[i]!,
    labelId: res.label_id[i]!,
    split: res.split[i]!,
    actualLabel: res.actual_label[i] ?? null,
    modelId: res.model_id[i]!,
    drivers,
  }
}

/** A row older than this many days (by target date) is not a current forecast. */
export const STALE_AFTER_DAYS = 3

export interface RiskAssessment {
  /** True when the row must not be presented as a forecast for tomorrow. */
  isHistorical: boolean
  notLive: boolean
  tooOld: boolean
  /**
   * An evaluation row scored by a model whose card says it uses no forecast weather, so historical
   * weather stood in. Live rows are scored with captured weather, so this never applies to them.
   */
  standInWeather: boolean
  today: string
  tomorrow: string
  isToday: boolean
  isTomorrow: boolean
}

export function assessRisk(
  row: Pick<RiskRow, 'split' | 'targetDate'>,
  card: Pick<ModelCardResponse, 'uses_forecast_weather'> | undefined,
  nowMs: number = Date.now(),
): RiskAssessment {
  const today = todayPlovdiv(nowMs)
  const tomorrow = addDays(today, 1)
  const notLive = row.split !== 'live'
  const tooOld = daysBetween(row.targetDate, today) > STALE_AFTER_DAYS
  return {
    isHistorical: notLive || tooOld,
    notLive,
    tooOld,
    standInWeather: notLive && card !== undefined && card.uses_forecast_weather === false,
    today,
    tomorrow,
    isToday: row.targetDate === today,
    isTomorrow: row.targetDate === tomorrow,
  }
}

const FEATURE_LABELS: Record<string, string> = {
  pm10_lag_1d: 'PM10 the day before',
  pm10_mean_7d: 'PM10 average of the past 7 days',
  no2_lag_1d: 'NO2 the day before',
  temperature_2m_forecast_mean: 'Forecast mean temperature',
  wind_gusts_10m_forecast_max: 'Forecast maximum wind gusts',
  precipitation_forecast_sum: 'Forecast total precipitation',
  is_heating_season: 'Heating season',
  is_weekend: 'Weekend',
}

/**
 * Readable name for a model feature: the real model's features first (featureLabels.ts), then the
 * names used by the contract fixtures; any other name is made readable by splitting on underscores.
 */
export function featureLabel(feature: string): string {
  const own = (labels: Record<string, string>) => (Object.prototype.hasOwnProperty.call(labels, feature) ? labels[feature] : undefined)
  const known = own(MODEL_FEATURE_LABELS) ?? own(FEATURE_LABELS)
  if (known) return known
  const words = feature.replace(/_/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export type Direction = 'raises' | 'lowers' | 'neutral'

export function direction(contribution: number): Direction {
  if (contribution > 0) return 'raises'
  if (contribution < 0) return 'lowers'
  return 'neutral'
}

export interface DriverBar extends Driver {
  label: string
  direction: Direction
  /** Contribution divided by the largest absolute contribution, from -1 to 1. */
  scaled: number
}

export function toDriverBars(drivers: Driver[]): DriverBar[] {
  const max = drivers.reduce((m, d) => Math.max(m, Math.abs(d.contribution)), 0)
  return drivers.map((d) => ({
    ...d,
    label: featureLabel(d.feature),
    direction: direction(d.contribution),
    scaled: max === 0 ? 0 : d.contribution / max,
  }))
}

export const SPLIT_LABELS: Record<string, string> = {
  train: 'Training data',
  validation: 'Validation data',
  test: 'Test data',
  live: 'Live',
  replay: 'Replay (scored afterwards)',
}

export function splitLabel(split: string): string {
  return SPLIT_LABELS[split] ?? split
}
