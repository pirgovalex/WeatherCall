import { createMemo, createSignal, For, Show } from 'solid-js'
import type { AqRiskResponse } from '../../api/types'
import { useModelCard, useRiskRange } from '../../queries/hooks'
import { useMetaContext } from '../../queries/metaContext'
import { stationOptionLabel, stationsFrom } from '../../transform/stations'
import { addDays, formatDateKey } from '../../transform/time'
import type { TrackSummary } from '../../transform/trackRecord'
import { OUTCOME_LABELS, recentCalls, summarize, toCalls } from '../../transform/trackRecord'
import { formatPercent, formatProbability } from '../../transform/units'
import Segmented from '../ui/Segmented'
import VerificationEmpty from './VerificationEmpty'

const RECENT = 10

function Stat(props: { term: string; value: string; note?: string }) {
  return (
    <div class="rounded-md border border-rule bg-surface px-4 py-3">
      <dt class="text-xs text-ink-3">{props.term}</dt>
      <dd class="text-xl font-semibold tabular-nums text-ink">{props.value}</dd>
      <Show when={props.note}>
        <dd class="text-xs text-ink-3">{props.note}</dd>
      </Show>
    </div>
  )
}

function Summary(props: { title: string; intro: string; summary: TrackSummary; testId: string }) {
  const s = () => props.summary
  return (
    <section class="mt-6" data-testid={props.testId} aria-label={props.title}>
      <h3 class="text-base font-semibold text-ink">{props.title}</h3>
      <p class="mt-1 max-w-2xl text-sm text-ink-2">{props.intro}</p>
      <dl class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          term="Right calls"
          value={`${s().correct} of ${s().decided}`}
          note={s().decided > 0 ? formatPercent(s().correct / s().decided) : undefined}
        />
        <Stat term="Bad days flagged" value={`${s().badDaysFlagged} of ${s().badDays}`} />
        <Stat term="False alarms" value={String(s().falseAlarms)} />
        <Stat
          term="Brier score"
          value={s().brier === null ? '—' : s().brier!.toFixed(3)}
          note={s().baseRate === null ? undefined : `${formatPercent(s().baseRate!)} of these days were bad`}
        />
      </dl>
      <Show when={s().waiting > 0}>
        <p class="mt-2 text-xs text-ink-3">
          {s().waiting} {s().waiting === 1 ? 'day is' : 'days are'} not counted yet: the station has not published a complete day.
        </p>
      </Show>
    </section>
  )
}

/** Live calls compared with what the station measured, and the replay of the days after the test period. */
export default function TrackRecord() {
  const { query: meta } = useMetaContext()
  const stations = createMemo(() => stationsFrom(meta.data))
  const [chosen, setChosen] = createSignal<string | undefined>()
  const station = createMemo(() => stations().find((s) => s.id === chosen()) ?? stations()[0])
  const range = createMemo(() => {
    const last = meta.data?.marts.mart_aq_risk_daily?.max_target_date
    return last ? { from: addDays(last, -365), to: last } : null
  })
  const args = (split: 'live' | 'replay') => () => {
    const r = range()
    const s = station()
    return r && s ? { stationId: s.id, split, from: r.from, to: r.to } : null
  }
  const live = useRiskRange(args('live'))
  const replay = useRiskRange(args('replay'))
  const modelId = createMemo(() => live.data?.model_id[0] ?? replay.data?.model_id[0])
  const card = useModelCard(modelId)
  const threshold = () => card.data?.threshold

  const calls = (res: AqRiskResponse | undefined) => {
    const t = threshold()
    return t === undefined ? [] : toCalls(res, t)
  }
  const liveCalls = createMemo(() => calls(live.data))
  const replayCalls = createMemo(() => calls(replay.data))
  const anything = () => liveCalls().length > 0 || replayCalls().length > 0

  return (
    <Show when={station() && range()} fallback={<VerificationEmpty />}>
      <div data-testid="track-record">
        <Segmented
          label="Station"
          options={stations().map((s) => ({ value: s.id, label: stationOptionLabel(s) }))}
          value={station()!.id}
          onChange={setChosen}
        />
        <Show when={anything()} fallback={<div class="mt-4"><VerificationEmpty /></div>}>
          <p class="mt-4 max-w-2xl text-sm text-ink-2">
            A call is a bad-day warning when the probability reaches the model's threshold of{' '}
            {formatPercent(threshold()!)}. A day counts once the station has published at least 18 valid hours for it.
          </p>
          <Show when={liveCalls().length > 0}>
            <Summary
              title="Live forecasts"
              intro="Scores issued in real time, before the day they are about, then checked against the station."
              summary={summarize(liveCalls())}
              testId="track-live"
            />
            <div class="mt-4 overflow-x-auto rounded-lg border border-rule bg-surface">
              <table class="w-full border-collapse text-left text-sm" data-testid="recent-calls">
                <caption class="sr-only">The most recent live forecasts and what happened</caption>
                <thead class="bg-sunken text-ink">
                  <tr>
                    <th scope="col" class="px-4 py-2 font-semibold">Day</th>
                    <th scope="col" class="px-4 py-2 text-right font-semibold">Probability</th>
                    <th scope="col" class="px-4 py-2 font-semibold">Call</th>
                    <th scope="col" class="px-4 py-2 font-semibold">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  <For each={recentCalls(liveCalls(), RECENT)}>
                    {(c) => (
                      <tr class="border-t border-rule">
                        <th scope="row" class="px-4 py-2 font-normal text-ink">{formatDateKey(c.targetDate)}</th>
                        <td class="px-4 py-2 text-right tabular-nums text-ink-2">{formatProbability(c.probability).text}</td>
                        <td class="px-4 py-2 text-ink-2">{c.calledBad ? 'Bad day' : 'Not bad'}</td>
                        <td class="px-4 py-2 text-ink-2">{OUTCOME_LABELS[c.outcome]}</td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </div>
          </Show>
          <Show when={replayCalls().length > 0}>
            <Summary
              title="Replay of the days after the test period"
              intro={
                'The same model scored afterwards on every day from the start of the year with the inputs a live run would have ' +
                'had. Nothing here was issued in real time, and station values have been checked since, so this is a little ' +
                'kinder than live forecasting.'
              }
              summary={summarize(replayCalls())}
              testId="track-replay"
            />
          </Show>
        </Show>
      </div>
    </Show>
  )
}
