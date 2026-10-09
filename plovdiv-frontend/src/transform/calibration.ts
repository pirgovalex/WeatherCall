import type { ModelCalibrationResponse } from '../api/types'

export interface CalibrationView {
  /** Bin edges and counts for every bin, including empty ones. */
  bins: { lo: number; hi: number; predicted: number | null; observed: number | null; n: number }[]
  /** Plot arrays: x is the mean predicted probability, with 0 and 1 added for the diagonal. */
  x: number[]
  observed: (number | null)[]
  diagonal: number[]
  /** n per plotted x (null at the added end points). */
  counts: (number | null)[]
}

export function toCalibrationView(res: ModelCalibrationResponse): CalibrationView {
  const bins = res.bin_lo.map((lo, i) => ({
    lo,
    hi: res.bin_hi[i]!,
    predicted: res.predicted_mean[i] ?? null,
    observed: res.observed_rate[i] ?? null,
    n: res.n[i]!,
  }))
  const filled = bins.filter((b) => b.n > 0 && b.predicted !== null && b.observed !== null)
  const x = [0, ...filled.map((b) => b.predicted as number), 1]
  return {
    bins,
    x,
    observed: [null, ...filled.map((b) => b.observed as number), null],
    diagonal: x.slice(),
    counts: [null, ...filled.map((b) => b.n), null],
  }
}

/** Bins with fewer test days than this are noisy; the chart and table say so. */
export const SPARSE_BIN_DAYS = 10
