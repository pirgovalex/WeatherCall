import { createMemo } from 'solid-js'
import type { DailyAqResponse } from '../../api/types'
import { dateKeyToEpoch, formatDateKey, formatDateKeyShort } from '../../transform/time'
import { formatNumber, formatUnit } from '../../transform/units'
import ChartFrame from './ChartFrame'
import { legendFor } from './spec'
import type { ChartSpec } from './spec'
import UPlotChart from './UPlotChart'

/** PM10 daily limit value that defines a bad day in the model label (from the model card text). */
export const PM10_DAILY_LIMIT = 50

interface DailyChartProps {
  data: DailyAqResponse
  label: string
  unit: string
}

export function describeDaily(data: DailyAqResponse, label: string, unit: string): string {
  const valid = data.mean.filter((m, i) => m !== null && data.is_valid_day[i]) as number[]
  const incomplete = data.is_valid_day.filter((v) => !v).length
  if (data.days.length === 0) return `${label}: no days in the selected range.`
  const first = formatDateKey(data.days[0]!)
  const last = formatDateKey(data.days[data.days.length - 1]!)
  const range = valid.length ? ` Valid daily means range from ${formatNumber(Math.min(...valid))} to ${formatNumber(Math.max(...valid))} ${unit}.` : ''
  const note = incomplete > 0 ? ` ${incomplete} day${incomplete === 1 ? ' has' : 's have'} fewer than 18 valid hours and ${incomplete === 1 ? 'is' : 'are'} shown as hollow markers.` : ''
  return `${label}, daily means from ${first} to ${last}.${range}${note}`
}

/**
 * Daily means on the EEA day (UTC+1). Days with fewer than 18 valid hours are
 * not valid days under the EEA rule: they are drawn as hollow markers and are
 * not joined into the line.
 */
export default function DailyChart(props: DailyChartProps) {
  const unit = () => formatUnit(props.unit)
  const isPm10 = () => props.data.pollutant === 'pm10'

  const spec = createMemo<ChartSpec>(() => {
    const d = props.data
    const validMeans = d.mean.map((m, i) => (d.is_valid_day[i] ? m : null))
    const incompleteMeans = d.mean.map((m, i) => (d.is_valid_day[i] ? null : m))
    const series: ChartSpec['series'] = [
      { id: 'valid', label: 'Daily mean (valid day)', values: validMeans, color: 'series1', kind: 'line', dots: true },
      { id: 'incomplete', label: 'Daily mean (fewer than 18 valid hours)', values: incompleteMeans, color: 'series1', kind: 'markers', hollow: true },
    ]
    if (isPm10()) {
      series.push({
        id: 'limit',
        label: `Daily limit value, ${PM10_DAILY_LIMIT} ${unit()}`,
        values: d.days.map(() => PM10_DAILY_LIMIT),
        color: 'muted',
        kind: 'reference',
      })
    }
    return {
      x: d.days.map(dateKeyToEpoch),
      xMode: 'time-utc',
      series,
      yMin: 0,
      formatX: (x) => formatDateKeyShort(new Date(x * 1000).toISOString().slice(0, 10)),
      formatY: (v) => formatNumber(v, 1),
      note: (i) => `${d.valid_hours[i]} valid hours of 24${d.is_valid_day[i] ? '' : '; not a valid day'}.`,
    }
  })

  const summary = () => describeDaily(props.data, props.label, unit())

  return (
    <ChartFrame
      id="daily"
      title={`${props.label}, daily mean`}
      axisNote={`${unit()}. Days follow the EEA convention (UTC+1) and are shown as plain dates.`}
      legend={legendFor(spec())}
      summary={summary()}
      table={{
        caption: `${props.label} daily means`,
        columns: ['Day', `Mean (${unit()})`, 'Valid hours', 'Valid day'],
        rows: props.data.days.map((day, i) => [
          formatDateKey(day),
          props.data.mean[i] == null ? 'No value' : formatNumber(props.data.mean[i] as number, 1),
          String(props.data.valid_hours[i]),
          props.data.is_valid_day[i] ? 'Yes' : 'No',
        ]),
      }}
    >
      <UPlotChart spec={spec()} label={`${props.label} daily mean time series`} describedBy="daily-summary" />
    </ChartFrame>
  )
}
