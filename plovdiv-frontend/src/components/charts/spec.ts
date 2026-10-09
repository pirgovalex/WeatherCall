/**
 * Library-neutral description of a chart. Components build one of these from a
 * transformed payload; only UPlotChart knows about uPlot, so swapping the
 * library touches that one file.
 */
export type ColorToken = 'series1' | 'series2' | 'muted' | 'ink'

export interface ChartSeriesSpec {
  id: string
  label: string
  values: (number | null)[]
  color: ColorToken
  /**
   * line: a 2px line. markers: points only. edge: an invisible series that only
   * bounds a band. reference: a thin dashed guide.
   */
  kind: 'line' | 'markers' | 'edge' | 'reference'
  /** Draw a dot on every point of a line. */
  dots?: boolean
  /** Hollow dots, used for values that should not be read as full measurements. */
  hollow?: boolean
  /** Leave the series out of the tooltip. */
  silent?: boolean
}

export interface ChartBand {
  upper: string
  lower: string
  color: ColorToken
}

export type XMode = 'time-plovdiv' | 'time-utc' | 'value'

export interface ChartSpec {
  x: number[]
  xMode: XMode
  series: ChartSeriesSpec[]
  bands?: ChartBand[]
  /** Lower bound for the y axis; omit for an automatic range. */
  yMin?: number
  yMax?: number
  /** Fixed x range, used for value axes such as probabilities. */
  xMin?: number
  xMax?: number
  formatX: (x: number) => string
  formatY: (value: number) => string
  /** Extra tooltip line for a data index, such as the number of valid days. */
  note?: (index: number) => string | undefined
  /** Text for assistive technology when the cursor is moved by keyboard. */
  announce?: (index: number) => string
}

export interface LegendItem {
  label: string
  color: ColorToken
  shape: 'line' | 'band' | 'dots' | 'hollow' | 'dashed'
}

/** Legend entries derived from the spec: one per visible series, plus bands. */
export function legendFor(spec: ChartSpec, bandLabels: Record<string, string> = {}): LegendItem[] {
  const items: LegendItem[] = []
  for (const s of spec.series) {
    if (s.kind === 'edge') continue
    items.push({
      label: s.label,
      color: s.color,
      shape: s.kind === 'markers' ? (s.hollow ? 'hollow' : 'dots') : s.kind === 'reference' ? 'dashed' : 'line',
    })
  }
  for (const band of spec.bands ?? []) {
    items.push({ label: bandLabels[band.upper] ?? 'Uncertainty band', color: band.color, shape: 'band' })
  }
  return items
}
