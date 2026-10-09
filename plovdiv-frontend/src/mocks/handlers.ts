import { delay, http, HttpResponse } from 'msw'
import { metaBody, MOCK_ROUTES, resolveMock } from './api'
import { getScenario } from './scenario'

/** `latency` is the base delay in ms; the slow scenario multiplies it. */
export function createHandlers(latency = 0) {
  const wait = async () => {
    const ms = getScenario() === 'slow' ? Math.max(latency, 1) * 20 : latency
    if (ms > 0) await delay(ms)
  }

  const dataHandlers = Object.keys(MOCK_ROUTES).map((path) =>
    http.get(`*${path}`, async ({ request }) => {
      await wait()
      const url = new URL(request.url)
      const result = resolveMock(path, url.searchParams, getScenario())
      return HttpResponse.json(result?.body ?? null, { status: result?.status ?? 404 })
    }),
  )

  return [
    http.get('*/api/v1/meta', async () => {
      await wait()
      return HttpResponse.json(metaBody(getScenario()) as Record<string, unknown>)
    }),
    http.get('*/healthz', () => HttpResponse.json({ status: 'ok' })),
    ...dataHandlers,
  ]
}
