import type { ErrorEnvelope } from './types'

/**
 * Error raised for every failed request. `code` is the code from the API error
 * envelope when there is one, otherwise NETWORK (no response), INVALID_RESPONSE
 * (a response that does not match the contract) or UNKNOWN (a failure status
 * without an envelope).
 */
export class ApiError extends Error {
  readonly status: number | null
  readonly code: string

  constructor(status: number | null, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }

  /** True when the request itself was wrong or the resource does not exist. */
  get isClientError(): boolean {
    return this.status !== null && this.status >= 400 && this.status < 500
  }

  /** True for 503: the warehouse run does not publish the data this request needs. */
  get isUnavailable(): boolean {
    return this.status === 503
  }
}

export type QueryValue = string | number | boolean | undefined | null
export type QueryParams = Record<string, QueryValue>

export function apiBase(): string {
  return (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''
}

/** Build a query string from defined values only, in a stable key order. */
export function buildQuery(params: QueryParams): string {
  const search = new URLSearchParams()
  for (const key of Object.keys(params).sort()) {
    const value = params[key]
    if (value === undefined || value === null) continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

function isEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== 'object' || value === null) return false
  const error = (value as { error?: unknown }).error
  if (typeof error !== 'object' || error === null) return false
  const { code, message } = error as { code?: unknown; message?: unknown }
  return typeof code === 'string' && typeof message === 'string'
}

export async function apiGet<T>(path: string, params: QueryParams = {}, signal?: AbortSignal): Promise<T> {
  const url = `${apiBase()}${path}${buildQuery(params)}`

  let response: Response
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' }, signal })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(null, 'NETWORK', 'The API could not be reached.')
  }

  let body: unknown
  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (!response.ok) {
    if (isEnvelope(body)) throw new ApiError(response.status, body.error.code, body.error.message)
    throw new ApiError(response.status, 'UNKNOWN', `The API answered with status ${response.status}.`)
  }
  if (body === null) throw new ApiError(response.status, 'INVALID_RESPONSE', 'The API response was not valid JSON.')
  return body as T
}
