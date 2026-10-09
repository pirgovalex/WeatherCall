import { setupWorker } from 'msw/browser'
import { createHandlers } from './handlers'
import { parseScenario, setScenario } from './scenario'

/** Start the service worker that answers /api requests with fixture data. */
export async function startMocks(): Promise<void> {
  setScenario(parseScenario(new URLSearchParams(window.location.search).get('mock')))
  const worker = setupWorker(...createHandlers(150))
  await worker.start({
    onUnhandledFrame: 'bypass',
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
  })
}
