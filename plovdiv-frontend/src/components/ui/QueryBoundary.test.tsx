import { render, screen } from '@solidjs/testing-library'
import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import QueryBoundary, { describeError } from './QueryBoundary'
import type { QueryLike } from './QueryBoundary'

const query = (over: Partial<QueryLike<string[]>>): QueryLike<string[]> => ({
  data: undefined,
  isPending: false,
  isError: false,
  error: null,
  isPlaceholderData: false,
  refetch: () => undefined,
  ...over,
})

const show = (q: QueryLike<string[]>) =>
  render(() => (
    <QueryBoundary query={q} what="test data" isEmpty={(d) => d.length === 0}>
      {(d) => <p>rows: {d().length}</p>}
    </QueryBoundary>
  ))

describe('QueryBoundary', () => {
  it('shows loading', () => {
    show(query({ isPending: true }))
    expect(screen.getByRole('status')).toHaveTextContent('Loading test data')
  })

  it('shows the content', () => {
    show(query({ data: ['a'] }))
    expect(screen.getByText('rows: 1')).toBeInTheDocument()
  })

  it('shows an empty state', () => {
    show(query({ data: [] }))
    expect(screen.getByTestId('empty-state')).toBeInTheDocument()
  })

  it('shows the envelope message and code for an error', () => {
    show(query({ isError: true, error: new ApiError(404, 'NOT_FOUND', "Station 'X' was not found.") }))
    expect(screen.getByRole('alert')).toHaveTextContent("Station 'X' was not found.")
    expect(screen.getByRole('alert')).toHaveTextContent('NOT_FOUND')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('treats 503 as a status message without a retry button', () => {
    show(query({ isError: true, error: new ApiError(503, 'SERVICE_UNAVAILABLE', 'The mart mart_aq_trend_monthly is not declared.') }))
    const note = screen.getByRole('status')
    expect(note).toHaveTextContent('not available in this data run')
    expect(note).toHaveTextContent('mart_aq_trend_monthly')
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it('keeps earlier data visible while a new request fails or reloads', () => {
    show(query({ data: ['a', 'b'], isPlaceholderData: true }))
    expect(screen.getByText('rows: 2')).toBeInTheDocument()
  })

  it('words network failures plainly', () => {
    expect(describeError(new ApiError(null, 'NETWORK', 'x'), 'trend').title).toBe('The API cannot be reached')
  })
})
