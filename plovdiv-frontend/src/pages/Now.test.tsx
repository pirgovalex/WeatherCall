import { screen, waitFor, within } from '@solidjs/testing-library'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/testServer'
import { setScenario } from '../mocks/scenario'
import { renderApp, setNow } from '../test/render'

const api = 'http://api.test'

describe('Now page: tomorrow\'s air quality risk', () => {
  it('shows a live row for tomorrow as a forecast, with the label from the model card', async () => {
    setNow('2026-10-27T12:00:00Z') // tomorrow in Plovdiv is 28 Oct, the target date of the live row
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    expect(await within(card).findByRole('heading', { name: "Tomorrow's air quality risk" })).toBeInTheDocument()
    expect(await within(card).findByTestId('risk-percent')).toHaveTextContent('23%')
    expect(await within(card).findByTestId('label-text')).toHaveTextContent('Daily mean PM10 above the EU daily limit value of 50 ug/m3')
    expect(within(card).queryByTestId('historical-notice')).toBeNull()
    expect(within(card).queryByTestId('stand-in-notice')).toBeNull()
    expect(within(card).getByText('pm10_exceed_gbm_v1')).toBeInTheDocument()
    expect(within(card).getByText('28 Oct 2026')).toBeInTheDocument()
    expect(within(card).getByRole('meter')).toHaveAttribute('aria-valuenow', '23')
    expect(within(card).getByTestId('threshold-note')).toHaveTextContent('35%')
  })

  it('draws drivers as signed bars with words for the direction', async () => {
    setNow('2026-10-27T12:00:00Z')
    renderApp('/')
    const list = await screen.findByRole('list', { name: 'Largest drivers of this score' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(5)
    expect(items[0]).toHaveTextContent('PM10 average of the past 7 days')
    expect(items[0]).toHaveTextContent('+0.62')
    expect(items[0]).toHaveTextContent('raises the risk')
    expect(items[1]).toHaveTextContent('−0.27')
    expect(items[1]).toHaveTextContent('lowers the risk')
  })

  it('warns when the row is a historical evaluation row, and does not call it tomorrow\'s forecast', async () => {
    setNow('2026-10-27T12:00:00Z')
    setScenario('historical')
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    const notice = await within(card).findByTestId('historical-notice')
    expect(notice).toHaveTextContent('Historical evaluation row. Live forecasts are not running yet.')
    expect(notice).toHaveTextContent('"test" split')
    expect(within(card).queryByRole('heading', { name: "Tomorrow's air quality risk" })).toBeNull()
    expect(within(card).getByRole('heading', { level: 2 })).toHaveTextContent('historical')
  })

  it('warns when a live row is more than 3 days older than today in Europe/Sofia', async () => {
    setNow('2026-11-05T12:00:00Z') // live row targets 28 Oct, eight days earlier
    renderApp('/')
    const notice = await screen.findByTestId('historical-notice')
    expect(notice).toHaveTextContent('more than 3 days before today')
    expect(notice).not.toHaveTextContent('"live" split')
  })

  it('does not warn for a live row three days old', async () => {
    setNow('2026-10-31T12:00:00Z')
    renderApp('/')
    await screen.findByTestId('risk-percent')
    expect(screen.queryByTestId('historical-notice')).toBeNull()
  })

  it('calls a live row for today "today" and explains the one-day station delay', async () => {
    setNow('2026-10-28T08:00:00Z') // today in Plovdiv is 28 Oct, the target date of the live row
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    expect(await within(card).findByRole('heading', { name: "Today's air quality risk" })).toBeInTheDocument()
    expect(await within(card).findByTestId('today-notice')).toHaveTextContent('28 Oct 2026')
    expect(within(card).queryByTestId('not-tomorrow-notice')).toBeNull()
    expect(within(card).queryByTestId('historical-notice')).toBeNull()
  })

  it('says the row is not for tomorrow when it is live but for another date', async () => {
    setNow('2026-10-29T12:00:00Z')
    renderApp('/')
    expect(await screen.findByTestId('not-tomorrow-notice')).toHaveTextContent('28 Oct 2026')
  })

  it('says historical weather stood in for an evaluation row when the model card says no forecast weather is used', async () => {
    setNow('2026-10-27T12:00:00Z')
    setScenario('historical')
    server.use(
      http.get(`${api}/api/v1/model/card`, ({ request }) => {
        const id = new URL(request.url).searchParams.get('model_id')
        return HttpResponse.json({
          model_id: id, algorithm: 'logistic_regression', trained_at: '2025-06-30T20:00:00Z',
          windows: { train_start: '2022-01-01', train_end: '2024-12-31', validation_start: '2025-01-01', validation_end: '2025-06-30', test_start: '2025-07-01', test_end: '2025-12-31' },
          label_id: 'l', label_text: 'Daily mean PM10 above 50 ug/m3', features: ['pm10_lag_1d'], uses_forecast_weather: false, threshold: 0.4,
          metrics: { roc_auc_test: 0.78, pr_auc_test: 0.5, brier_test: 0.14, precision_test: 0.55, recall_test: 0.62 },
          baselines: { roc_auc_test_climatology: 0.67, roc_auc_test_persistence: null }, limitations: 'x',
          warehouse: { run_id: 'r', schema_version: '1', generated_at_utc: '2026-10-07T14:05:12Z' }, attribution: [],
        })
      }),
    )
    renderApp('/')
    expect(await screen.findByTestId('stand-in-notice')).toHaveTextContent('historical weather as a stand-in')
  })

  it('switches station', async () => {
    setNow('2026-10-27T12:00:00Z')
    const user = userEvent.setup()
    renderApp('/')
    expect(await screen.findByTestId('risk-percent')).toHaveTextContent('23%')
    await user.click(screen.getByRole('button', { name: 'Trakia (traffic)' }))
    await waitFor(() => expect(screen.getByTestId('risk-percent')).toHaveTextContent('77%'))
    expect(screen.getByTestId('threshold-note')).toHaveTextContent('at or above')
  })

  it('shows the 503 message when the risk mart is not published', async () => {
    server.use(
      http.get(`${api}/api/v1/aq/risk`, () =>
        HttpResponse.json({ error: { code: 'SERVICE_UNAVAILABLE', message: 'The mart mart_aq_risk_daily is not declared in the manifest.' } }, { status: 503 }),
      ),
    )
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    await waitFor(() => expect(within(card).getByRole('status')).toHaveTextContent('not available in this data run'))
    expect(card).toHaveTextContent('mart_aq_risk_daily')
  })

  it('says there is nothing to show when the run lists no stations', async () => {
    setScenario('unavailable')
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    expect(await within(card).findByText(/does not list any stations/)).toBeInTheDocument()
  })

  it('shows the error envelope when the request fails', async () => {
    setScenario('error')
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    await waitFor(() => expect(within(card).getByRole('alert')).toHaveTextContent('INTERNAL_ERROR'))
  })

  it('shows an empty state when the station has no rows', async () => {
    setScenario('empty')
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    expect(await within(card).findByTestId('empty-state')).toHaveTextContent('No risk score for this station')
  })
})

describe('Now page: readings and footer', () => {
  it('shows the newest valid reading with its Plovdiv time and source type', async () => {
    renderApp('/')
    const tile = await screen.findByTestId('reading-pm10')
    await waitFor(() => expect(tile).toHaveTextContent('Model analysis'))
    expect(tile).toHaveTextContent(/µg\/m³/)
    expect(tile).toHaveTextContent('Plovdiv')
  })

  it('shows the attribution, run id and generation time from /meta in the footer', async () => {
    renderApp('/')
    const footer = await screen.findByRole('contentinfo')
    await waitFor(() => expect(footer).toHaveTextContent('Copernicus Atmosphere Monitoring Service (CAMS)'))
    expect(footer).toHaveTextContent('2026-10-07T14-00-00Z-abc123')
    expect(footer).toHaveTextContent('7 Oct 2026, 17:05')
  })

  it('shows an error with a retry when /meta fails', async () => {
    server.use(http.get(`${api}/api/v1/meta`, () => HttpResponse.json({ error: { code: 'INTERNAL_ERROR', message: 'boom' } }, { status: 500 })))
    renderApp('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })

  it('has landmarks and a keyboard-reachable navigation', async () => {
    renderApp('/')
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
    const links = within(screen.getByRole('navigation', { name: 'Main' })).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['Now', 'History', 'Forecasts', 'Method'])
    expect(links[0]).toHaveAttribute('aria-current', 'page')
  })
})
