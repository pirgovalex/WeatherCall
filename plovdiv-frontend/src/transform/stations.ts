import type { MetaResponse } from '../api/types'

export interface Station {
  id: string
  label: string
  stationType: string
  areaType: string
}

/** Stations from the manifest catalogue, in catalogue order. Empty when none is published. */
export function stationsFrom(meta: MetaResponse | undefined): Station[] {
  const catalogue = meta?.stations
  if (!catalogue) return []
  return Object.entries(catalogue).map(([id, s]) => ({
    id,
    label: s.label,
    stationType: s.station_type,
    areaType: s.area_type,
  }))
}

export function stationOptionLabel(station: Station): string {
  return `${station.label} (${station.stationType})`
}
