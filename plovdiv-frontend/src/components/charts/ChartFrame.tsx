import { For, Show } from 'solid-js'
import type { JSX } from 'solid-js'
import DataTable from '../ui/DataTable'
import type { ColorToken, LegendItem } from './spec'

const COLOR_CLASS: Record<ColorToken, string> = {
  series1: 'text-s1',
  series2: 'text-s2',
  muted: 'text-ink-3',
  ink: 'text-ink',
}

function Swatch(props: { item: LegendItem }) {
  return (
    <svg viewBox="0 0 24 12" width="24" height="12" aria-hidden="true" class={`shrink-0 ${COLOR_CLASS[props.item.color]}`}>
      {props.item.shape === 'band' ? <rect x="1" y="1" width="22" height="10" rx="2" fill="currentColor" opacity="0.25" /> : null}
      {props.item.shape === 'line' ? <line x1="1" y1="6" x2="23" y2="6" stroke="currentColor" stroke-width="2" stroke-linecap="round" /> : null}
      {props.item.shape === 'dashed' ? (
        <line x1="1" y1="6" x2="23" y2="6" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 3" />
      ) : null}
      {props.item.shape === 'dots' ? <circle cx="12" cy="6" r="4.5" fill="currentColor" /> : null}
      {props.item.shape === 'hollow' ? (
        <circle cx="12" cy="6" r="4" fill="none" stroke="currentColor" stroke-width="2" />
      ) : null}
    </svg>
  )
}

export function Legend(props: { items: LegendItem[] }) {
  return (
    <ul class="flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-2" aria-label="Chart legend">
      <For each={props.items}>
        {(item) => (
          <li class="flex items-center gap-2">
            <Swatch item={item} />
            <span>{item.label}</span>
          </li>
        )}
      </For>
    </ul>
  )
}

interface ChartFrameProps {
  id: string
  title: string
  /** Unit or axis note shown above the plot. */
  axisNote?: string
  /** One or two sentences describing what the chart shows; also read by assistive technology. */
  summary: string
  legend?: LegendItem[]
  caption?: JSX.Element
  table: { caption: string; columns: string[]; rows: string[][] }
  children: JSX.Element
}

/** Title, legend, plot, text summary and table twin for one chart. */
export default function ChartFrame(props: ChartFrameProps) {
  return (
    <figure class="rounded-lg border border-rule bg-surface p-4 sm:p-5" aria-labelledby={`${props.id}-title`}>
      <figcaption class="mb-3">
        <h3 id={`${props.id}-title`} class="text-base font-semibold text-ink">
          {props.title}
        </h3>
        <Show when={props.axisNote}>
          <p class="text-xs text-ink-3">{props.axisNote}</p>
        </Show>
      </figcaption>
      <Show when={props.legend && props.legend.length > 1}>
        <div class="mb-2">
          <Legend items={props.legend!} />
        </div>
      </Show>
      {props.children}
      <p id={`${props.id}-summary`} class="mt-3 text-sm text-ink-2">
        {props.summary}
      </p>
      <Show when={props.caption}>
        <div class="mt-2 text-sm text-ink-2">{props.caption}</div>
      </Show>
      <DataTable caption={props.table.caption} columns={props.table.columns} rows={props.table.rows} />
    </figure>
  )
}
