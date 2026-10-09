import type { SeriesResponse, SourceType } from '../api/types'
import { formatPlovdivDateTime } from './time'
import { formatNumber, formatUnit } from './units'

export interface SeriesView {
  variable: string
  unit: string
  sourceType: SourceType | undefined
  /** Epoch seconds, ascending. Missing hours are present with a null value. */
  x: number[]
  y: (number | null)[]
}

const HOUR = 3600

/**
 * Turn a series payload into plotting arrays. Nulls stay nulls so the chart
 * draws gaps. For hourly data, hours that are absent from the payload are filled
 * with nulls too, so a missing stretch is a gap rather than a straight line.
 */
export function toSeriesView(res: SeriesResponse): SeriesView {
  const x: number[] = []
  const y: (number | null)[] = []
  const hourly = res.resolution === 'hourly'
  for (let i = 0; i < res.timestamps.length; i += 1) {
    const t = res.timestamps[i]!
    if (hourly && x.length > 0) {
      let prev = x[x.length - 1]!
      while (t - prev > HOUR) {
        prev += HOUR
        x.push(prev)
        y.push(null)
      }
    }
    x.push(t)
    y.push(res.values[i] ?? null)
  }
  return { variable: res.variable, unit: res.unit, sourceType: res.source_type, x, y }
}

export interface Reading {
  time: number
  value: number
}

/** The newest non-null value, or null when the window has none. */
export function latestReading(view: SeriesView): Reading | null {
  for (let i = view.y.length - 1; i >= 0; i -= 1) {
    const v = view.y[i]
    if (v !== null && v !== undefined) return { time: view.x[i]!, value: v }
  }
  return null
}

export interface SeriesStats {
  total: number
  missing: number
  min: Reading | null
  max: Reading | null
}

export function seriesStats(view: SeriesView): SeriesStats {
  let min: Reading | null = null
  let max: Reading | null = null
  let missing = 0
  for (let i = 0; i < view.y.length; i += 1) {
    const v = view.y[i]
    if (v === null || v === undefined) {
      missing += 1
      continue
    }
    if (min === null || v < min.value) min = { time: view.x[i]!, value: v }
    if (max === null || v > max.value) max = { time: view.x[i]!, value: v }
  }
  return { total: view.y.length, missing, min, max }
}

const SOURCE_LABELS: Record<SourceType, { short: string; long: string }> = {
  station: { short: 'Station measurement', long: 'Measured at a monitoring station' },
  analysis: { short: 'Model analysis', long: 'Modelled value for a grid cell, not a station measurement' },
  reanalysis_provisional: {
    short: 'Reanalysis (provisional)',
    long: 'Reanalysis value that may still be revised; not a station measurement',
  },
  reanalysis_final: { short: 'Reanalysis (final)', long: 'Final reanalysis value; not a station measurement' },
  forecast: { short: 'Forecast', long: 'Forecast value, not yet observed' },
}

export function sourceLabel(sourceType: SourceType | undefined): { short: string; long: string } {
  return sourceType ? SOURCE_LABELS[sourceType] : { short: 'Source not stated', long: 'The API did not state the source of these values' }
}

/** Plain-language summary used as the accessible description of a time-series chart. */
export function describeSeries(label: string, view: SeriesView): string {
  const stats = seriesStats(view)
  const unit = formatUnit(view.unit)
  if (stats.total === 0 || stats.min === null || stats.max === null) {
    return `${label}: no values in the selected period.`
  }
  const first = formatPlovdivDateTime(view.x[0]!)
  const last = formatPlovdivDateTime(view.x[view.x.length - 1]!)
  const gaps = stats.missing === 0 ? 'No hours are missing.' : `${stats.missing} of ${stats.total} hours have no value and appear as gaps.`
  return (
    `${label}, hourly, ${first} to ${last}, Europe/Sofia time. ` +
    `Lowest ${formatNumber(stats.min.value)} ${unit} at ${formatPlovdivDateTime(stats.min.time)}, ` +
    `highest ${formatNumber(stats.max.value)} ${unit} at ${formatPlovdivDateTime(stats.max.time)}. ${gaps}`
  )
}
