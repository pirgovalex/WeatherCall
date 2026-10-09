import { createMemo, createSignal, Show } from 'solid-js'
import { ApiError } from '../../api/client'
import { useTrend } from '../../queries/hooks'
import { useMetaContext } from '../../queries/metaContext'
import { stationOptionLabel, stationsFrom } from '../../transform/stations'
import { toTrendView } from '../../transform/trend'
import { endOfMonth } from '../../transform/window'
import TrendChart from '../charts/TrendChart'
import QueryBoundary, { ErrorState } from '../ui/QueryBoundary'
import Segmented from '../ui/Segmented'

const STATION_TYPE_HELP: Record<string, string> = {
  background: 'A background station is placed away from direct sources such as busy roads, so it reflects the general air in the area.',
  traffic: 'A traffic station is placed close to a busy road, so it reflects air near heavy traffic and usually reads higher.',
}

/** Station switcher and the observed against weather-adjusted PM10 trend. */
export default function TrendPanel() {
  const { query: meta } = useMetaContext()
  const stations = createMemo(() => stationsFrom(meta.data))
  const [chosen, setChosen] = createSignal<string | undefined>()
  const station = createMemo(() => stations().find((s) => s.id === chosen()) ?? stations()[0])
  const range = createMemo(() => {
    const mart = meta.data?.marts.mart_aq_trend_monthly
    return mart ? { from: mart.min_month, to: endOfMonth(mart.max_month) } : null
  })
  const query = useTrend(() => {
    const r = range()
    const s = station()
    return r && s ? { stationId: s.id, from: r.from, to: r.to } : null
  })
  const view = createMemo(() => (query.data ? toTrendView(query.data) : null))

  return (
    <Show
      when={range()}
      fallback={
        <ErrorState
          what="monthly trend"
          error={new ApiError(503, 'SERVICE_UNAVAILABLE', 'The mart mart_aq_trend_monthly is not declared in the manifest.')}
        />
      }
    >
      <Show
        when={station()}
        fallback={<p class="text-sm text-ink-2">The warehouse run does not list any stations.</p>}
      >
        {(s) => (
          <div>
            <div class="mb-4 flex flex-col gap-3">
              <Segmented
                label="Station"
                options={stations().map((st) => ({ value: st.id, label: stationOptionLabel(st) }))}
                value={s().id}
                onChange={setChosen}
              />
              <p class="max-w-2xl text-sm text-ink-2">
                {s().label} is a {s().stationType} station in an {s().areaType} area. {STATION_TYPE_HELP[s().stationType] ?? ''}
              </p>
            </div>
            <QueryBoundary
              query={query}
              what="monthly trend"
              isEmpty={(d) => d.months.length === 0}
              emptyTitle="No monthly means for this station"
              emptyText="The warehouse has no monthly PM10 means for this station in the published period."
            >
              {() => <Show when={view()}>{(v) => <TrendChart view={v()} stationLabel={s().label} />}</Show>}
            </QueryBoundary>
          </div>
        )}
      </Show>
    </Show>
  )
}
