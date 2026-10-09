import { createMemo, For } from 'solid-js'
import type { JSX } from 'solid-js'
import { useModelCard } from '../../queries/hooks'
import { METRIC_HELP, toPerformance } from '../../transform/performance'
import { formatDateKey } from '../../transform/time'
import { formatPercent } from '../../transform/units'
import QueryBoundary from '../ui/QueryBoundary'

const NOT_COMPUTED = 'Not computed'
const NOT_PUBLISHED = 'Not published'

function fixed(value: number | null, digits: number, missing = NOT_COMPUTED): JSX.Element {
  if (value !== null) return value.toFixed(digits)
  return (
    <>
      <span aria-hidden="true">{'\u2014'}</span>
      <span class="sr-only">{missing}</span>
    </>
  )
}

function describeDifference(diff: number | null): string {
  if (diff === null) return NOT_COMPUTED
  if (Math.abs(diff) < 0.0005) return 'Same as the model'
  return `${Math.abs(diff).toFixed(3)} ${diff < 0 ? 'lower' : 'higher'} than the model`
}

export default function PerformanceTable(props: { modelId: string }) {
  const query = useModelCard(() => props.modelId)
  return (
    <QueryBoundary query={query} what="model performance">
      {(card) => {
        const rows = createMemo(() => toPerformance(card()))
        return (
          <div>
            <p class="mb-3 max-w-2xl text-sm text-ink-2">
              All numbers are measured on the test period, {formatDateKey(card().windows.test_start)} to{' '}
              {formatDateKey(card().windows.test_end)}, which the model never saw during training. Precision and recall use the
              decision threshold of {formatPercent(card().threshold)}.
            </p>
            <div class="overflow-x-auto rounded-lg border border-rule bg-surface">
              <table class="w-full min-w-[40rem] border-collapse text-left text-sm" data-testid="performance-table">
                <caption class="sr-only">
                  Test performance of {card().model_id} compared with the climatology and persistence baselines
                </caption>
                <thead class="bg-sunken text-ink">
                  <tr>
                    <th scope="col" class="px-4 py-3 font-semibold">
                      Predictor
                    </th>
                    <th scope="col" class="whitespace-nowrap px-4 py-3 text-right font-semibold">
                      ROC-AUC
                    </th>
                    <th scope="col" class="px-4 py-3 font-semibold">
                      ROC-AUC compared with the model
                    </th>
                    <th scope="col" class="whitespace-nowrap px-4 py-3 text-right font-semibold">
                      PR-AUC
                    </th>
                    <th scope="col" class="whitespace-nowrap px-4 py-3 text-right font-semibold">
                      Brier score
                    </th>
                    <th scope="col" class="whitespace-nowrap px-4 py-3 text-right font-semibold">
                      Precision
                    </th>
                    <th scope="col" class="whitespace-nowrap px-4 py-3 text-right font-semibold">
                      Recall
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <For each={rows()}>
                    {(row) => {
                      const baseline = row.id !== 'model'
                      const other = baseline ? NOT_PUBLISHED : NOT_COMPUTED
                      return (
                        <tr class="border-t border-rule align-top">
                          <th scope="row" class="px-4 py-3 font-medium text-ink">
                            {row.name}
                            <span class="block text-xs font-normal text-ink-3">{row.description}</span>
                          </th>
                          <td class="px-4 py-3 text-right tabular-nums text-ink">{fixed(row.rocAuc, 3)}</td>
                          <td class="px-4 py-3 text-ink-2">{baseline ? describeDifference(row.difference) : 'Reference'}</td>
                          <td class="px-4 py-3 text-right tabular-nums text-ink-2">{fixed(row.prAuc, 3, other)}</td>
                          <td class="px-4 py-3 text-right tabular-nums text-ink-2">{fixed(row.brier, 3, other)}</td>
                          <td class="px-4 py-3 text-right tabular-nums text-ink-2">{fixed(row.precision, 2, other)}</td>
                          <td class="px-4 py-3 text-right tabular-nums text-ink-2">{fixed(row.recall, 2, other)}</td>
                        </tr>
                      )
                    }}
                  </For>
                </tbody>
              </table>
            </div>
            <p class="mt-2 text-xs text-ink-3">
              The model card publishes only ROC-AUC for the baselines. A higher ROC-AUC is better; a lower Brier score is better.
            </p>
            <dl class="mt-6 grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
              <For each={METRIC_HELP}>
                {(m) => (
                  <div>
                    <dt class="font-semibold text-ink">{m.name}</dt>
                    <dd class="text-ink-2">{m.help}</dd>
                  </div>
                )}
              </For>
            </dl>
          </div>
        )
      }}
    </QueryBoundary>
  )
}
