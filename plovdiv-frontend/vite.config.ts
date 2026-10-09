/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import solid from 'vite-plugin-solid'
import tailwindcss from '@tailwindcss/vite'

// The browser sees a single origin: /api and /healthz are proxied to the
// backend during development. Override the target with DEV_API_TARGET.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.DEV_API_TARGET || 'http://127.0.0.1:8000'
  const proxy = {
    '/api': { target, changeOrigin: false },
    '/healthz': { target, changeOrigin: false },
  }

  return {
    // Hot module replacement is off under Vitest: solid-refresh passes a file:/// id
    // to Node's file APIs, which Windows rejects.
    plugins: [tailwindcss(), solid({ hot: !process.env.VITEST })],
    server: { proxy },
    preview: { proxy },
    test: {
      environment: 'jsdom',
      globals: false,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      server: { deps: { inline: [/solid-js/, /@solidjs/, /@tanstack\/solid-query/] } },
    },
  }
})
