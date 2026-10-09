import { screen } from '@solidjs/testing-library'
import { describe, expect, it } from 'vitest'
import { renderApp } from '../test/render'

describe('Method page', () => {
  it('states the data sources with the wording used by the warehouse', async () => {
    renderApp('/method')
    expect(await screen.findByRole('heading', { name: 'Data sources and attribution' })).toBeInTheDocument()
    const text = document.body.textContent ?? ''
    expect(text).toContain('licensed under CC BY 4.0')
    expect(text).toContain('Contains modified Copernicus Atmosphere Monitoring Service information.')
    expect(text).toContain('European Environment Agency (EEA)')
  })

  it('writes out the model card fields, including the limitations', async () => {
    renderApp('/method')
    const prose = await screen.findByTestId('model-card-prose')
    expect(prose).toHaveTextContent('pm10_exceed_gbm_v1')
    expect(prose).toHaveTextContent('gradient boosted trees')
    expect(prose).toHaveTextContent('35%')
    expect(prose).toHaveTextContent('Trained on two stations in one city')
    expect(prose).toHaveTextContent('weather forecasts that are available when the score is issued')
  })

  it('explains how to read each page', async () => {
    renderApp('/method')
    expect(await screen.findByRole('heading', { name: 'How to read each page' })).toBeInTheDocument()
    for (const name of ['Now', 'History', 'Forecasts']) {
      expect(screen.getAllByRole('heading', { name, level: 3 }).length).toBeGreaterThan(0)
    }
  })
})
