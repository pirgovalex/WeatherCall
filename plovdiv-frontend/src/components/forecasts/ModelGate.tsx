import { createMemo, Show } from 'solid-js'
import type { Accessor, JSX } from 'solid-js'
import { useLatestRisk } from '../../queries/hooks'
import { useMetaContext } from '../../queries/metaContext'
import { stationsFrom } from '../../transform/stations'
import { toLatestRisk } from '../../transform/risk'
import QueryBoundary from '../ui/QueryBoundary'

/**
 * The API has no endpoint that lists models, so the current model is the one on
 * the most recently issued risk row of the first station (contract gap noted in
 * the README). Children receive its id.
 */
export default function ModelGate(props: { children: (modelId: Accessor<string>) => JSX.Element }) {
  const { query: meta } = useMetaContext()
  const first = createMemo(() => stationsFrom(meta.data)[0]?.id)
  const risk = useLatestRisk(first)
  const modelId = createMemo(() => (risk.data ? toLatestRisk(risk.data)?.modelId : undefined))
  return (
    <Show
      when={first()}
      fallback={<p class="text-sm text-ink-2">The warehouse run does not list any stations, so no model can be identified.</p>}
    >
      <QueryBoundary
        query={risk}
        what="current model"
        isEmpty={() => modelId() === undefined}
        emptyTitle="No model has published a score yet"
        emptyText="Model details appear once the first risk score exists."
      >
        {() => <Show when={modelId()}>{(id) => props.children(id)}</Show>}
      </QueryBoundary>
    </Show>
  )
}
