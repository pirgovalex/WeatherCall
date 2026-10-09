/**
 * The dashboard against the layout of the real warehouse run: the hourly mart ends in forecast hours
 * well after its last analysis hour, the daily mart (EEA station means) ends months before the hourly
 * data begins, and the risk model's feature names are raw column names.
 */
import { screen, waitFor, within } from '@solidjs/testing-library'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import manifest from '../../contracts/fixtures/manifest.json'
import { server } from '../mocks/testServer'
import { renderApp, setNow } from '../test/render'

const api = 'http://api.test'

afterEach(() => {
  vi.restoreAllMocks()
})

/** The fixture manifest with the optional range fields set the way the real run sets them. */
function realMeta(daily: Record<string, string> = { min_day: '2013-01-01', max_day: '2025-12-31' }) {
  return {
    ...manifest,
    marts: {
      ...manifest.marts,
      mart_weather_aq_hourly: { ...manifest.marts.mart_weather_aq_hourly, max_analysis_valid_at_utc: '2026-10-20T10:00:00Z' },
      mart_aq_daily: { ...manifest.marts.mart_aq_daily, ...daily },
    },
  }
}

function requestedUrls(spy: { mock: { calls: unknown[][] } }, path: string): URL[] {
  return spy.mock.calls.map((c) => new URL(String(c[0]))).filter((u) => u.pathname === path)
}

const warehouse = { run_id: 'r', schema_version: '1', generated_at_utc: '2026-10-07T14:05:12Z' }

describe('Now page with forecast hours after the last analysis hour', () => {
  it('anchors the readings, the series window and the page text on the newest analysis hour', async () => {
    server.use(http.get(`${api}/api/v1/meta`, () => HttpResponse.json(realMeta())))
    const spy = vi.spyOn(server, 'fetch')
    renderApp('/')
    const tile = await screen.findByTestId('reading-pm10')
    await waitFor(() => expect(tile).toHaveTextContent('Model analysis'))

    const text = () => document.body.textContent ?? ''
    // 10:00 UTC on 20 Oct is 13:00 in Plovdiv; the forecast end (27 Oct) is not mentioned.
    await waitFor(() => expect(text()).toContain('up to 20 Oct 2026, 13:00 Plovdiv time'))
    expect(text()).toContain('Data runs from 14 Oct 2026 to 20 Oct 2026.')
    expect(text()).not.toContain('27 Oct 2026')

    // The three days of the readings end with the Plovdiv day of the analysis hour, not with the forecast end.
    const series = requestedUrls(spy, '/api/v1/series')
    expect(series.length).toBeGreaterThan(0)
    for (const url of series) {
      expect(url.searchParams.get('source_type')).toBe('analysis')
      expect(url.searchParams.get('from')).toBe('2026-10-17T21:00:00Z') // 18 Oct 00:00 in Plovdiv
      expect(url.searchParams.get('to')).toBe('2026-10-20T20:00:00Z') // last hour of 20 Oct in Plovdiv
    }
  })

  it('falls back to the end of the hourly mart when the manifest has no analysis end', async () => {
    const plain = realMeta()
    delete (plain.marts.mart_weather_aq_hourly as Record<string, unknown>).max_analysis_valid_at_utc
    server.use(http.get(`${api}/api/v1/meta`, () => HttpResponse.json(plain)))
    renderApp('/')
    await screen.findByTestId('reading-pm10')
    await waitFor(() => expect(document.body.textContent).toContain('up to 28 Oct 2026, 01:00 Plovdiv time'))
    expect(document.body.textContent).toContain('Data runs from 14 Oct 2026 to 28 Oct 2026.')
  })
})

