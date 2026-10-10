import { createMemo, createSignal, Show } from 'solid-js'
import type { Pollutant } from '../../api/types'
import { useDaily } from '../../queries/hooks'
import { useMetaContext } from '../../queries/metaContext'
import { stationOptionLabel, stationsFrom } from '../../transform/stations'
import { formatDateKey } from '../../transform/time'
import { dailyWindow, lastDailyDays } from '../../transform/window'
import { formatUnit } from '../../transform/units'
import DailyChart from '../charts/DailyChart'
import QueryBoundary from '../ui/QueryBoundary'
import Segmented from '../ui/Segmented'

const POLLUTANTS: Pollutant[] = ['pm10', 'pm2_5', 'no2']

/**
 * Daily means for one station and pollutant over the most recent days of the daily mart, from /aq/daily. The daily mart has
 * its own span (min_day to max_day in the manifest): the EEA series need not overlap the hourly window. Each station's
 * series lives under its daily location; without a station catalogue the city location is used.
 */
export default function DailyPanel() {
  const { query: meta } = useMetaContext()
  const range = createMemo(() => lastDailyDays(dailyWindow(meta.data!)))
  const options = createMemo(() =>
    POLLUTANTS.filter((p) => meta.data!.variables[p]).map((p) => ({ value: p, label: meta.data!.variables[p]!.label })),
  )
  const stations = createMemo(() => stationsFrom(meta.data))
  const [chosenStation, setChosenStation] = createSignal<string | undefined>()
  const station = createMemo(() => stations().find((s) => s.id === chosenStation()) ?? stations()[0])
  const [pollutant, setPollutant] = createSignal<Pollutant>('pm10')
  const query = useDaily(() => ({
    pollutant: pollutant(),
    from: range().from,
    to: range().to,
    location: station()?.dailyLocationId,
  }))
  const label = () => meta.data!.variables[pollutant()]?.label ?? pollutant()

  return (
    <div>
      <div class="mb-4 flex flex-col gap-3">
        <Show when={stations().length > 1}>
          <Segmented
            label="Station"
            options={stations().map((s) => ({ value: s.id, label: stationOptionLabel(s) }))}
            value={station()!.id}
            onChange={setChosenStation}
          />
        </Show>
        <Segmented label="Pollutant" options={options()} value={pollutant()} onChange={setPollutant} />
      </div>
      <p class="mb-3 text-xs text-ink-3" data-testid="daily-span">
        Showing {formatDateKey(range().from)} to {formatDateKey(range().to)}, the most recent days in the daily series.
      </p>
      <QueryBoundary
        query={query}
        what="daily series"
        isEmpty={(d) => d.days.length === 0}
        emptyTitle="No daily means in this period"
        emptyText="The warehouse has no daily values for this station and pollutant in the published period."
      >
        {(data) => (
          <Show when={data()}>
            {(d) => <DailyChart data={d()} label={label()} unit={meta.data!.variables[pollutant()]?.unit ?? formatUnit('ug/m3')} />}
          </Show>
        )}
      </QueryBoundary>
    </div>
  )
}
