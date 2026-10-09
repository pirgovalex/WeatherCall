import { onMount, Show } from 'solid-js'
import type { JSX } from 'solid-js'
import { USE_MOCKS } from '../../config'
import { initTheme } from '../../theme/theme'
import Brand from './Brand'
import Footer from './Footer'
import MetaGate from './MetaGate'
import MetaProvider from './MetaProvider'
import Nav from './Nav'
import ThemeToggle from './ThemeToggle'

/** Shared layout: navigation, page content and the attribution footer. */
export default function AppShell(props: { children?: JSX.Element }) {
  onMount(initTheme)
  return (
    <MetaProvider>
      <a
        href="#main"
        class="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-on-primary"
      >
        Skip to content
      </a>
      <div class="min-h-screen lg:grid lg:grid-cols-[16rem_1fr]">
        <header class="border-b border-rule bg-surface px-4 py-3 lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col lg:justify-between lg:border-r lg:border-b-0 lg:px-5 lg:py-6">
          <div class="flex flex-col gap-3 lg:gap-8">
            <div class="flex items-center justify-between gap-3">
              <Brand />
              <div class="lg:hidden">
                <ThemeToggle />
              </div>
            </div>
            <Nav />
          </div>
          <div class="hidden flex-col gap-3 lg:flex">
            <Show when={USE_MOCKS}>
              <p class="rounded-md bg-info px-3 py-2 text-xs text-ink-2" data-testid="mock-badge">
                Mock data from the contract fixtures. Numbers are synthetic.
              </p>
            </Show>
            <ThemeToggle />
          </div>
        </header>
        <div class="min-w-0 px-4 py-8 sm:px-8 lg:px-12 lg:py-10">
          <div class="mx-auto max-w-5xl">
            <main id="main" tabindex="-1">
              <Show when={USE_MOCKS}>
                <p class="mb-6 rounded-md bg-info px-3 py-2 text-xs text-ink-2 lg:hidden">
                  Mock data from the contract fixtures. Numbers are synthetic.
                </p>
              </Show>
              <MetaGate>{props.children}</MetaGate>
            </main>
            <Footer />
          </div>
        </div>
      </div>
    </MetaProvider>
  )
}
