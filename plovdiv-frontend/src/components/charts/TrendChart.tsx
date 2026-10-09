import { createMemo } from 'solid-js'
import { formatMonthKey } from '../../transform/time'
import { describeTrend, isLowCoverage } from '../../transform/trend'
import type { TrendView } from '../../transform/trend'
import { formatNumber, formatUnit } from '../../transform/units'
import ChartFrame from './ChartFrame'
import { legendFor } from './spec'
import type { ChartSpec } from './spec'
import UPlotChart from './UPlotChart'

interface TrendChartProps {
  view: TrendView
  stationLabel: string
}

/** Observed against weather-adjusted monthly mean, with the uncertainty band around the adjusted line. */
export default function TrendChart(props: TrendChartProps) {
  const unit = () => formatUnit(props.view.unit)

  const spec = createMemo<ChartSpec>(() => ({
    x: props.view.x,
    xMode: 'time-utc',
    series: [
      { id: 'hi', label: 'Upper bound', values: props.view.hi, color: 'series2', kind: 'edge', silent: true },
      { id: 'lo', label: 'Lower bound', values: props.view.lo, color: 'series2', kind: 'edge', silent: true },
      { id: 'observed', label: 'Observed', values: props.view.observed, color: 'series1', kind: 'line', dots: true },
      { id: 'adjusted', label: 'Weather-adjusted', values: props.view.adjusted, color: 'series2', kind: 'line', dots: true },
    ],
    bands: [{ upper: 'hi', lower: 'lo', color: 'series2' }],
    yMin: 0,
    formatX: (x) => formatMonthKey(new Date(x * 1000).toISOString().slice(0, 10)),
    formatY: (v) => formatNumber(v, 1),
    note: (i) => {
      const n = props.view.nDays[i] ?? 0
      if (n === 0) return 'No valid days this month.'
      const lo = props.view.lo[i]
      const hi = props.view.hi[i]
      const band = lo != null && hi != null ? `Band ${formatNumber(lo)} to ${formatNumber(hi)}. ` : ''
      return `${band}${n} valid day${n === 1 ? '' : 's'}${isLowCoverage(n) ? ' (low coverage)' : ''}.`
    },
  }))

  const legend = () => legendFor(spec(), { hi: 'Uncertainty of the weather-adjusted mean' })
  const summary = () => describeTrend(props.stationLabel, props.view)

  return (
    <ChartFrame
      id="trend"
      title={`${props.stationLabel}: PM10 monthly mean`}
      axisNote={`${unit()}. Each point is one calendar month.`}
      legend={legend()}
      summary={summary()}
      table={{
        caption: `${props.stationLabel} monthly PM10 means`,
        columns: ['Month', 'Observed', 'Weather-adjusted', 'Band low', 'Band high', 'Valid days'],
        rows: props.view.months.map((month, i) => {
          const cell = (v: number | null | undefined) => (v == null ? 'No value' : formatNumber(v, 1))
          return [
            formatMonthKey(month),
            cell(props.view.observed[i]),
            cell(props.view.adjusted[i]),
            cell(props.view.lo[i]),
            cell(props.view.hi[i]),
            String(props.view.nDays[i] ?? 0),
          ]
        }),
      }}
    >
      <UPlotChart spec={spec()} label={`${props.stationLabel} monthly PM10 trend`} describedBy="trend-summary" />
    </ChartFrame>
  )
}

