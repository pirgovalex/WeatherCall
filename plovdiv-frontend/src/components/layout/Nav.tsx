import { A } from '@solidjs/router'
import { For } from 'solid-js'

export const NAV_ITEMS = [
  { href: '/', label: 'Now', end: true },
  { href: '/history', label: 'History', end: false },
  { href: '/forecasts', label: 'Forecasts', end: false },
  { href: '/method', label: 'Method', end: false },
]

export default function Nav() {
  return (
    <nav aria-label="Main">
      <ul class="flex gap-1 lg:flex-col">
        <For each={NAV_ITEMS}>
          {(item) => (
            <li class="flex-1 lg:flex-none">
              <A
                href={item.href}
                end={item.end}
                class="block rounded-md px-3 py-2 text-center text-sm font-medium text-ink-2 hover:bg-sunken hover:text-ink aria-[current=page]:bg-primary-soft aria-[current=page]:font-semibold aria-[current=page]:text-ink lg:text-left lg:text-base"
              >
                {item.label}
              </A>
            </li>
          )}
        </For>
      </ul>
    </nav>
  )
}
