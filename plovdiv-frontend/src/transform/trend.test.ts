import { describe, expect, it } from 'vitest'
import { describeLatestMonth, isLowCoverage } from './trend'
import type { TrendView } from './trend'

const view = (obs: number | null, adj: number | null, lo: number | null, hi: number | null): TrendView => ({
  unit: 'ug/m3',
  months: ['2026-09-01', '2026-10-01'],
  x: [0, 1],
  observed: [20, obs],
  adjusted: [20, adj],
  lo: [18, lo],
  hi: [22, hi],
  nDays: [30, 13],
})

describe('describeLatestMonth', () => {
  it('says weather explains little when the observed mean is inside the band', () => {
    expect(describeLatestMonth(view(30, 30.5, 28, 33))).toMatch(/inside the uncertainty band/)
  })
  it('says weather raised the average when observed is above the band', () => {
    expect(describeLatestMonth(view(35, 30, 28, 33))).toMatch(/raised the average/)
  })
  it('says weather lowered the average when observed is below the band', () => {
    expect(describeLatestMonth(view(25, 30, 28, 33))).toMatch(/lowered the average/)
  })
  it('skips months without values and falls back to the earlier month', () => {
    expect(describeLatestMonth(view(null, null, null, null))).toMatch(/^Sep/)
  })
  it('flags low coverage only for months with some but few valid days', () => {
    expect(isLowCoverage(0)).toBe(false)
    expect(isLowCoverage(9)).toBe(true)
    expect(isLowCoverage(28)).toBe(false)
  })
})
