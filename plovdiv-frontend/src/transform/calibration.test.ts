import { describe, expect, it } from 'vitest'
import type { ModelCalibrationResponse } from '../api/types'
import { toCalibrationView } from './calibration'

const res = {
  model_id: 'm',
  bin_lo: [0, 0.5, 0.9],
  bin_hi: [0.5, 0.9, 1],
  predicted_mean: [0.2, 0.7, null],
  observed_rate: [0.25, 0.6, null],
  n: [100, 20, 0],
} as unknown as ModelCalibrationResponse

describe('toCalibrationView', () => {
  it('leaves empty bins out of the plot but keeps them for the table', () => {
    const view = toCalibrationView(res)
    expect(view.bins).toHaveLength(3)
    expect(view.x).toEqual([0, 0.2, 0.7, 1])
    expect(view.observed).toEqual([null, 0.25, 0.6, null])
    expect(view.diagonal).toEqual(view.x)
    expect(view.counts).toEqual([null, 100, 20, null])
  })
})
