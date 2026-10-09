import { describe, expect, it } from 'vitest'
import { formatProbability } from './units'

describe('formatProbability', () => {
  it('writes an ordinary probability as a whole percent', () => {
    expect(formatProbability(0.23)).toEqual({ text: '23%', spoken: '23 percent', percent: 23 })
    expect(formatProbability(0.5)).toMatchObject({ text: '50%', percent: 50 })
    expect(formatProbability(0.994)).toMatchObject({ text: '99%', percent: 99 })
    expect(formatProbability(0.01)).toMatchObject({ text: '1%', percent: 1 })
  })

  it('keeps 0% and 100% for exactly 0 and exactly 1', () => {
    expect(formatProbability(0)).toEqual({ text: '0%', spoken: '0 percent', percent: 0 })
    expect(formatProbability(1)).toEqual({ text: '100%', spoken: '100 percent', percent: 100 })
  })

  it('does not round a small probability down to zero', () => {
    expect(formatProbability(0.0049)).toEqual({ text: 'less than 1%', spoken: 'less than 1 percent', percent: 0 })
    expect(formatProbability(0.000018)).toMatchObject({ text: 'less than 1%' })
  })

  it('does not round a probability close to one up to certainty', () => {
    expect(formatProbability(0.9951)).toEqual({ text: 'more than 99%', spoken: 'more than 99 percent', percent: 100 })
    expect(formatProbability(0.993107)).toMatchObject({ text: '99%' })
    expect(formatProbability(0.99999)).toMatchObject({ text: 'more than 99%' })
  })

  it('treats the rounding boundary the way Math.round does', () => {
    expect(formatProbability(0.005)).toMatchObject({ text: '1%' })
    expect(formatProbability(0.995)).toMatchObject({ text: 'more than 99%' })
  })
})
