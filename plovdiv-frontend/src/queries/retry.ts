import { ApiError } from '../api/client'

/** Client errors and 503 will not change on retry; network failures get two more attempts. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.isClientError || error.isUnavailable)) return false
  if (error instanceof ApiError && error.code === 'INVALID_RESPONSE') return false
  return failureCount < 2
}

export const MINUTE = 60_000
export const STALE_META = MINUTE
export const STALE_DATA = 30 * MINUTE
