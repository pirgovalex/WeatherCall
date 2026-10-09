import { createMemoryHistory, MemoryRouter } from '@solidjs/router'
import { QueryClient, QueryClientProvider } from '@tanstack/solid-query'
import { render } from '@solidjs/testing-library'
import { vi } from 'vitest'
import { AppRoutes } from '../App'
import AppShell from '../components/layout/AppShell'

export function testClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
}

/** Render the whole app (shared layout and routes) at a path, with a fresh query cache. */
export function renderApp(path = '/') {
  const history = createMemoryHistory()
  history.set({ value: path, scroll: false, replace: true })
  const client = testClient()
  return render(() => (
    <QueryClientProvider client={client}>
      <MemoryRouter history={history} root={AppShell}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>
  ))
}

/** Freeze only Date, so timers used by the query client keep working. */
export function setNow(iso: string): void {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(iso))
}
