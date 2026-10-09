/** One location for now: the manifest does not list locations (contract gap G2). */
export const LOCATION_ID = 'plovdiv'

/** The source type is always sent explicitly (contract gap G5). */
export const SOURCE_TYPE = 'analysis' as const

export const HOURLY_VARIABLE_ORDER = ['pm10', 'pm2_5', 'no2', 'temperature_2m', 'wind_gusts_10m', 'precipitation']

export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === '1'
