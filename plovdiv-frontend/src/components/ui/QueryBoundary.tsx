import { Match, Show, Switch } from 'solid-js'
import type { Accessor, JSX } from 'solid-js'
import { ApiError } from '../../api/client'
import Notice from './Notice'

/** The parts of a TanStack Query result the boundary needs. */
export interface QueryLike<T> {
  data: T | undefined
  isPending: boolean
  isError: boolean
  error: unknown
  isPlaceholderData: boolean
  refetch: () => unknown
}

interface QueryBoundaryProps<T> {
  query: QueryLike<T>
  /** What is being loaded, for screen readers and messages: "monthly trend". */
  what: string
  isEmpty?: (data: T) => boolean
  emptyTitle?: string
  emptyText?: string
  children: (data: Accessor<T>) => JSX.Element
}

export function describeError(error: unknown, what: string): { title: string; text: string; code: string } {
  if (error instanceof ApiError) {
    switch (true) {
      case error.status === 503:
        return {
          title: `The ${what} is not available in this data run`,
          text: `${error.message} The warehouse run behind this dashboard does not publish it yet.`,
          code: error.code,
        }
      case error.status === 404:
        return { title: `No ${what} found`, text: error.message, code: error.code }
      case error.status === 422:
        return { title: `The request for the ${what} was rejected`, text: error.message, code: error.code }
      case error.code === 'NETWORK':
        return {
          title: 'The API cannot be reached',
          text: 'Check your connection, or that the backend is running, then try again.',
          code: error.code,
        }
      case error.code === 'INVALID_RESPONSE':
        return {
          title: `The ${what} response was not what the contract describes`,
          text: error.message,
          code: error.code,
        }
      default:
        return { title: `The ${what} could not be loaded`, text: error.message, code: error.code }
    }
  }
  return { title: `The ${what} could not be loaded`, text: 'An unexpected error occurred.', code: 'UNKNOWN' }
}

export function LoadingState(props: { what: string }) {
  return (
    <div
      role="status"
      class="flex min-h-32 items-center gap-3 rounded-md border border-dashed border-rule bg-sunken px-4 py-6 text-sm text-ink-2"
    >
      <span aria-hidden="true" class="size-3 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />
      Loading {props.what}
    </div>
  )
}

export function ErrorState(props: { error: unknown; what: string; onRetry?: () => void }) {
  const info = () => describeError(props.error, props.what)
  const unavailable = () => props.error instanceof ApiError && props.error.status === 503
  return (
    <div data-testid="error-state">
      <Notice tone={unavailable() ? 'warning' : 'danger'} title={info().title} live={unavailable() ? 'status' : 'alert'}>
        <p>{info().text}</p>
        <p class="mt-1 text-xs text-ink-3">Error code: {info().code}</p>
        <Show when={props.onRetry && !unavailable()}>
          <button
            type="button"
            onClick={() => props.onRetry?.()}
            class="mt-3 rounded border border-rule bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-sunken"
          >
            Try again
          </button>
        </Show>
      </Notice>
    </div>
  )
}

export function EmptyState(props: { title: string; text: string }) {
  return (
    <div role="status" data-testid="empty-state" class="rounded-md border border-dashed border-rule bg-sunken px-4 py-6">
      <p class="font-semibold text-ink">{props.title}</p>
      <p class="mt-1 max-w-prose text-sm text-ink-2">{props.text}</p>
    </div>
  )
}

/**
 * Renders loading, error, empty and success states for one query. While a new
 * range loads over an earlier result, the earlier chart stays at reduced
 * opacity instead of being replaced by a skeleton.
 */
export default function QueryBoundary<T>(props: QueryBoundaryProps<T>) {
  const data = () => props.query.data
  return (
    <Switch>
      <Match when={props.query.isError && data() === undefined}>
        <ErrorState error={props.query.error} what={props.what} onRetry={() => void props.query.refetch()} />
      </Match>
      <Match when={data() === undefined}>
        <LoadingState what={props.what} />
      </Match>
      <Match when={data() !== undefined && props.isEmpty?.(data() as T)}>
        <EmptyState
          title={props.emptyTitle ?? `No ${props.what} for this selection`}
          text={props.emptyText ?? 'The warehouse has no rows for the chosen filters. Try another station, variable or period.'}
        />
      </Match>
      <Match when={data() !== undefined}>
        <div class={props.query.isPlaceholderData ? 'opacity-60 transition-opacity' : 'transition-opacity'} aria-busy={props.query.isPlaceholderData}>
          {props.children(data as Accessor<T>)}
        </div>
      </Match>
    </Switch>
  )
}
