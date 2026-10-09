import { getResponse } from 'msw'
import type { RequestHandler } from 'msw'
import { createHandlers } from './handlers'

/**
 * Request interception for tests. msw/node cannot be loaded next to the Solid
 * plugin, which adds the "browser" export condition that msw/node excludes, so the
 * same handlers are resolved with msw's core `getResponse` and installed as
 * `fetch`.
 */
export function createTestServer() {
  const initial = createHandlers(0)
  let current: RequestHandler[] = [...initial]

  return {
    /** Put handlers in front of the defaults for one test. */
    use(...handlers: RequestHandler[]) {
      current = [...handlers, ...current]
    },
    resetHandlers() {
      current = [...initial]
    },
    async fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
      const request = new Request(input, init)
      const response = await getResponse(current, request)
      if (!response) throw new Error(`No mock handler for ${request.method} ${request.url}`)
      if (response.type === 'error') throw new TypeError('Failed to fetch')
      return response
    },
  }
}

export const server = createTestServer()
