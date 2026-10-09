import { createMemo, createSignal, Show } from 'solid-js'
import type { RiskRow } from '../../transform/risk'
import { assessRisk, splitLabel, toDriverBars, toLatestRisk } from '../../transform/risk'
import { stationOptionLabel, stationsFrom } from '../../transform/stations'
import { formatProbability } from '../../transform/units'
import { formatDateKey, formatPlovdivDateTimeYear, isoToEpoch } from '../../transform/time'
import { useLatestRisk, useModelCard } from '../../queries/hooks'
import { useMetaContext } from '../../queries/metaContext'
import Notice from '../ui/Notice'
import QueryBoundary, { ErrorState } from '../ui/QueryBoundary'
import Segmented from '../ui/Segmented'
import type { ModelCardResponse } from '../../api/types'
import DriverBars from './DriverBars'
import RiskMeter from './RiskMeter'

function Fact(props: { term: string; children: string }) {
  return (
    <div>
      <dt class="text-xs text-ink-3">{props.term}</dt>
      <dd class="break-words text-sm font-medium text-ink">{props.children}</dd>
    </div>
  )
}

function Notices(props: { row: RiskRow; card: ModelCardResponse | undefined }) {
  const assessment = () => assessRisk(props.row, props.card)
  return (
    <div class="mb-6 space-y-3">
      <Show when={assessment().isHistorical}>
        <Notice tone="warning" title="Historical evaluation row. Live forecasts are not running yet." testId="historical-notice">
          <p>
            This score is for {formatDateKey(props.row.targetDate)}. It comes from the {splitLabel(props.row.split).toLowerCase()} used to
            check the model on past days, and it is not a forecast for tomorrow.
          </p>
          <ul class="mt-1 list-disc pl-5">
            <Show when={assessment().notLive}>
              <li>The row belongs to the "{props.row.split}" split, not to live forecasting.</li>
            </Show>
            <Show when={assessment().tooOld}>
              <li>
                Its target date is more than 3 days before today ({formatDateKey(assessment().today)}, Europe/Sofia).
              </li>
            </Show>
          </ul>
        </Notice>
      </Show>
      <Show when={!assessment().isHistorical && assessment().isToday}>
        <Notice tone="info" title="Forecast for today" testId="today-notice">
          Station measurements are published with about a day's delay, so the newest score is for today,{' '}
          {formatDateKey(props.row.targetDate)}. It uses yesterday's measurements and today's weather forecast.
        </Notice>
      </Show>
      <Show when={!assessment().isHistorical && !assessment().isTomorrow && !assessment().isToday}>
        <Notice tone="info" title="This row is not for tomorrow" testId="not-tomorrow-notice">
          It applies to {formatDateKey(props.row.targetDate)}. Tomorrow in Europe/Sofia is {formatDateKey(assessment().tomorrow)}.
        </Notice>
      </Show>
      <Show when={assessment().standInWeather}>
        <Notice tone="info" title="Historical weather used as a stand-in" testId="stand-in-notice">
          The model card says this model does not use forecast weather, so the score was produced with historical weather as a
          stand-in for the weather on the target day.
        </Notice>
      </Show>
    </div>
  )
}

