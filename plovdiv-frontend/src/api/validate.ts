import { ApiError } from './client'

/** Parallel arrays must have the same length; anything else is a broken response. */
export function assertEqualLengths(what: string, arrays: Record<string, readonly unknown[]>): void {
  const entries = Object.entries(arrays)
  const first = entries[0]
  if (!first) return
  for (const [name, values] of entries) {
    if (values.length !== first[1].length) {
      throw new ApiError(
        null,
        'INVALID_RESPONSE',
        `The ${what} response is inconsistent: ${name} has ${values.length} entries, ${first[0]} has ${first[1].length}.`,
      )
    }
  }
}

/** Epoch seconds or ISO dates must be strictly ascending. */
export function assertAscending(what: string, values: readonly (number | string)[]): void {
  for (let i = 1; i < values.length; i += 1) {
    if (!(values[i]! > values[i - 1]!)) {
      throw new ApiError(null, 'INVALID_RESPONSE', `The ${what} response is not in ascending order.`)
    }
  }
}
