import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../mocks/testServer'
import { ApiError, buildQuery } from './client'
import { getAqRisk, getMeta, getSeries } from './endpoints'

const base = 'http://api.test'

describe('buildQuery', () => {
  it('skips undefined values and sorts keys', () => {
    expect(buildQuery({ b: 2, a: 'x', c: undefined, d: null })).toBe('?a=x&b=2')
    expect(buildQuery({})).toBe('')
  })
})

describe('error envelope', () => {
  it('turns a 404 envelope into an ApiError with its code and message', async () => {
    const error = await getAqRisk({ station_id: 'NOPE', latest: true }).catch((e) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(404)
    expect(error.code).toBe('NOT_FOUND')
    expect(error.message).toContain('NOPE')
    expect(error.isClientError).toBe(true)
  })

  it('reports 422 for a missing parameter', async () => {
    const error = await getSeries({ variable: 'pm10', location: 'plovdiv', from: '', to: '', resolution: 'hourly' }).catch((e) => e)
    expect(error.status).toBe(422)
    expect(error.code).toBe('VALIDATION_ERROR')
  })

  it('reports 503 as unavailable', async () => {
    server.use(
      http.get(`${base}/api/v1/model/card`, () =>
        HttpResponse.json({ error: { code: 'SERVICE_UNAVAILABLE', message: 'mart_model_card is not declared.' } }, { status: 503 }),
      ),
    )
    const { getModelCard } = await import('./endpoints')
    const error = await getModelCard({ model_id: 'x' }).catch((e) => e)
    expect(error.isUnavailable).toBe(true)
    expect(error.code).toBe('SERVICE_UNAVAILABLE')
  })

  it('maps a failure without an envelope to UNKNOWN', async () => {
    server.use(http.get(`${base}/api/v1/meta`, () => new HttpResponse('Bad gateway', { status: 502 })))
    const error = await getMeta().catch((e) => e)
    expect(error.code).toBe('UNKNOWN')
    expect(error.status).toBe(502)
  })

  it('maps a dropped connection to NETWORK', async () => {
    server.use(http.get(`${base}/api/v1/meta`, () => HttpResponse.error()))
    const error = await getMeta().catch((e) => e)
    expect(error.code).toBe('NETWORK')
    expect(error.status).toBeNull()
  })
})

describe('response checks', () => {
  it('rejects a series whose arrays differ in length', async () => {
    server.use(
      http.get(`${base}/api/v1/series`, () =>
        HttpResponse.json({ variable: 'pm10', unit: 'ug/m3', location_id: 'plovdiv', resolution: 'hourly', timestamps: [1, 2], values: [1] }),
      ),
    )
    const error = await getSeries({ variable: 'pm10', location: 'plovdiv', from: 'a', to: 'b', resolution: 'hourly' }).catch((e) => e)
    expect(error.code).toBe('INVALID_RESPONSE')
  })

  it('rejects a series that is not ascending', async () => {
    server.use(
      http.get(`${base}/api/v1/series`, () =>
        HttpResponse.json({ variable: 'pm10', unit: 'ug/m3', location_id: 'plovdiv', resolution: 'hourly', timestamps: [2, 1], values: [1, 2] }),
      ),
    )
    const error = await getSeries({ variable: 'pm10', location: 'plovdiv', from: 'a', to: 'b', resolution: 'hourly' }).catch((e) => e)
    expect(error.code).toBe('INVALID_RESPONSE')
  })
})
