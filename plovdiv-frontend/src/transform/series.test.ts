import { describe, expect, it } from 'vitest'
import type { SeriesResponse } from '../api/types'
import { describeSeries, latestReading, seriesStats, sourceLabel, toSeriesView } from './series'

const base: SeriesResponse = {
  variable: 'pm10',
  unit: 'ug/m3',
  location_id: 'plovdiv',
  resolution: 'hourly',
  source_type: 'analysis',
  timestamps: [0, 3600, 7200, 10800],
  values: [10, null, 12, null],
  warehouse: { run_id: 'r', schema_version: '1', generated_at_utc: '2026-10-07T14:05:12Z' },
  attribution: [],
}

describe('toSeriesView', () => {
  it('keeps epoch seconds and nulls untouched', () => {
    const view = toSeriesView(base)
    expect(view.x).toEqual([0, 3600, 7200, 10800])
    expect(view.y).toEqual([10, null, 12, null])
    expect(view.x.length).toBe(view.y.length)
  })

  it('fills hours that are absent from the payload with nulls so they draw as gaps', () => {
    const view = toSeriesView({ ...base, timestamps: [0, 3600, 14400], values: [1, 2, 3] })
    expect(view.x).toEqual([0, 3600, 7200, 10800, 14400])
    expect(view.y).toEqual([1, 2, null, null, 3])
  })

  it('does not invent rows for daily series', () => {
    const view = toSeriesView({ ...base, resolution: 'daily', timestamps: [0, 172800], values: [1, 2] })
    expect(view.x).toEqual([0, 172800])
  })
})

describe('readings and summaries', () => {
  it('finds the newest non-null value', () => {
    expect(latestReading(toSeriesView(base))).toEqual({ time: 7200, value: 12 })
  })

  it('returns null when every value is missing', () => {
    expect(latestReading(toSeriesView({ ...base, values: [null, null, null, null] }))).toBeNull()
  })

  it('counts missing values and finds the extremes', () => {
    const stats = seriesStats(toSeriesView(base))
    expect(stats.missing).toBe(2)
    expect(stats.min?.value).toBe(10)
    expect(stats.max?.value).toBe(12)
  })

  it('describes a series in words and mentions the gaps', () => {
    const text = describeSeries('PM10', toSeriesView(base))
    expect(text).toContain('PM10, hourly')
    expect(text).toContain('2 of 4 hours have no value')
  })

  it('never calls a modelled source a measurement', () => {
    expect(sourceLabel('analysis').long).toMatch(/not a station measurement/i)
    expect(sourceLabel(undefined).short).toBe('Source not stated')
  })
})
