import { createContext, useContext } from 'solid-js'
import type { CreateQueryResult } from '@tanstack/solid-query'
import type { ApiError } from '../api/client'
import type { MetaResponse } from '../api/types'

export interface MetaContextValue {
  query: CreateQueryResult<MetaResponse, ApiError>
  /** The warehouse run id; data queries are keyed on it and wait until it is known. */
  runId: () => string | undefined
}

export const MetaContext = createContext<MetaContextValue>()

export function useMetaContext(): MetaContextValue {
  const value = useContext(MetaContext)
  if (!value) throw new Error('useMetaContext must be used inside MetaProvider')
  return value
}
