import { describe, expect, it } from 'vitest'
import {
  addDays,
  dateKeyToEpoch,
  daysBetween,
  formatDateKey,
  formatPlovdivDateTime,
  isoUtc,
  plovdivDateKey,
  plovdivDayRangeUtc,
  plovdivDayStartUtc,
  plovdivOffsetMinutes,
  plovdivSpanToApiRange,
  todayPlovdiv,
} from './time'

const utc = (iso: string) => Date.parse(iso) / 1000

describe('Europe/Sofia days across the autumn clock change', () => {
  it('maps Plovdiv days to UTC ranges as in ADR-001', () => {
    expect(isoUtc(plovdivDayStartUtc('2026-10-24'))).toBe('2026-10-23T21:00:00Z')
    expect(isoUtc(plovdivDayStartUtc('2026-10-25'))).toBe('2026-10-24T21:00:00Z')
    expect(isoUtc(plovdivDayStartUtc('2026-10-26'))).toBe('2026-10-25T22:00:00Z')
  })

  it('counts 24, 25 and 24 hours', () => {
    expect(plovdivDayRangeUtc('2026-10-24').hours).toBe(24)
    expect(plovdivDayRangeUtc('2026-10-25').hours).toBe(25)
    expect(plovdivDayRangeUtc('2026-10-26').hours).toBe(24)
  })

  it('counts 23 hours on the spring change', () => {
    expect(plovdivDayRangeUtc('2027-03-28').hours).toBe(23)
  })

  it('uses +3 before the change and +2 after it', () => {
    expect(plovdivOffsetMinutes(Date.parse('2026-10-24T12:00:00Z'))).toBe(180)
    expect(plovdivOffsetMinutes(Date.parse('2026-10-26T12:00:00Z'))).toBe(120)
  })

  it('turns a span of days into an inclusive hourly API range', () => {
    expect(plovdivSpanToApiRange('2026-10-24', '2026-10-26')).toEqual({
      from: '2026-10-23T21:00:00Z',
      to: '2026-10-26T21:00:00Z',
    })
    // The 25-hour day alone ends at its 25th hour.
    expect(plovdivSpanToApiRange('2026-10-25', '2026-10-25')).toEqual({
      from: '2026-10-24T21:00:00Z',
      to: '2026-10-25T21:00:00Z',
    })
  })

  it('shows the repeated local hour as the same clock time one hour apart', () => {
    // 00:00Z and 01:00Z on 2026-10-25 straddle the change at 01:00Z (04:00 EEST becomes 03:00 EET).
    expect(formatPlovdivDateTime(utc('2026-10-25T00:00:00Z'))).toContain('03:00')
    expect(formatPlovdivDateTime(utc('2026-10-25T01:00:00Z'))).toContain('03:00')
    expect(formatPlovdivDateTime(utc('2026-10-25T02:00:00Z'))).toContain('04:00')
  })
})

describe('calendar helpers', () => {
  it('finds the Plovdiv date of an instant', () => {
    expect(plovdivDateKey(Date.parse('2026-10-27T23:00:00Z'))).toBe('2026-10-28')
    expect(plovdivDateKey(Date.parse('2026-10-27T21:30:00Z'))).toBe('2026-10-27')
  })

  it('returns today in Plovdiv for a given clock', () => {
    expect(todayPlovdiv(Date.parse('2026-12-31T22:30:00Z'))).toBe('2027-01-01')
  })

  it('adds days and counts the difference without time zones', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
    expect(daysBetween('2026-10-25', '2026-10-28')).toBe(3)
    expect(daysBetween('2026-10-28', '2026-10-25')).toBe(-3)
  })

  it('treats a plain date as UTC midnight', () => {
    expect(dateKeyToEpoch('2026-10-14')).toBe(utc('2026-10-14T00:00:00Z'))
    expect(formatDateKey('2026-10-14')).toBe('14 Oct 2026')
  })
})
