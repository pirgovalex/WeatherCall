/**
 * Time helpers. Everything is built on Intl; there is no date library and no
 * dependency on the browser's own time zone. The warehouse works in UTC and the
 * dashboard shows Europe/Sofia.
 */
export const PLOVDIV = 'Europe/Sofia'
const LOCALE = 'en-GB'

const HOUR_S = 3600
const DAY_MS = 86_400_000

const partsFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: PLOVDIV,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

interface WallClock {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  second: number
}

function plovdivWallClock(epochMs: number): WallClock {
  const out: Record<string, number> = {}
  for (const part of partsFormatter.formatToParts(new Date(epochMs))) {
    if (part.type !== 'literal') out[part.type] = Number(part.value)
  }
  return out as unknown as WallClock
}

/** Offset of Europe/Sofia from UTC at the given instant, in minutes (120 or 180). */
export function plovdivOffsetMinutes(epochMs: number): number {
  const w = plovdivWallClock(epochMs)
  const asUtc = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second)
  return Math.round((asUtc - Math.floor(epochMs / 1000) * 1000) / 60_000)
}

const pad = (n: number, width = 2) => String(n).padStart(width, '0')

/** Calendar date (YYYY-MM-DD) in Europe/Sofia for an instant. */
export function plovdivDateKey(epochMs: number): string {
  const w = plovdivWallClock(epochMs)
  return `${pad(w.year, 4)}-${pad(w.month)}-${pad(w.day)}`
}

/** Today's date in Europe/Sofia. */
export function todayPlovdiv(nowMs: number = Date.now()): string {
  return plovdivDateKey(nowMs)
}

export function parseDateKey(key: string): { year: number; month: number; day: number } {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(key)
  if (!match) throw new RangeError(`Not a date: ${key}`)
  return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) }
}

/** Epoch seconds at 00:00 UTC of a plain calendar date. */
export function dateKeyToEpoch(key: string): number {
  const { year, month, day } = parseDateKey(key)
  return Date.UTC(year, month - 1, day) / 1000
}

export function addDays(key: string, days: number): string {
  const ms = dateKeyToEpoch(key) * 1000 + days * DAY_MS
  const d = new Date(ms)
  return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

/** Whole calendar days from `a` to `b` (positive when b is later). */
export function daysBetween(a: string, b: string): number {
  return Math.round((dateKeyToEpoch(b) - dateKeyToEpoch(a)) / 86_400)
}

/**
 * Epoch seconds (UTC) of the first instant of a Plovdiv calendar day. The clock
 * changes in Plovdiv happen in the early morning, never at midnight, so the offset
 * in force at midnight is the one in force at the matching UTC midnight, or one
 * hour away from it; the loop checks the candidates instead of assuming.
 */
export function plovdivDayStartUtc(key: string): number {
  const utcMidnightMs = dateKeyToEpoch(key) * 1000
  const offsets = [
    plovdivOffsetMinutes(utcMidnightMs),
    plovdivOffsetMinutes(utcMidnightMs - 6 * HOUR_S * 1000),
    plovdivOffsetMinutes(utcMidnightMs + 6 * HOUR_S * 1000),
  ]
  for (const offset of offsets) {
    const candidate = utcMidnightMs - offset * 60_000
    const w = plovdivWallClock(candidate)
    if (plovdivDateKey(candidate) === key && w.hour === 0 && w.minute === 0) return candidate / 1000
  }
  throw new RangeError(`Could not resolve the start of the Plovdiv day ${key}`)
}

/** Range of one Plovdiv day in UTC: `to` is exclusive. */
export function plovdivDayRangeUtc(key: string): { from: number; to: number; hours: number } {
  const from = plovdivDayStartUtc(key)
  const to = plovdivDayStartUtc(addDays(key, 1))
  return { from, to, hours: (to - from) / HOUR_S }
}

/** Inclusive hourly range for a span of Plovdiv days, as the API expects it. */
export function plovdivSpanToApiRange(fromKey: string, toKey: string): { from: string; to: string } {
  const start = plovdivDayStartUtc(fromKey)
  const endExclusive = plovdivDayStartUtc(addDays(toKey, 1))
  return { from: isoUtc(start), to: isoUtc(endExclusive - HOUR_S) }
}

export function isoUtc(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toISOString().replace('.000Z', 'Z')
}

const cache = new Map<string, Intl.DateTimeFormat>()
function formatter(key: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  let f = cache.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat(LOCALE, options)
    cache.set(key, f)
  }
  return f
}

/** "21 Oct, 14:00" in Europe/Sofia. */
export function formatPlovdivDateTime(epochSeconds: number): string {
  return formatter('plovdiv-dt', {
    timeZone: PLOVDIV,
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(epochSeconds * 1000))
}

/** "21 Oct 2026, 14:00" in Europe/Sofia. */
export function formatPlovdivDateTimeYear(epochSeconds: number): string {
  return formatter('plovdiv-dty', {
    timeZone: PLOVDIV,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(epochSeconds * 1000))
}

/** Parse an ISO timestamp (with Z) to epoch seconds. */
export function isoToEpoch(iso: string): number {
  return Math.floor(Date.parse(iso) / 1000)
}

/** Plain calendar date ("14 Oct 2026"), independent of any time zone. */
export function formatDateKey(key: string): string {
  return formatter('date-utc', { timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric' }).format(
    new Date(dateKeyToEpoch(key) * 1000),
  )
}

/** Plain calendar date with weekday ("Tue 14 Oct"). */
export function formatDateKeyShort(key: string): string {
  return formatter('date-utc-short', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' }).format(
    new Date(dateKeyToEpoch(key) * 1000),
  )
}

/** "Oct 2026" for a month key (first day of the month). */
export function formatMonthKey(key: string): string {
  return formatter('month-utc', { timeZone: 'UTC', month: 'short', year: 'numeric' }).format(
    new Date(dateKeyToEpoch(key) * 1000),
  )
}

/** Readable form of a UTC offset such as "UTC+3". */
export function formatPlovdivOffset(epochSeconds: number): string {
  const minutes = plovdivOffsetMinutes(epochSeconds * 1000)
  return `UTC+${minutes / 60}`
}
