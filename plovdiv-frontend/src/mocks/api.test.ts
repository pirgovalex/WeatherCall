import { describe, expect, it } from 'vitest'
import { resolveMock } from './api'

const get = (path: string, query: Record<string, string>, scenario: Parameters<typeof resolveMock>[2] = 'default') =>
  resolveMock(path, new URLSearchParams(query), scenario)!

describe('mock API follows the contract rules', () => {
  it('returns parallel arrays of equal length for the risk range', () => {
    const { status, body } = get('/api/v1/aq/risk', { station_id: 'BG0078A', from: '2026-10-01', to: '2026-10-28' })
    const b = body as Record<string, unknown[]>
    expect(status).toBe(200)
    const lengths = ['target_dates', 'issued_at', 'probability', 'label_id', 'split', 'actual_label', 'model_id'].map((k) => b[k]!.length)
    expect(new Set(lengths).size).toBe(1)
    expect(b.drivers).toBeUndefined()
  })

  it('returns exactly one row and its drivers for latest=true', () => {
    const { body } = get('/api/v1/aq/risk', { station_id: 'BG0051A', latest: 'true' })
    const b = body as { target_dates: string[]; drivers: { feature: string[]; rank: number[] } }
    expect(b.target_dates).toHaveLength(1)
    expect(b.drivers.feature.length).toBeGreaterThan(0)
    expect(b.drivers.rank[0]).toBe(1)
  })

  it('keeps null for months with no valid days', () => {
    const { body } = get('/api/v1/aq/trend', { station_id: 'BG0078A', from: '2025-09-01', to: '2026-10-31' })
    const b = body as { months: string[]; observed_mean: (number | null)[]; n_days: number[] }
    const i = b.months.indexOf('2026-03-01')
    expect(b.observed_mean[i]).toBeNull()
    expect(b.n_days[i]).toBe(0)
  })

  it('answers 404, 422 and 503 with the error envelope', () => {
    expect(get('/api/v1/aq/trend', { station_id: 'X', from: '2026-01-01', to: '2026-02-01' }).status).toBe(404)
    expect(get('/api/v1/aq/trend', { station_id: 'BG0051A' }).status).toBe(422)
    const down = get('/api/v1/model/card', { model_id: 'pm10_exceed_gbm_v1' }, 'unavailable')
    expect(down.status).toBe(503)
    expect(down.body).toEqual({ error: { code: 'SERVICE_UNAVAILABLE', message: expect.stringContaining('mart_model_card') } })
  })

  it('treats `to` as inclusive for hourly series', () => {
    const { body } = get('/api/v1/series', {
      variable: 'pm10', location: 'plovdiv', resolution: 'hourly', from: '2026-10-14T00:00:00Z', to: '2026-10-14T23:00:00Z',
    })
    expect((body as { timestamps: number[] }).timestamps).toHaveLength(24)
  })

  it('has the 25-hour Plovdiv day in the fixture window', () => {
    const { body } = get('/api/v1/series', {
      variable: 'pm10', location: 'plovdiv', resolution: 'hourly', from: '2026-10-24T21:00:00Z', to: '2026-10-25T21:00:00Z',
    })
    expect((body as { timestamps: number[] }).timestamps).toHaveLength(25)
  })
})
