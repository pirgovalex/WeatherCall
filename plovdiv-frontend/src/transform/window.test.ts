import { describe, expect, it } from 'vitest'
import manifest from '../../contracts/fixtures/manifest.json'
import type { MetaResponse } from '../api/types'
import { isoToEpoch } from './time'
import { DAILY_PANEL_DAYS, dailyWindow, dataWindow, lastDailyDays, lastDays } from './window'

type Hourly = MetaResponse['marts']['mart_weather_aq_hourly']
type Daily = MetaResponse['marts']['mart_aq_daily']

/** The fixture manifest with the two required mart entries replaced. */
function meta(hourly: Partial<Hourly>, daily: Partial<Daily> = {}, drop: { hourly?: (keyof Hourly)[]; daily?: (keyof Daily)[] } = {}): MetaResponse {
  const h: Record<string, unknown> = { ...manifest.marts.mart_weather_aq_hourly, ...hourly }
  const d: Record<string, unknown> = { ...manifest.marts.mart_aq_daily, ...daily }
  for (const key of drop.hourly ?? []) delete h[key]
  for (const key of drop.daily ?? []) delete d[key]
  return { ...manifest, marts: { ...manifest.marts, mart_weather_aq_hourly: h, mart_aq_daily: d } } as unknown as MetaResponse
}

// The real warehouse run: forecast hours run days past the last analysis hour, and the EEA series
// ends months before the hourly data begins.
const REAL_HOURLY = {
  min_valid_at_utc: '2026-09-30T00:00:00Z',
  max_valid_at_utc: '2026-10-13T23:00:00Z',
  max_analysis_valid_at_utc: '2026-10-07T14:00:00Z',
}
const REAL_DAILY = { min_day: '2013-01-01', max_day: '2025-12-31' }

describe('dataWindow', () => {
  it('ends at the newest analysis hour when the manifest states it', () => {
    const w = dataWindow(meta(REAL_HOURLY, REAL_DAILY))
    expect(w.maxEpoch).toBe(isoToEpoch('2026-10-07T14:00:00Z'))
    expect(w.maxDay).toBe('2026-10-07') // 17:00 in Plovdiv
    expect(w.maxUtcDay).toBe('2026-10-07')
    expect(w.minDay).toBe('2026-09-30')
    expect(w.minUtcDay).toBe('2026-09-30')
  })

  it('falls back to max_valid_at_utc when the manifest has no analysis end', () => {
    const w = dataWindow(meta({ ...REAL_HOURLY }, REAL_DAILY, { hourly: ['max_analysis_valid_at_utc'] }))
    expect(w.maxEpoch).toBe(isoToEpoch('2026-10-13T23:00:00Z'))
    expect(w.maxUtcDay).toBe('2026-10-13')
    expect(w.maxDay).toBe('2026-10-14') // 02:00 in Plovdiv
  })

  it('keeps the Plovdiv day and the UTC day apart for an analysis hour late in the UTC evening', () => {
    const w = dataWindow(meta({ ...REAL_HOURLY, max_analysis_valid_at_utc: '2026-10-07T22:00:00Z' }))
    expect(w.maxUtcDay).toBe('2026-10-07')
    expect(w.maxDay).toBe('2026-10-08')
  })

  it('anchors "the last days" on the analysis end, not on the forecast end', () => {
    const w = dataWindow(meta(REAL_HOURLY))
    expect(lastDays(w, 3)).toEqual({ from: '2026-10-05', to: '2026-10-07' })
    expect(lastDays(w, 30)).toEqual({ from: '2026-09-30', to: '2026-10-07' })
  })

  it('never ends before the first hour, whatever the manifest says', () => {
    const w = dataWindow(meta({ ...REAL_HOURLY, max_analysis_valid_at_utc: '2026-09-01T00:00:00Z' }))
    expect(w.maxEpoch).toBe(isoToEpoch(REAL_HOURLY.min_valid_at_utc))
  })

  it('is unchanged for the fixture manifest, whose hours are all analysis', () => {
    const w = dataWindow(manifest as unknown as MetaResponse)
    expect(w.maxEpoch).toBe(isoToEpoch('2026-10-27T23:00:00Z'))
    expect(w.minUtcDay).toBe('2026-10-14')
    expect(w.maxUtcDay).toBe('2026-10-27')
  })
})

describe('dailyWindow', () => {
  it('uses min_day and max_day of the daily mart when both are present', () => {
    expect(dailyWindow(meta(REAL_HOURLY, REAL_DAILY))).toEqual({ minDay: '2013-01-01', maxDay: '2025-12-31' })
  })

  it('falls back to the hourly window when the manifest has no daily range', () => {
    const fallback = dailyWindow(meta(REAL_HOURLY, {}, { daily: ['min_day', 'max_day'] }))
    expect(fallback).toEqual({ minDay: '2026-09-30', maxDay: '2026-10-07' })
  })

  it('falls back when only one of the two days is present, or the pair is not usable', () => {
    const hourlyWindow = { minDay: '2026-09-30', maxDay: '2026-10-07' }
    expect(dailyWindow(meta(REAL_HOURLY, { min_day: '2013-01-01' }, { daily: ['max_day'] }))).toEqual(hourlyWindow)
    expect(dailyWindow(meta(REAL_HOURLY, { max_day: '2025-12-31' }, { daily: ['min_day'] }))).toEqual(hourlyWindow)
    expect(dailyWindow(meta(REAL_HOURLY, { min_day: '2025-12-31', max_day: '2013-01-01' }))).toEqual(hourlyWindow)
    expect(dailyWindow(meta(REAL_HOURLY, { min_day: '1 Jan 2013', max_day: '2025-12-31' }))).toEqual(hourlyWindow)
  })

  it('does not depend on the hourly window when the daily range is present', () => {
    const a = dailyWindow(meta(REAL_HOURLY, REAL_DAILY))
    const b = dailyWindow(meta({ ...REAL_HOURLY, max_analysis_valid_at_utc: '2026-10-01T00:00:00Z' }, REAL_DAILY))
    expect(a).toEqual(b)
  })
})

describe('lastDailyDays', () => {
  it('counts back from the last day of the daily mart', () => {
    const range = lastDailyDays({ minDay: '2013-01-01', maxDay: '2025-12-31' })
    expect(range).toEqual({ from: '2025-10-03', to: '2025-12-31' })
    expect(DAILY_PANEL_DAYS).toBe(90)
  })

  it('takes a different number of days and stays inside the 366 day limit of the API', () => {
    expect(lastDailyDays({ minDay: '2013-01-01', maxDay: '2025-12-31' }, 7)).toEqual({ from: '2025-12-25', to: '2025-12-31' })
    expect(DAILY_PANEL_DAYS).toBeLessThanOrEqual(366)
  })

  it('does not reach before the first day of a short series', () => {
    expect(lastDailyDays({ minDay: '2026-10-14', maxDay: '2026-10-27' })).toEqual({ from: '2026-10-14', to: '2026-10-27' })
  })
})
