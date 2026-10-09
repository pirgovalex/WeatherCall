import type { MetaResponse } from '../api/types'
import { addDays, daysBetween, isoToEpoch, parseDateKey, plovdivDateKey } from './time'

export interface DataWindow {
  /** First and last Plovdiv calendar day that has hourly data. */
  minDay: string
  maxDay: string
  /** The same range as UTC calendar dates, for endpoints keyed on EEA days. */
  minUtcDay: string
  maxUtcDay: string
  /** Epoch seconds of the newest hourly analysis value in the warehouse. */
  maxEpoch: number
}

/**
 * The hourly data window, taken from the manifest ranges rather than the clock.
 *
 * The dashboard reads the hourly mart with source_type=analysis, so the window ends at the newest
 * analysis hour (`max_analysis_valid_at_utc`) when the manifest states it. `max_valid_at_utc` can
 * lie days later, in forecast hours that the dashboard does not request. A manifest written
 * before the optional field existed falls back to `max_valid_at_utc`.
 */
export function dataWindow(meta: MetaResponse): DataWindow {
  const hourly = meta.marts.mart_weather_aq_hourly
  const latest = hourly.max_analysis_valid_at_utc ?? hourly.max_valid_at_utc
  const minEpoch = isoToEpoch(hourly.min_valid_at_utc)
  // Never let a manifest put the analysis end before the start of the mart.
  const maxEpoch = Math.max(isoToEpoch(latest), minEpoch)
  return {
    minDay: plovdivDateKey(minEpoch * 1000),
    maxDay: plovdivDateKey(maxEpoch * 1000),
    minUtcDay: hourly.min_valid_at_utc.slice(0, 10),
    maxUtcDay: isoUtcDay(maxEpoch),
    maxEpoch,
  }
}

/** UTC calendar date (YYYY-MM-DD) of an instant given in epoch seconds. */
function isoUtcDay(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toISOString().slice(0, 10)
}

/** The span of the daily mart: first and last EEA day (plain UTC calendar dates). */
export interface DailyWindow {
  minDay: string
  maxDay: string
}

const DATE_KEY = /^\d{4}-\d{2}-\d{2}$/

/**
 * The daily window, from `min_day` and `max_day` of `mart_aq_daily` when the manifest states both.
 * The EEA daily series need not overlap the hourly window (the real one ends months before the
 * newest hourly data), so without those fields the hourly window is the fallback.
 */
export function dailyWindow(meta: MetaResponse): DailyWindow {
  const daily = meta.marts.mart_aq_daily
  const { min_day: minDay, max_day: maxDay } = daily
  if (minDay && maxDay && DATE_KEY.test(minDay) && DATE_KEY.test(maxDay) && minDay <= maxDay) return { minDay, maxDay }
  const hourly = dataWindow(meta)
  return { minDay: hourly.minUtcDay, maxDay: hourly.maxUtcDay }
}

/** How many EEA days the daily panel shows, counted back from the last day of the daily mart. */
export const DAILY_PANEL_DAYS = 90

/** The last `days` days of the daily window, never reaching before its first day. */
export function lastDailyDays(window: DailyWindow, days: number = DAILY_PANEL_DAYS): DayRange {
  const from = addDays(window.maxDay, -(days - 1))
  return { from: from < window.minDay ? window.minDay : from, to: window.maxDay }
}

export interface DayRange {
  from: string
  to: string
}

/** The last `days` Plovdiv days of data, never reaching before the first day. */
export function lastDays(window: DataWindow, days: number): DayRange {
  const from = addDays(window.maxDay, -(days - 1))
  return { from: from < window.minDay ? window.minDay : from, to: window.maxDay }
}

export function allDays(window: DataWindow): DayRange {
  return { from: window.minDay, to: window.maxDay }
}

export const MAX_SPAN_DAYS = 366

export function isValidRange(range: DayRange): boolean {
  const span = daysBetween(range.from, range.to) + 1
  return range.from <= range.to && span <= MAX_SPAN_DAYS
}

export const DEFAULT_DAYS = 3

/** Last day of the month that starts on `monthStart` (YYYY-MM-01). */
export function endOfMonth(monthStart: string): string {
  const { year, month } = parseDateKey(monthStart)
  const next = month === 12 ? `${year + 1}-01-01` : `${year}-${String(month + 1).padStart(2, '0')}-01`
  return addDays(next, -1)
}
