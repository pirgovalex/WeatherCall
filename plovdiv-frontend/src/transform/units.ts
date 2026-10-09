const UNITS: Record<string, string> = {
  'ug/m3': 'µg/m³',
  degC: '°C',
  'm/s': 'm/s',
  mm: 'mm',
}

/** Display form of a unit string from the manifest. */
export function formatUnit(unit: string): string {
  return UNITS[unit] ?? unit
}

export function formatNumber(value: number, digits = 1): string {
  return new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(value)
}

export function formatPercent(fraction: number, digits = 0): string {
  return `${(fraction * 100).toFixed(digits)}%`
}

export interface ProbabilityText {
  /** Written form: "23%", "less than 1%" or "more than 99%". */
  text: string
  /** The same for a screen reader or an aria label: "23 percent", "less than 1 percent". */
  spoken: string
  /** The whole percent shown, 0 to 100. */
  percent: number
}

/**
 * A probability as a whole percent, without ever claiming certainty. A value that is not exactly 0
 * or 1 but would round to 0% or 100% is written "less than 1%" or "more than 99%": 0.0049 is not
 * "0%", which would read as impossible.
 */
export function formatProbability(probability: number): ProbabilityText {
  const percent = Math.round(probability * 100)
  if (probability > 0 && percent <= 0) return { text: 'less than 1%', spoken: 'less than 1 percent', percent: 0 }
  if (probability < 1 && percent >= 100) return { text: 'more than 99%', spoken: 'more than 99 percent', percent: 100 }
  return { text: `${percent}%`, spoken: `${percent} percent`, percent }
}

export function formatFixed(value: number, digits: number): string {
  return value.toFixed(digits)
}
