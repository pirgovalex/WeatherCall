import { screen, within } from '@solidjs/testing-library'
import { describe, expect, it } from 'vitest'
import { setScenario } from '../mocks/scenario'
import { renderApp } from '../test/render'

describe('Forecasts page', () => {
  it('draws the reliability chart with the diagonal in its legend and a bin table', async () => {
    renderApp('/forecasts')
    const chart = await screen.findByRole('img', { name: 'Reliability diagram' })
    const figure = chart.closest('figure')!
    expect(within(figure).getByRole('list', { name: 'Chart legend' })).toHaveTextContent('Perfect calibration')
    expect(figure).toHaveTextContent('1 bin has no test days and is left out')
    expect(figure).toHaveTextContent('Bins with fewer than 10 test days are noisy')
    const table = within(figure).getByRole('table', { hidden: true })
    expect(within(table).getByText('90% to 100%').closest('tr')).toHaveTextContent('No test days')
  })

  it('compares the model with the climatology and persistence baselines', async () => {
    renderApp('/forecasts')
    const table = await screen.findByTestId('performance-table')
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(4)
    expect(rows[1]).toHaveTextContent('This model')
    expect(rows[1]).toHaveTextContent('0.842')
    expect(rows[1]).toHaveTextContent('0.611')
    expect(rows[1]).toHaveTextContent('0.118')
    expect(rows[1]).toHaveTextContent('0.58')
    expect(rows[1]).toHaveTextContent('0.71')
    expect(rows[2]).toHaveTextContent('Climatology baseline')
    expect(rows[2]).toHaveTextContent('0.676')
    expect(rows[2]).toHaveTextContent('0.166 lower than the model')
    expect(rows[3]).toHaveTextContent('Persistence baseline')
    expect(rows[3]).toHaveTextContent('0.771')
    expect(rows[3]).toHaveTextContent('0.071 lower than the model')
  })

  it('explains that verification will fill in as captures accumulate, with no data', async () => {
    renderApp('/forecasts')
    const empty = await screen.findByTestId('verification-empty')
    expect(empty).toHaveTextContent('fills in as forecast captures accumulate')
    expect(within(empty).queryByRole('table')).toBeNull()
    expect(empty.querySelector('canvas')).toBeNull()
  })

  it('keeps the verification section when the model cannot be identified', async () => {
    setScenario('error')
    renderApp('/forecasts')
    expect(await screen.findByTestId('verification-empty')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('INTERNAL_ERROR')
  })
})
