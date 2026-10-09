import { Match, Show, Switch } from 'solid-js'
import { describeError } from '../ui/QueryBoundary'
import { useSeries } from '../../queries/hooks'
import { latestReading, sourceLabel, toSeriesView } from '../../transform/series'
import { formatPlovdivDateTime } from '../../transform/time'
import { formatNumber, formatUnit } from '../../transform/units'
import type { DayRange } from '../../transform/window'
import { plovdivSpanToApiRange } from '../../transform/time'
import { createMemo } from 'solid-js'

interface ReadingTileProps {
  variable: string
  label: string
  range: DayRange
}

/** The newest valid hourly value for one variable, with its time and source. */
export default function ReadingTile(props: ReadingTileProps) {
  const query = useSeries(() => ({ variable: props.variable, ...plovdivSpanToApiRange(props.range.from, props.range.to) }))
  const view = createMemo(() => (query.data ? toSeriesView(query.data) : null))
  const reading = createMemo(() => {
    const v = view()
    return v ? latestReading(v) : null
  })

  return (
    <div class="bg-surface p-4" data-testid={`reading-${props.variable}`}>
      <h3 class="text-sm font-medium text-ink-2">{props.label}</h3>
      <Switch>
        <Match when={query.isError && !query.data}>
          <p class="mt-2 text-sm text-ink-2">{describeError(query.error, props.label).title}</p>
          <button
            type="button"
            onClick={() => void query.refetch()}
            class="mt-2 rounded border border-rule px-2 py-1 text-xs font-medium text-ink hover:bg-sunken"
          >
            Try again
          </button>
        </Match>
        <Match when={!query.data}>
          <p role="status" class="mt-2 text-sm text-ink-3">
            Loading
          </p>
        </Match>
        <Match when={reading()}>
          {(r) => (
            <>
              <p class="mt-1 text-3xl font-semibold leading-tight tracking-tight text-ink">
                {formatNumber(r().value, 1)}
                <span class="ml-1 text-base font-normal text-ink-2">{formatUnit(view()!.unit)}</span>
              </p>
              <p class="mt-1 text-xs text-ink-3">{formatPlovdivDateTime(r().time)} Plovdiv</p>
              <Show when={view()}>
                {(v) => <p class="mt-1 text-xs text-ink-3">{sourceLabel(v().sourceType).short}</p>}
              </Show>
            </>
          )}
        </Match>
        <Match when={true}>
          <p class="mt-2 text-sm text-ink-2">No valid reading in the last {props.range.to === props.range.from ? 'day' : 'few days'} of data.</p>
        </Match>
      </Switch>
    </div>
  )
}
