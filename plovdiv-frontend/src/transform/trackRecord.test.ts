import { describe, expect, it } from 'vitest'
import type { AqRiskResponse } from '../api/types'
import { recentCalls, summarize, toCalls } from './trackRecord'

function response(probability: number[], actual: (number | null)[]): AqRiskResponse {
  const n = probability.length
  return {
    station_id: 'BG0051A',
    target_dates: Array.from({ length: n }, (_, i) => `2026-10-${String(i + 1).padStart(2, '0')}`),
    issued_at: Array.from({ length: n }, (_, i) => `2026-10-${String(i + 1).padStart(2, '0')}T06:00:00Z`),
    probability,
    label_id: Array(n).fill('label'),
    split: Array(n).fill('live'),
    actual_label: actual,
    model_id: Array(n).fill('m'),
    warehouse: { run_id: 'r', schema_version: '1', generated_at_utc: '2026-10-10T00:00:00Z' },
    attribution: [],
  } as AqRiskResponse
}

describe('track record', () => {
  it('classifies each call against the threshold and the outcome', () => {
    const calls = toCalls(response([0.9, 0.2, 0.7, 0.1, 0.6], [1, 1, 0, 0, null]), 0.54)
    expect(calls.map((c) => c.outcome)).toEqual(['hit', 'miss', 'false-alarm', 'correct-rejection', 'waiting'])
  })

  it('a probability exactly at the threshold is a call for a bad day', () => {
    expect(toCalls(response([0.54], [1]), 0.54)[0]!.calledBad).toBe(true)
  })

  it('summarizes only decided days', () => {
    const summary = summarize(toCalls(response([0.9, 0.2, 0.7, 0.1, 0.6], [1, 1, 0, 0, null]), 0.54))
    expect(summary).toMatchObject({ decided: 4, waiting: 1, correct: 2, badDays: 2, badDaysFlagged: 1, falseAlarms: 1 })
    expect(summary.brier).toBeCloseTo((0.01 + 0.64 + 0.49 + 0.01) / 4, 10)
    expect(summary.baseRate).toBe(0.5)
  })

  it('has no scores before anything is decided', () => {
    const summary = summarize(toCalls(response([0.4], [null]), 0.54))
    expect(summary.decided).toBe(0)
    expect(summary.brier).toBeNull()
    expect(summary.baseRate).toBeNull()
  })

  it('lists the most recent calls first', () => {
    const calls = toCalls(response([0.1, 0.2, 0.3], [0, 0, 0]), 0.54)
    expect(recentCalls(calls, 2).map((c) => c.targetDate)).toEqual(['2026-10-03', '2026-10-02'])
  })
})
