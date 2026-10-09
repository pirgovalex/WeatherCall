import { describe, expect, it } from 'vitest'
import { MODEL_FEATURE_LABELS } from './featureLabels'
import { assessRisk, featureLabel, toDriverBars } from './risk'

const now = Date.parse('2026-10-28T10:00:00Z') // 12:00 in Plovdiv, 2026-10-28

describe('assessRisk', () => {
  it('accepts a live row for tomorrow', () => {
    const a = assessRisk({ split: 'live', targetDate: '2026-10-29' }, { uses_forecast_weather: true }, now)
    expect(a.isHistorical).toBe(false)
    expect(a.isTomorrow).toBe(true)
    expect(a.standInWeather).toBe(false)
  })

  it('flags every split other than live as historical', () => {
    for (const split of ['train', 'validation', 'test']) {
      expect(assessRisk({ split, targetDate: '2026-10-29' }, undefined, now).isHistorical).toBe(true)
    }
  })

  it('flags a live row whose target date is more than 3 days before today', () => {
    expect(assessRisk({ split: 'live', targetDate: '2026-10-25' }, undefined, now).isHistorical).toBe(false)
    const old = assessRisk({ split: 'live', targetDate: '2026-10-24' }, undefined, now)
    expect(old.isHistorical).toBe(true)
    expect(old.tooOld).toBe(true)
  })

  it('uses the Plovdiv date, not the UTC date, to decide how old a row is', () => {
    // 22:30 UTC on 27 Oct is already 28 Oct in Plovdiv.
    const lateNow = Date.parse('2026-10-27T22:30:00Z')
    expect(assessRisk({ split: 'live', targetDate: '2026-10-25' }, undefined, lateNow).tooOld).toBe(false)
    expect(assessRisk({ split: 'live', targetDate: '2026-10-24' }, undefined, lateNow).tooOld).toBe(true)
  })

  it('reports historical weather stand-in only for evaluation rows when the card says forecast weather is not used', () => {
    expect(assessRisk({ split: 'test', targetDate: '2025-12-31' }, { uses_forecast_weather: false }, now).standInWeather).toBe(true)
    expect(assessRisk({ split: 'test', targetDate: '2025-12-31' }, { uses_forecast_weather: true }, now).standInWeather).toBe(false)
    expect(assessRisk({ split: 'test', targetDate: '2025-12-31' }, undefined, now).standInWeather).toBe(false)
  })

  it('never reports a stand-in for live rows, which are scored with captured weather', () => {
    expect(assessRisk({ split: 'live', targetDate: '2026-10-29' }, { uses_forecast_weather: false }, now).standInWeather).toBe(false)
  })

  it('recognises a live row for today', () => {
    const a = assessRisk({ split: 'live', targetDate: '2026-10-28' }, undefined, now)
    expect(a.isToday).toBe(true)
    expect(a.isTomorrow).toBe(false)
    expect(a.isHistorical).toBe(false)
  })
})

describe('drivers', () => {
  it('scales bars by the largest absolute contribution and keeps the sign', () => {
    const bars = toDriverBars([
      { feature: 'pm10_lag_1d', contribution: 0.5, rank: 1 },
      { feature: 'wind_gusts_10m_forecast_max', contribution: -0.25, rank: 2 },
    ])
    expect(bars[0]).toMatchObject({ direction: 'raises', scaled: 1 })
    expect(bars[1]).toMatchObject({ direction: 'lowers', scaled: -0.5 })
  })

  it('gives readable names, with a fallback for unknown features', () => {
    expect(featureLabel('wind_gusts_10m_forecast_max')).toBe('Forecast maximum wind gusts')
    expect(featureLabel('soil_moisture_index')).toBe('Soil moisture index')
  })

  // The feature names of the real model (models/pm10_risk_logreg.json), with day t = today and t1 = tomorrow.
  const REAL_FEATURES = [
    'log_pm10_t', 'log_pm10_t_minus_1', 'log_pm10_mean3', 'log_pm10_mean7', 'log_pm25_t',
    'month_sin', 'month_cos', 'is_weekend', 'heating_season',
    'temp_mean_c_t', 'temp_range_c_t', 'log1p_precip_mm_t', 'wind_max_ms_t', 'radiation_mj_m2_t', 'rh_mean_pct_t', 'pressure_mean_hpa_t',
    'temp_mean_c_t1', 'temp_range_c_t1', 'log1p_precip_mm_t1', 'wind_max_ms_t1', 'radiation_mj_m2_t1', 'rh_mean_pct_t1', 'pressure_mean_hpa_t1',
  ]

  it('names every feature of the real model in plain English', () => {
    expect(REAL_FEATURES).toHaveLength(23)
    const labels = REAL_FEATURES.map(featureLabel)
    expect(new Set(labels).size).toBe(REAL_FEATURES.length) // no two features share a name
    REAL_FEATURES.forEach((feature, i) => {
      expect(labels[i], feature).not.toMatch(/[_]|\bt1\b|\blog\b|\bmean\d/i)
      expect(MODEL_FEATURE_LABELS[feature], feature).toBe(labels[i])
    })
  })

  it('uses the labels the review asked for', () => {
    expect(featureLabel('month_cos')).toBe('Time of year (cosine)')
    expect(featureLabel('month_sin')).toBe('Time of year (sine)')
    expect(featureLabel('wind_max_ms_t1')).toBe('Strongest wind tomorrow')
    expect(featureLabel('wind_max_ms_t')).toBe('Strongest wind today')
    expect(featureLabel('log_pm10_mean7')).toBe('Average PM10, last 7 days')
    expect(featureLabel('log_pm10_t_minus_1')).toBe('PM10 yesterday')
    expect(featureLabel('temp_mean_c_t1')).toBe('Average temperature tomorrow')
  })

  it('separates today from tomorrow for the weather features', () => {
    for (const base of ['temp_mean_c', 'temp_range_c', 'log1p_precip_mm', 'wind_max_ms', 'radiation_mj_m2', 'rh_mean_pct', 'pressure_mean_hpa']) {
      expect(featureLabel(`${base}_t`)).toMatch(/today$/)
      expect(featureLabel(`${base}_t1`)).toMatch(/tomorrow$/)
    }
  })

  it('still falls back to the raw name for a feature it does not know, and does not trip over object keys', () => {
    expect(featureLabel('log_pm10_mean30')).toBe('Log pm10 mean30')
    expect(featureLabel('constructor')).toBe('Constructor')
    expect(featureLabel('toString')).toBe('ToString')
  })
})
