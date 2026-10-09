/* @refresh reload */
import '@fontsource-variable/atkinson-hyperlegible-next'
import { render } from 'solid-js/web'
import { QueryClientProvider } from '@tanstack/solid-query'
import './index.css'
import App from './App.tsx'
import { createQueryClient } from './queries/client'

const queryClient = createQueryClient()
const root = document.getElementById('root')

async function start() {
  if (import.meta.env.VITE_USE_MOCKS === '1') {
    const { startMocks } = await import('./mocks/browser')
    await startMocks()
  }
  render(
    () => (
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    ),
    root!,
  )
}

void start()
