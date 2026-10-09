import { QueryClient } from '@tanstack/solid-query'
import { shouldRetry } from './retry'

/** The application's query client: no retries for client errors or 503, two for network failures. */
export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: shouldRetry, refetchOnWindowFocus: false } } })
}
