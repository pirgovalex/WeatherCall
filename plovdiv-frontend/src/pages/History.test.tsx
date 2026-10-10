import { screen, waitFor, within } from '@solidjs/testing-library'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/testServer'
import { setScenario } from '../mocks/scenario'
import { renderApp } from '../test/render'

const api = 'http://api.test'

describe('History page', () => {
  it('shows the observed and weather-adjusted lines with a legend, summary and table', async () => {
    renderApp('/history')
    const chart = await screen.findByRole('img', { name: 'Kamenitsa monthly PM10 trend' })
    expect(chart).toHaveAttribute('data-uplot', 'true')
    const figure = chart.closest('figure')!
    const legend = within(figure).getByRole('list', { name: 'Chart legend' })
    expect(legend).toHaveTextContent('Observed')
    expect(legend).toHaveTextContent('Weather-adjusted')
    expect(legend).toHaveTextContent('Uncertainty of the weather-adjusted mean')
    expect(figure).toHaveTextContent('Oct 2026: observed')
    expect(chart).toHaveAttribute('aria-describedby', 'trend-summary')
    expect(document.getElementById('trend-summary')).toBeInTheDocument()
  })

  it('explains the difference between the two lines in plain language', async () => {
    renderApp('/history')
    await screen.findByRole('img', { name: 'Kamenitsa monthly PM10 trend' })
    const text = document.body.textContent ?? ''
    expect(text).toContain('Observed is the average of the valid daily means')
    expect(text).toContain('after the estimated effect of weather has been removed')
  })

  it('switches between the background and the traffic station', async () => {
    const user = userEvent.setup()
    renderApp('/history')
    await screen.findByRole('img', { name: 'Kamenitsa monthly PM10 trend' })
    expect(screen.getByText(/Kamenitsa is a background station/)).toBeInTheDocument()
    const trendSection = screen.getByRole('region', { name: 'Monthly PM10 trend' })
    await user.click(within(trendSection).getByRole('button', { name: 'Trakia (traffic)' }))
    const chart = await screen.findByRole('img', { name: 'Trakia monthly PM10 trend' })
    expect(screen.getByText(/Trakia is a traffic station/)).toBeInTheDocument()
    // The month without valid days is a gap in the summary and a row in the table.
    expect(chart.closest('figure')).toHaveTextContent('1 month had no valid days')
    const table = within(chart.closest('figure')!).getByRole('table', { hidden: true })
    expect(within(table).getByText('Mar 2026').closest('tr')).toHaveTextContent('No value')
  })

  it('asks for the daily means of the chosen station under its daily location', async () => {
    const user = userEvent.setup()
    const locations: string[] = []
    server.use(
      http.get(`${api}/api/v1/aq/daily`, ({ request }) => {
        // Record the location and fall through to the default handler.
        locations.push(new URL(request.url).searchParams.get('location') ?? '')
        return undefined
      }),
    )
    renderApp('/history')
    await screen.findByRole('img', { name: 'PM10 daily mean time series' })
    const daily = screen.getByRole('region', { name: 'Daily means' })
    await user.click(within(daily).getByRole('button', { name: 'Trakia (traffic)' }))
    await waitFor(() => expect(within(daily).getByRole('button', { name: 'Trakia (traffic)' })).toHaveAttribute('aria-pressed', 'true'))
    // The fixture catalogue gives both stations the city location and no daily location of their own.
    expect(new Set(locations)).toEqual(new Set(['plovdiv']))
  })

  it('shows the daily chart with the invalid day called out', async () => {
    renderApp('/history')
    const chart = await screen.findByRole('img', { name: 'PM10 daily mean time series' })
    const figure = chart.closest('figure')!
    expect(figure).toHaveTextContent('1 day has fewer than 18 valid hours')
    const row = within(figure).getByText('23 Oct 2026').closest('tr')!
    expect(row).toHaveTextContent('17')
    expect(row).toHaveTextContent('No')
  })

  it('shows a 503 message for the trend when the mart is not declared', async () => {
    setScenario('unavailable')
    renderApp('/history')
    await waitFor(() => {
      const status = screen.getAllByRole('status')
      expect(status.some((s) => /not available in this data run/.test(s.textContent ?? ''))).toBe(true)
    })
    expect(document.body).toHaveTextContent('mart_aq_trend_monthly')
  })

  it('shows the envelope for a failed trend request', async () => {
    server.use(http.get(`${api}/api/v1/aq/trend`, () => HttpResponse.json({ error: { code: 'NOT_FOUND', message: "Station 'BG0051A' was not found." } }, { status: 404 })))
    renderApp('/history')
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent("Station 'BG0051A' was not found."))
  })

  it('shows an empty state when a station has no monthly rows', async () => {
    setScenario('empty')
    renderApp('/history')
    expect(await screen.findByText('No monthly means for this station')).toBeInTheDocument()
  })
})
