import type { AqRiskResponse } from '../api/types'

export type Outcome = 'hit' | 'miss' | 'false-alarm' | 'correct-rejection' | 'waiting'

export interface Call {
  targetDate: string
  probability: number
  calledBad: boolean
  /** 1 bad day, 0 not bad, null while the day is not complete or had too few valid hours. */
  actual: number | null
  outcome: Outcome
}

export interface TrackSummary {
  /** Rows whose outcome is known. */
  decided: number
  waiting: number
  correct: number
  badDays: number
  badDaysFlagged: number
  falseAlarms: number
  /** Mean squared difference between probability and outcome; null when nothing is decided. */
  brier: number | null
  /** Share of decided days that were bad. */
  baseRate: number | null
}

/** One call per row: whether the model called a bad day (probability at or above the threshold) and what happened. */
export function toCalls(res: AqRiskResponse | undefined, threshold: number): Call[] {
  if (!res) return []
  return res.target_dates.map((targetDate, i) => {
    const probability = res.probability[i]!
    const actual = res.actual_label[i] ?? null
    const calledBad = probability >= threshold
    let outcome: Outcome = 'waiting'
    if (actual === 1) outcome = calledBad ? 'hit' : 'miss'
    else if (actual === 0) outcome = calledBad ? 'false-alarm' : 'correct-rejection'
    return { targetDate, probability, calledBad, actual, outcome }
  })
}

export function summarize(calls: Call[]): TrackSummary {
  const decided = calls.filter((c) => c.actual !== null)
  const count = (o: Outcome) => decided.filter((c) => c.outcome === o).length
  const hits = count('hit')
  const misses = count('miss')
  const brier =
    decided.length > 0 ? decided.reduce((sum, c) => sum + (c.probability - (c.actual as number)) ** 2, 0) / decided.length : null
  return {
    decided: decided.length,
    waiting: calls.length - decided.length,
    correct: hits + count('correct-rejection'),
    badDays: hits + misses,
    badDaysFlagged: hits,
    falseAlarms: count('false-alarm'),
    brier,
    baseRate: decided.length > 0 ? (hits + misses) / decided.length : null,
  }
}

export const OUTCOME_LABELS: Record<Outcome, string> = {
  hit: 'Bad day, called',
  miss: 'Bad day, missed',
  'false-alarm': 'False alarm',
  'correct-rejection': 'Not bad, as called',
  waiting: 'Waiting for the station data',
}

/** The most recent calls first. */
export function recentCalls(calls: Call[], n: number): Call[] {
  return [...calls].sort((a, b) => b.targetDate.localeCompare(a.targetDate)).slice(0, n)
}
