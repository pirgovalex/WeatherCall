import type { AqTrendResponse } from '../api/types'
import { dateKeyToEpoch, formatMonthKey } from './time'
import { formatNumber, formatUnit } from './units'

export interface TrendView {
  unit: string
  months: string[]
  /** Epoch seconds at 00:00 UTC of the first day of each month. */
  x: number[]
  observed: (number | null)[]
  adjusted: (number | null)[]
  lo: (number | null)[]
  hi: (number | null)[]
  nDays: number[]
}

export function toTrendView(res: AqTrendResponse): TrendView {
  return {
    unit: res.unit,
    months: res.months,
    x: res.months.map(dateKeyToEpoch),
    observed: res.observed_mean,
    adjusted: res.weather_adjusted_mean,
    lo: res.adjusted_lo,
    hi: res.adjusted_hi,
    nDays: res.n_days,
  }
}

/** Months with fewer valid days than this are flagged as low coverage. */
export const LOW_COVERAGE_DAYS = 15

export function isLowCoverage(nDays: number): boolean {
  return nDays > 0 && nDays < LOW_COVERAGE_DAYS
}

/**
 * One sentence about the most recent month that has both lines, explaining what
 * the gap between them means. It says "within the band" only when the observed
 * mean falls inside the interval, and never goes beyond that.
 */
export function describeLatestMonth(view: TrendView): string | null {
  for (let i = view.months.length - 1; i >= 0; i -= 1) {
    const obs = view.observed[i]
    const adj = view.adjusted[i]
    const lo = view.lo[i]
    const hi = view.hi[i]
    if (obs == null || adj == null || lo == null || hi == null) continue
    const unit = formatUnit(view.unit)
    const month = formatMonthKey(view.months[i]!)
    const head = `${month}: observed ${formatNumber(obs)} ${unit}, weather-adjusted ${formatNumber(adj)} ${unit}.`
    if (obs >= lo && obs <= hi) {
      return `${head} The observed mean lies inside the uncertainty band, so weather explains little of the difference.`
    }
    return obs > hi
      ? `${head} The observed mean is above the band: weather that month is estimated to have raised the average.`
      : `${head} The observed mean is below the band: weather that month is estimated to have lowered the average.`
  }
  return null
}

export function describeTrend(stationLabel: string, view: TrendView): string {
  const valid = view.observed.filter((v) => v !== null).length
  if (view.months.length === 0) return `${stationLabel}: no months in the selected range.`
  const first = formatMonthKey(view.months[0]!)
  const last = formatMonthKey(view.months[view.months.length - 1]!)
  const empty = view.months.length - valid
  const gap = empty > 0 ? ` ${empty} month${empty === 1 ? '' : 's'} had no valid days and appear as gaps.` : ''
  const latest = describeLatestMonth(view)
  return `${stationLabel}, monthly PM10 means from ${first} to ${last}.${gap}${latest ? ` ${latest}` : ''}`
}
