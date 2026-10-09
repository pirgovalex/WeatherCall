import { createMemo, createSignal, Show } from 'solid-js'
import { useSeries } from '../../queries/hooks'
import { useMetaContext } from '../../queries/metaContext'
import { toSeriesView } from '../../transform/series'
import { formatDateKey, plovdivSpanToApiRange } from '../../transform/time'
import { allDays, dataWindow, isValidRange, lastDays, MAX_SPAN_DAYS } from '../../transform/window'
import type { DayRange } from '../../transform/window'
import SeriesChart from '../charts/SeriesChart'
import Notice from '../ui/Notice'
import QueryBoundary from '../ui/QueryBoundary'
import Segmented from '../ui/Segmented'
import { orderedVariables } from './ReadingsGrid'

type Preset = '1' | '3' | '7' | 'all'

const PRESETS: { value: Preset; label: string }[] = [
  { value: '1', label: 'Last day' },
  { value: '3', label: '3 days' },
  { value: '7', label: '7 days' },
  { value: 'all', label: 'All data' },
]

/** Variable and date-range controls with one time-series chart underneath. */
export default function SeriesPanel() {
  const { query: meta } = useMetaContext()
  const window = createMemo(() => dataWindow(meta.data!))
  const variables = createMemo(() => orderedVariables(meta.data!.variables))

  const [variable, setVariable] = createSignal<string>(variables()[0]?.id ?? 'pm10')
  const [custom, setCustom] = createSignal<DayRange | null>(null)
  const [preset, setPreset] = createSignal<Preset>('3')

  const presetRange = (p: Preset): DayRange => (p === 'all' ? allDays(window()) : lastDays(window(), Number(p)))
  const range = createMemo<DayRange>(() => custom() ?? presetRange(preset()))
  const valid = createMemo(() => isValidRange(range()))

  const selected = createMemo(() => variables().find((v) => v.id === variable()) ?? variables()[0])
  const query = useSeries(() =>
    valid() && selected() ? { variable: selected()!.id, ...plovdivSpanToApiRange(range().from, range().to) } : null,
  )
  const view = createMemo(() => (query.data ? toSeriesView(query.data) : null))

  const choosePreset = (p: Preset) => {
    setCustom(null)
    setPreset(p)
  }
  const setFrom = (value: string) => value && setCustom({ from: value, to: range().to })
  const setTo = (value: string) => value && setCustom({ from: range().from, to: value })

  return (
    <div>
      <div class="mb-4 flex flex-col gap-4">
        <Segmented
          label="Variable"
          options={variables().map((v) => ({ value: v.id, label: v.label }))}
          value={selected()?.id ?? ''}
          onChange={setVariable}
        />
        <div class="flex flex-wrap items-end gap-x-6 gap-y-3">
          <Segmented
            label="Period"
            options={PRESETS}
            value={custom() ? ('' as Preset) : preset()}
            onChange={choosePreset}
          />
          <div class="flex flex-wrap items-end gap-3">
            <label class="text-sm text-ink-2">
              <span class="mb-1 block text-xs text-ink-3">From (Plovdiv day)</span>
              <input
                type="date"
                value={range().from}
                min={window().minDay}
                max={window().maxDay}
                onChange={(e) => setFrom(e.currentTarget.value)}
                class="rounded-md border border-rule bg-surface px-2 py-1.5 text-sm text-ink"
              />
            </label>
            <label class="text-sm text-ink-2">
              <span class="mb-1 block text-xs text-ink-3">To (Plovdiv day)</span>
              <input
                type="date"
                value={range().to}
                min={window().minDay}
                max={window().maxDay}
                onChange={(e) => setTo(e.currentTarget.value)}
                class="rounded-md border border-rule bg-surface px-2 py-1.5 text-sm text-ink"
              />
            </label>
          </div>
        </div>
        <p class="text-xs text-ink-3">
          Data runs from {formatDateKey(window().minDay)} to {formatDateKey(window().maxDay)}. Days are Europe/Sofia calendar days;
          the day the clocks change in autumn has 25 hours.
        </p>
      </div>
      <Show
        when={valid()}
        fallback={
          <Notice tone="warning" title="Choose a valid period" live="status">
            The start day must not be after the end day, and the period can be at most {MAX_SPAN_DAYS} days.
          </Notice>
        }
      >
        <QueryBoundary
          query={query}
          what={`${selected()?.label ?? 'series'} series`}
          isEmpty={(d) => d.timestamps.length === 0}
          emptyTitle="No values in this period"
          emptyText="The warehouse has no hourly values for this variable between these days. Choose a wider period."
        >
          {() => <Show when={view()}>{(v) => <SeriesChart view={v()} label={selected()!.label} />}</Show>}
        </QueryBoundary>
      </Show>
    </div>
  )
}

