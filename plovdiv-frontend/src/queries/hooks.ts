import { createQuery, keepPreviousData } from '@tanstack/solid-query'
import type { Accessor } from 'solid-js'
import type { ApiError } from '../api/client'
import {
  getAqDaily,
  getAqRisk,
  getAqTrend,
  getMeta,
  getModelCalibration,
  getModelCard,
  getSeries,
} from '../api/endpoints'
import type { AqRiskResponse, AqTrendResponse, DailyAqResponse, ModelCalibrationResponse, ModelCardResponse, Pollutant, SeriesResponse } from '../api/types'
import { LOCATION_ID, SOURCE_TYPE } from '../config'
import { useMetaContext } from './metaContext'
import { STALE_DATA, STALE_META } from './retry'

export function metaQueryOptions() {
  return {
    queryKey: ['meta'] as const,
    queryFn: ({ signal }: { signal: AbortSignal }) => getMeta(signal),
    staleTime: STALE_META,
  }
}

export interface SeriesArgs {
  variable: string
  from: string
  to: string
}

export function useSeries(args: Accessor<SeriesArgs | null>) {
  const { runId } = useMetaContext()
  return createQuery<SeriesResponse, ApiError>(() => {
    const a = args()
    const run = runId()
    return {
      queryKey: ['series', run, a?.variable, LOCATION_ID, SOURCE_TYPE, a?.from, a?.to],
      enabled: a !== null && run !== undefined,
      queryFn: ({ signal }) =>
        getSeries(
          {
            variable: a!.variable,
            location: LOCATION_ID,
            from: a!.from,
            to: a!.to,
            resolution: 'hourly',
            source_type: SOURCE_TYPE,
          },
          signal,
        ),
      staleTime: STALE_DATA,
      placeholderData: keepPreviousData,
    }
  })
}

export interface DailyArgs {
  pollutant: Pollutant
  from: string
  to: string
}

export function useDaily(args: Accessor<DailyArgs | null>) {
  const { runId } = useMetaContext()
  return createQuery<DailyAqResponse, ApiError>(() => {
    const a = args()
    const run = runId()
    return {
      queryKey: ['daily', run, a?.pollutant, LOCATION_ID, a?.from, a?.to],
      enabled: a !== null && run !== undefined,
      queryFn: ({ signal }) => getAqDaily({ pollutant: a!.pollutant, location: LOCATION_ID, from: a!.from, to: a!.to }, signal),
      staleTime: STALE_DATA,
      placeholderData: keepPreviousData,
    }
  })
}

export interface TrendArgs {
  stationId: string
  from: string
  to: string
}

export function useTrend(args: Accessor<TrendArgs | null>) {
  const { runId } = useMetaContext()
  return createQuery<AqTrendResponse, ApiError>(() => {
    const a = args()
    const run = runId()
    return {
      queryKey: ['trend', run, a?.stationId, 'pm10', a?.from, a?.to],
      enabled: a !== null && run !== undefined,
      queryFn: ({ signal }) =>
        getAqTrend({ station_id: a!.stationId, pollutant: 'pm10', from: a!.from, to: a!.to }, signal),
      staleTime: STALE_DATA,
      placeholderData: keepPreviousData,
    }
  })
}

export function useLatestRisk(stationId: Accessor<string | undefined>) {
  const { runId } = useMetaContext()
  return createQuery<AqRiskResponse, ApiError>(() => {
    const id = stationId()
    const run = runId()
    return {
      queryKey: ['risk-latest', run, id],
      enabled: id !== undefined && run !== undefined,
      queryFn: ({ signal }) => getAqRisk({ station_id: id!, latest: true }, signal),
      staleTime: STALE_DATA,
      placeholderData: keepPreviousData,
    }
  })
}

export function useModelCard(modelId: Accessor<string | undefined>) {
  const { runId } = useMetaContext()
  return createQuery<ModelCardResponse, ApiError>(() => {
    const id = modelId()
    const run = runId()
    return {
      queryKey: ['model-card', run, id],
      enabled: id !== undefined && run !== undefined,
      queryFn: ({ signal }) => getModelCard({ model_id: id! }, signal),
      staleTime: STALE_DATA,
    }
  })
}

export function useCalibration(modelId: Accessor<string | undefined>) {
  const { runId } = useMetaContext()
  return createQuery<ModelCalibrationResponse, ApiError>(() => {
    const id = modelId()
    const run = runId()
    return {
      queryKey: ['model-calibration', run, id],
      enabled: id !== undefined && run !== undefined,
      queryFn: ({ signal }) => getModelCalibration({ model_id: id! }, signal),
      staleTime: STALE_DATA,
    }
  })
}
