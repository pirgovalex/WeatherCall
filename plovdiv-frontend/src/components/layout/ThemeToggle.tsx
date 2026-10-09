import { For } from 'solid-js'
import { setTheme, themeChoice } from '../../theme/theme'
import type { ThemeChoice } from '../../theme/theme'

const CHOICES: { value: ThemeChoice; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

export default function ThemeToggle() {
  return (
    <div role="group" aria-label="Colour theme" class="inline-flex rounded-md bg-sunken p-0.5">
      <For each={CHOICES}>
        {(choice) => (
          <button
            type="button"
            aria-pressed={themeChoice() === choice.value}
            onClick={() => setTheme(choice.value)}
            class="rounded px-2.5 py-1 text-xs font-medium text-ink-2 hover:text-ink aria-pressed:bg-surface aria-pressed:text-ink aria-pressed:ring-1 aria-pressed:ring-rule"
          >
            {choice.label}
          </button>
        )}
      </For>
    </div>
  )
}
