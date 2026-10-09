import { createMemo, For } from 'solid-js'
import { HOURLY_VARIABLE_ORDER } from '../../config'
import { useMetaContext } from '../../queries/metaContext'
import { dataWindow, DEFAULT_DAYS, lastDays } from '../../transform/window'
import ReadingTile from './ReadingTile'

export function orderedVariables(variables: Record<string, { label: string }>): { id: string; label: string }[] {
  const ids = Object.keys(variables)
  const known = HOURLY_VARIABLE_ORDER.filter((id) => ids.includes(id))
  const rest = ids.filter((id) => !HOURLY_VARIABLE_ORDER.includes(id))
  return [...known, ...rest].map((id) => ({ id, label: variables[id]!.label }))
}

export default function ReadingsGrid() {
  const { query } = useMetaContext()
  const range = createMemo(() => lastDays(dataWindow(query.data!), DEFAULT_DAYS))
  const variables = createMemo(() => orderedVariables(query.data!.variables))
  return (
    <div class="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-rule bg-rule sm:grid-cols-3">
      <For each={variables()}>{(v) => <ReadingTile variable={v.id} label={v.label} range={range()} />}</For>
    </div>
  )
}
