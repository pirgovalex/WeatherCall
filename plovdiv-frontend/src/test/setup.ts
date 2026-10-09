import '@testing-library/jest-dom/vitest'
import { cleanup } from '@solidjs/testing-library'
import { afterEach, vi } from 'vitest'
import { server } from '../mocks/testServer'
import { setScenario } from '../mocks/scenario'

// Tests call a fixed origin; the handlers answer it through the fetch stub below.
vi.stubEnv('VITE_API_BASE_URL', 'http://api.test')

// uPlot needs a real canvas; component tests only check what surrounds the chart.
vi.mock('uplot', () => {
  class FakePlot {
    static tzDate = (d: Date) => d
    over = document.createElement('div')
    cursor = { idx: null as number | null, left: -1, top: -1 }
    series: unknown[] = []
    opts: unknown
    data: unknown
    constructor(opts: unknown, data: unknown, host: HTMLElement) {
      this.opts = opts
      this.data = data
      host.setAttribute('data-uplot', 'true')
    }
    destroy() {}
    setSize() {}
    setCursor() {}
    valToPos() {
      return 0
    }
  }
  return { default: FakePlot }
})

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => server.fetch(input, init))
afterEach(() => {
  cleanup()
  server.resetHandlers()
  setScenario('default')
  vi.useRealTimers()
})