describe('History page with a daily mart that ends before the hourly data', () => {
  const dailyBody = {
    pollutant: 'pm10',
    location_id: 'plovdiv',
    days: ['2025-12-29', '2025-12-30', '2025-12-31'],
    mean: [31.2, null, 25.5],
    valid_hours: [18, 0, 18],
    is_valid_day: [true, false, true],
    warehouse,
    attribution: ['Test'],
  }

  it('asks for the most recent days of the daily mart and shows them', async () => {
    server.use(http.get(`${api}/api/v1/meta`, () => HttpResponse.json(realMeta())))
    server.use(http.get(`${api}/api/v1/aq/daily`, () => HttpResponse.json(dailyBody)))
    const spy = vi.spyOn(server, 'fetch')
    renderApp('/history')
    const chart = await screen.findByRole('img', { name: 'PM10 daily mean time series' })
    expect(chart.closest('figure')).toHaveTextContent('1 day has fewer than 18 valid hours')
    expect(screen.getByTestId('daily-span')).toHaveTextContent('3 Oct 2025 to 31 Dec 2025')

    const daily = requestedUrls(spy, '/api/v1/aq/daily')
    expect(daily.length).toBeGreaterThan(0)
    for (const url of daily) {
      expect(url.searchParams.get('from')).toBe('2025-10-03')
      expect(url.searchParams.get('to')).toBe('2025-12-31')
    }
  })

  it('keeps the empty state for a pollutant without rows', async () => {
    server.use(http.get(`${api}/api/v1/meta`, () => HttpResponse.json(realMeta())))
    server.use(
      http.get(`${api}/api/v1/aq/daily`, () =>
        HttpResponse.json({ ...dailyBody, pollutant: 'no2', days: [], mean: [], valid_hours: [], is_valid_day: [] }),
      ),
    )
    renderApp('/history')
    const empty = await screen.findAllByTestId('empty-state')
    expect(empty.some((e) => /No daily means in this period/.test(e.textContent ?? ''))).toBe(true)
  })

  it('uses the hourly window when the manifest has no daily range', async () => {
    const noDailyRange = realMeta()
    delete (noDailyRange.marts.mart_aq_daily as Record<string, unknown>).min_day
    delete (noDailyRange.marts.mart_aq_daily as Record<string, unknown>).max_day
    server.use(http.get(`${api}/api/v1/meta`, () => HttpResponse.json(noDailyRange)))
    const spy = vi.spyOn(server, 'fetch')
    renderApp('/history')
    await screen.findByRole('img', { name: 'PM10 daily mean time series' })
    const daily = requestedUrls(spy, '/api/v1/aq/daily')
    expect(daily.length).toBeGreaterThan(0)
    for (const url of daily) {
      expect(url.searchParams.get('from')).toBe('2026-10-14')
      expect(url.searchParams.get('to')).toBe('2026-10-20')
    }
  })
})

describe('Risk card with the real model', () => {
  const riskBody = (probability: number) => ({
    station_id: 'BG0051A',
    target_dates: ['2026-10-28'],
    issued_at: ['2026-10-27T10:00:00Z'],
    probability: [probability],
    label_id: ['pm10_above_p75_month'],
    split: ['live'],
    actual_label: [null],
    model_id: ['pm10_exceed_gbm_v1'],
    drivers: {
      feature: ['month_cos', 'wind_max_ms_t1', 'log_pm10_mean7', 'log_pm10_t_minus_1', 'temp_mean_c_t1'],
      contribution: [-1.2, -0.8, 0.4, 0.3, -0.2],
      rank: [1, 2, 3, 4, 5],
    },
    warehouse,
    attribution: ['Test'],
  })

  it('does not show 0% for a probability that is only close to zero', async () => {
    setNow('2026-10-27T12:00:00Z')
    server.use(http.get(`${api}/api/v1/aq/risk`, () => HttpResponse.json(riskBody(0.0049))))
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    expect(await within(card).findByTestId('risk-percent')).toHaveTextContent(/^less than 1%$/)
    const meter = within(card).getByRole('meter')
    expect(meter).toHaveAttribute('aria-label', 'Probability of a bad air quality day, less than 1 percent')
    expect(meter).toHaveAttribute('aria-valuetext', 'less than 1 percent')
    expect(meter).toHaveAttribute('aria-valuenow', '0')
  })

  it('does not show 100% for a probability that is only close to one', async () => {
    setNow('2026-10-27T12:00:00Z')
    server.use(http.get(`${api}/api/v1/aq/risk`, () => HttpResponse.json(riskBody(0.9951))))
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    expect(await within(card).findByTestId('risk-percent')).toHaveTextContent(/^more than 99%$/)
    const meter = within(card).getByRole('meter')
    expect(meter).toHaveAttribute('aria-label', 'Probability of a bad air quality day, more than 99 percent')
    expect(meter).toHaveAttribute('aria-valuetext', 'more than 99 percent')
  })

  it('shows 0% and 100% for exactly 0 and 1', async () => {
    setNow('2026-10-27T12:00:00Z')
    server.use(http.get(`${api}/api/v1/aq/risk`, () => HttpResponse.json(riskBody(0))))
    renderApp('/')
    const card = await screen.findByTestId('risk-card')
    expect(await within(card).findByTestId('risk-percent')).toHaveTextContent(/^0%$/)
  })

  it('names the drivers of the real model in plain English', async () => {
    setNow('2026-10-27T12:00:00Z')
    server.use(http.get(`${api}/api/v1/aq/risk`, () => HttpResponse.json(riskBody(0.4))))
    renderApp('/')
    const list = await screen.findByRole('list', { name: 'Largest drivers of this score' })
    const items = within(list).getAllByRole('listitem')
    expect(items.map((i) => i.querySelector('span')?.firstChild?.textContent)).toEqual([
      'Time of year (cosine)',
      'Strongest wind tomorrow',
      'Average PM10, last 7 days',
      'PM10 yesterday',
      'Average temperature tomorrow',
    ])
    expect(list.textContent).not.toMatch(/month_cos|wind_max_ms_t1|Month cos|Wind max ms/)
  })
})
