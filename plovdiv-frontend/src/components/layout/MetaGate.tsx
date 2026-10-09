import { Match, Switch } from 'solid-js'
import type { JSX } from 'solid-js'
import { useMetaContext } from '../../queries/metaContext'
import { ErrorState, LoadingState } from '../ui/QueryBoundary'

/** Page content renders only once the warehouse metadata is known. */
export default function MetaGate(props: { children: JSX.Element }) {
  const { query } = useMetaContext()
  return (
    <Switch>
      <Match when={query.data !== undefined}>{props.children}</Match>
      <Match when={query.isError}>
        <ErrorState error={query.error} what="warehouse information" onRetry={() => void query.refetch()} />
      </Match>
      <Match when={true}>
        <LoadingState what="warehouse information" />
      </Match>
    </Switch>
  )
}
