import { For } from 'solid-js'

export interface SegmentOption<T extends string> {
  value: T
  label: string
  hint?: string
}

interface SegmentedProps<T extends string> {
  label: string
  options: SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
}

/** A row of toggle buttons for choosing one value. Each button is a real button in the tab order. */
export default function Segmented<T extends string>(props: SegmentedProps<T>) {
  return (
    <div role="group" aria-label={props.label} class="inline-flex flex-wrap gap-1 rounded-md bg-sunken p-1">
      <For each={props.options}>
        {(option) => (
          <button
            type="button"
            aria-pressed={props.value === option.value}
            title={option.hint}
            onClick={() => props.onChange(option.value)}
            class="rounded px-3 py-1.5 text-sm font-medium text-ink-2 hover:text-ink aria-pressed:bg-surface aria-pressed:text-ink aria-pressed:shadow-sm aria-pressed:ring-1 aria-pressed:ring-rule"
          >
            {option.label}
          </button>
        )}
      </For>
    </div>
  )
}