function RiskBody(props: { row: RiskRow }) {
  const card = useModelCard(() => props.row.modelId)
  const assessment = () => assessRisk(props.row, card.data)
  const prob = createMemo(() => formatProbability(props.row.probability))
  const bars = createMemo(() => toDriverBars(props.row.drivers))

  return (
    <>
      <Notices row={props.row} card={card.data} />
      <div class="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <p
            class={`font-bold leading-none tracking-tight text-ink ${prob().text.length > 4 ? 'text-4xl sm:text-5xl' : 'text-6xl sm:text-7xl'}`}
            data-testid="risk-percent"
          >
            {prob().text}
          </p>
          <p class="mt-2 text-lg font-medium text-ink">probability of a bad air quality day</p>
          <Show
            when={card.data}
            fallback={
              <Show
                when={card.isError}
                fallback={<p class="mt-2 text-sm text-ink-2">Loading the definition of a bad air quality day.</p>}
              >
                <div class="mt-3">
                  <ErrorState error={card.error} what="model card" onRetry={() => void card.refetch()} />
                </div>
              </Show>
            }
          >
            {(c) => (
              <p class="mt-2 text-sm text-ink-2" data-testid="label-text">
                A bad air quality day here means: {c().label_text}.
              </p>
            )}
          </Show>
          <div class="mt-6">
            <RiskMeter
              probability={props.row.probability}
              threshold={card.data?.threshold}
              label={`Probability of a bad air quality day, ${prob().spoken}`}
            />
          </div>
          <dl class="mt-6 grid grid-cols-2 gap-x-6 gap-y-3">
            <Fact term="Model">{props.row.modelId}</Fact>
            <Fact term="Data split">{splitLabel(props.row.split)}</Fact>
            <Fact term="Applies to">{formatDateKey(props.row.targetDate)}</Fact>
            <Fact term="Issued">{formatPlovdivDateTimeYear(isoToEpoch(props.row.issuedAt))}</Fact>
            <Show when={props.row.actualLabel !== null}>
              <Fact term="Actual outcome">{props.row.actualLabel === 1 ? 'It was a bad air quality day' : 'It was not a bad air quality day'}</Fact>
            </Show>
          </dl>
          <Show when={assessment().isHistorical}>
            <p class="sr-only">This is a historical row.</p>
          </Show>
        </div>
        <div>
          <h3 class="mb-3 text-base font-semibold text-ink">What pushes the score up or down</h3>
          <Show
            when={bars().length > 0}
            fallback={<p class="text-sm text-ink-2">No driver breakdown is published for this row.</p>}
          >
            <DriverBars bars={bars()} />
          </Show>
        </div>
      </div>
    </>
  )
}

/** The headline card: the probability of a bad air quality day, what it means, and what drives it. */
export default function RiskCard() {
  const { query } = useMetaContext()
  const stations = createMemo(() => stationsFrom(query.data))
  const [chosen, setChosen] = createSignal<string | undefined>()
  const stationId = () => chosen() ?? stations()[0]?.id
  const risk = useLatestRisk(stationId)
  const row = createMemo(() => (risk.data ? toLatestRisk(risk.data) : null))
  const card = useModelCard(() => row()?.modelId)
  const title = () => {
    const r = row()
    if (!r) return 'Air quality risk'
    const a = assessRisk(r, card.data)
    if (a.isHistorical) return 'Latest model row: air quality risk (historical)'
    if (a.isToday) return "Today's air quality risk"
    if (a.isTomorrow) return "Tomorrow's air quality risk"
    return `Air quality risk for ${formatDateKey(r.targetDate)}`
  }

  return (
    <section aria-labelledby="risk-title" class="rounded-lg border border-rule bg-surface p-5 sm:p-8" data-testid="risk-card">
      <div class="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="risk-title" class="text-2xl font-semibold tracking-tight text-ink">
            {title()}
          </h2>
          <p class="mt-1 text-sm text-ink-2">The model's estimate of whether the day will be a bad air quality day.</p>
        </div>
        <Show when={stations().length > 0}>
          <Segmented
            label="Station"
            options={stations().map((s) => ({ value: s.id, label: stationOptionLabel(s) }))}
            value={stationId() ?? ''}
            onChange={setChosen}
          />
        </Show>
      </div>
      <Show
        when={stations().length > 0}
        fallback={<p class="text-sm text-ink-2">The warehouse run does not list any stations, so there is no risk score to show.</p>}
      >
        <QueryBoundary
          query={risk}
          what="risk score"
          isEmpty={(d) => d.target_dates.length === 0}
          emptyTitle="No risk score for this station"
          emptyText="The model has not published a score for this station yet."
        >
          {() => <Show when={row()}>{(r) => <RiskBody row={r()} />}</Show>}
        </QueryBoundary>
      </Show>
    </section>
  )
}

