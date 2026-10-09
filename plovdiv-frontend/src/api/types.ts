import type { components, paths } from './schema'

type Schemas = components['schemas']

export type MetaResponse = Schemas['MetaResponse']
export type SeriesResponse = Schemas['SeriesResponse']
export type DailyAqResponse = Schemas['DailyAqResponse']
export type AqTrendResponse = Schemas['AqTrendResponse']
export type AqRiskResponse = Schemas['AqRiskResponse']
export type AqRiskDrivers = Schemas['AqRiskDrivers']
export type ModelCardResponse = Schemas['ModelCardResponse']
export type ModelCalibrationResponse = Schemas['ModelCalibrationResponse']
export type StationEntry = Schemas['StationEntry']
export type ErrorEnvelope = Schemas['ErrorEnvelope']

export type SeriesParams = NonNullable<paths['/api/v1/series']['get']['parameters']['query']>
export type DailyParams = NonNullable<paths['/api/v1/aq/daily']['get']['parameters']['query']>
export type TrendParams = NonNullable<paths['/api/v1/aq/trend']['get']['parameters']['query']>
export type RiskParams = NonNullable<paths['/api/v1/aq/risk']['get']['parameters']['query']>
export type ModelParams = NonNullable<paths['/api/v1/model/card']['get']['parameters']['query']>

export type SourceType = NonNullable<SeriesParams['source_type']>
export type Pollutant = DailyParams['pollutant']
export type RiskSplit = NonNullable<RiskParams['split']>
