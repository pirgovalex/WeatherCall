import { For } from 'solid-js'
import { useModelCard } from '../../queries/hooks'
import { featureLabel } from '../../transform/risk'
import { formatDateKey, formatPlovdivDateTimeYear, isoToEpoch } from '../../transform/time'
import { formatPercent } from '../../transform/units'
import QueryBoundary from '../ui/QueryBoundary'

/** The model card written out as sentences, with the limitations quoted as published. */
export default function ModelCardProse(props: { modelId: string }) {
  const query = useModelCard(() => props.modelId)
  return (
    <QueryBoundary query={query} what="model card">
      {(card) => (
        <div class="max-w-3xl space-y-4 text-base text-ink-2" data-testid="model-card-prose">
          <p>
            The current model is <strong class="font-semibold text-ink">{card().model_id}</strong>, built with{' '}
            {card().algorithm.replace(/_/g, ' ')} and trained on {formatPlovdivDateTimeYear(isoToEpoch(card().trained_at))} (Plovdiv
            time).
          </p>
          <p>
            It learned from days between {formatDateKey(card().windows.train_start)} and {formatDateKey(card().windows.train_end)},
            was tuned on {formatDateKey(card().windows.validation_start)} to {formatDateKey(card().windows.validation_end)}, and
            was scored on {formatDateKey(card().windows.test_start)} to {formatDateKey(card().windows.test_end)}, days it had not
            seen. The windows do not overlap.
          </p>
          <p>
            What it predicts: <span class="text-ink">{card().label_text}</span> (label <code class="text-sm">{card().label_id}</code>).
            It counts a day as a predicted bad day when the probability reaches {formatPercent(card().threshold)}.
          </p>
          <p>
            {card().uses_forecast_weather
              ? 'Some of its inputs are weather forecasts that are available when the score is issued, rather than observed weather.'
              : 'It does not use forecast weather. Scores produced with it use historical weather as a stand-in for the weather on the target day.'}
          </p>
          <div>
            <p>Inputs:</p>
            <ul class="mt-1 columns-1 gap-8 pl-5 sm:columns-2">
              <For each={card().features}>
                {(feature) => (
                  <li class="list-disc break-inside-avoid">
                    {featureLabel(feature)} <span class="text-xs text-ink-3">({feature})</span>
                  </li>
                )}
              </For>
            </ul>
          </div>
          <div class="rounded-md border-l-4 border-notice-edge bg-notice px-4 py-3">
            <p class="font-semibold text-ink">Limitations stated on the model card</p>
            <p class="mt-1 text-ink-2">{card().limitations}</p>
          </div>
        </div>
      )}
    </QueryBoundary>
  )
}
