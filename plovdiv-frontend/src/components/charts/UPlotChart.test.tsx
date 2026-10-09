import { render, screen } from '@solidjs/testing-library'
import { describe, expect, it } from 'vitest'
import UPlotChart, { withAlpha } from './UPlotChart'
import type { ChartSpec } from './spec'

const spec: ChartSpec = {
  x: [0, 3600, 7200],
  xMode: 'time-plovdiv',
  series: [{ id: 'a', label: 'PM10', values: [1, null, 3], color: 'series1', kind: 'line' }],
  formatX: (x) => String(x),
  formatY: (v) => String(v),
}

describe('UPlotChart', () => {
  it('exposes the chart as a labelled, focusable image', () => {
    render(() => <UPlotChart spec={spec} label="PM10 hourly" describedBy="s" />)
    const chart = screen.getByRole('img', { name: 'PM10 hourly' })
    expect(chart).toHaveAttribute('tabindex', '0')
    expect(chart).toHaveAttribute('aria-describedby', 's')
    expect(chart).toHaveAttribute('data-uplot', 'true')
  })

  it('converts hex colours for translucent fills', () => {
    expect(withAlpha('#2a78d6', 0.16)).toBe('rgba(42, 120, 214, 0.16)')
    expect(withAlpha('rgb(1,2,3)', 0.5)).toBe('rgb(1,2,3)')
  })
})
