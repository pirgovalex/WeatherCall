import { createMemo } from 'solid-js'
import { describeSeries, sourceLabel } from '../../transform/series'
import type { SeriesView } from '../../transform/series'
import { formatPlovdivDateTime, formatPlovdivDateTimeYear } from '../../transform/time'
import { formatNumber, formatUnit } from '../../transform/units'
import ChartFrame from './ChartFrame'
import type { ChartSpec } from './spec'
import UPlotChart from './UPlotChart'

const ZERO_BASED = new Set(['pm2_5', 'pm10', 'no2', 'wind_gusts_10m', 'precipitation'])

interface SeriesChartProps {
  view: SeriesView
  label: string
}

/** Hourly time series in Europe/Sofia time. Missing hours are gaps, never interpolated. */
export default function SeriesChart(props: SeriesChartProps) {
  const unit = () => formatUnit(props.view.unit)
  const source = () => sourceLabel(props.view.sourceType)

  const spec = createMemo<ChartSpec>(() => ({
    x: props.view.x,
    xMode: 'time-plovdiv',
    series: [{ id: 'value', label: props.label, values: props.view.y, color: 'series1', kind: 'line' }],
    yMin: ZERO_BASED.has(props.view.variable) ? 0 : undefined,
    formatX: formatPlovdivDateTime,
    formatY: (v) => formatNumber(v, 1),
  }))

  const summary = () => describeSeries(props.label, props.view)
  const id = () => `series-${props.view.variable}`

  return (
    <ChartFrame
      id={id()}
      title={`${props.label}, hourly`}
      axisNote={`${unit()}. Times in Europe/Sofia. ${source().short}: ${source().long.toLowerCase()}.`}
      summary={summary()}
      table={{
        caption: `${props.label} by hour in Europe/Sofia time`,
        columns: ['Time (Plovdiv)', `Value (${unit()})`],
        rows: props.view.x.map((t, i) => [
          formatPlovdivDateTimeYear(t),
          props.view.y[i] == null ? 'No value' : formatNumber(props.view.y[i] as number, 2),
        ]),
      }}
    >
      <UPlotChart spec={spec()} label={`${props.label}, hourly time series`} describedBy={`${id()}-summary`} />
    </ChartFrame>
  )
}
