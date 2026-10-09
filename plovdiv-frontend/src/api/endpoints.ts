import { apiGet } from './client'
import { assertAscending, assertEqualLengths } from './validate'
import type {
  AqRiskResponse,
  AqTrendResponse,
  DailyAqResponse,
  DailyParams,
  MetaResponse,
  ModelCalibrationResponse,
  ModelCardResponse,
  ModelParams,
  RiskParams,
  SeriesParams,
  SeriesResponse,
  TrendParams,
} from './types'

export const getMeta = (signal?: AbortSignal) => apiGet<MetaResponse>('/api/v1/meta', {}, signal)

export async function getSeries(params: SeriesParams, signal?: AbortSignal): Promise<SeriesResponse> {
  const body = await apiGet<SeriesResponse>('/api/v1/series', params, signal)
  assertEqualLengths('series', { timestamps: body.timestamps, values: body.values })
  assertAscending('series', body.timestamps)
  return body
}

export async function getAqDaily(params: DailyParams, signal?: AbortSignal): Promise<DailyAqResponse> {
  const body = await apiGet<DailyAqResponse>('/api/v1/aq/daily', params, signal)
  assertEqualLengths('daily', {
    days: body.days,
    mean: body.mean,
    valid_hours: body.valid_hours,
    is_valid_day: body.is_valid_day,
  })
  return body
}

export async function getAqTrend(params: TrendParams, signal?: AbortSignal): Promise<AqTrendResponse> {
  const body = await apiGet<AqTrendResponse>('/api/v1/aq/trend', params, signal)
  assertEqualLengths('trend', {
    months: body.months,
    observed_mean: body.observed_mean,
    weather_adjusted_mean: body.weather_adjusted_mean,
    adjusted_lo: body.adjusted_lo,
    adjusted_hi: body.adjusted_hi,
    n_days: body.n_days,
  })
  assertAscending('trend', body.months)
  return body
}

export async function getAqRisk(params: RiskParams, signal?: AbortSignal): Promise<AqRiskResponse> {
  const body = await apiGet<AqRiskResponse>('/api/v1/aq/risk', params, signal)
  assertEqualLengths('risk', {
    target_dates: body.target_dates,
    issued_at: body.issued_at,
    probability: body.probability,
    label_id: body.label_id,
    split: body.split,
    actual_label: body.actual_label,
    model_id: body.model_id,
  })
  if (body.drivers) {
    assertEqualLengths('risk drivers', {
      feature: body.drivers.feature,
      contribution: body.drivers.contribution,
      rank: body.drivers.rank,
    })
  }
  return body
}

export const getModelCard = (params: ModelParams, signal?: AbortSignal) =>
  apiGet<ModelCardResponse>('/api/v1/model/card', params, signal)

export async function getModelCalibration(
  params: ModelParams,
  signal?: AbortSignal,
): Promise<ModelCalibrationResponse> {
  const body = await apiGet<ModelCalibrationResponse>('/api/v1/model/calibration', params, signal)
  assertEqualLengths('calibration', {
    bin_lo: body.bin_lo,
    bin_hi: body.bin_hi,
    predicted_mean: body.predicted_mean,
    observed_rate: body.observed_rate,
    n: body.n,
  })
  return body
}
