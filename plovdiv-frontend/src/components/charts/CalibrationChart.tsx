import { createMemo, For, Show } from 'solid-js'
import { SPARSE_BIN_DAYS } from '../../transform/calibration'
import type { CalibrationView } from '../../transform/calibration'
import { formatPercent } from '../../transform/units'
import ChartFrame from './ChartFrame'
import { legendFor } from './spec'
import type { ChartSpec } from './spec'
import UPlotChart from './UPlotChart'

export function describeCalibration(view: CalibrationView): string {
  const filled = view.bins.filter((b) => b.n > 0 && b.predicted !== null && b.observed !== null)
  const empty = view.bins.length - filled.length
  if (filled.length === 0) return 'No bin has test days, so there is nothing to plot.'
  const worst = filled.reduce((w, b) => (Math.abs(b.observed! - b.predicted!) > Math.abs(w.observed! - w.predicted!) ? b : w))
  const gap = Math.abs(worst.observed! - worst.predicted!)
  const emptyNote = empty > 0 ? ` ${empty} bin${empty === 1 ? ' has' : 's have'} no test days and ${empty === 1 ? 'is' : 'are'} left out.` : ''
  return (
    `Predicted probability against the share of days that were actually bad, on the test period. ` +
    `Points on the diagonal mean the probabilities are well calibrated. ` +
    `The largest gap is ${Math.round(gap * 100)} percentage points, in the bin from ${formatPercent(worst.lo)} to ${formatPercent(worst.hi)} (${worst.n} test days).` +
    emptyNote
  )
}

/** Reliability diagram: mean predicted probability against observed rate, with the diagonal. */
export default function CalibrationChart(props: { view: CalibrationView }) {
  const spec = createMemo<ChartSpec>(() => ({
    x: props.view.x,
    xMode: 'value',
    xMin: 0,
    xMax: 1,
    yMin: 0,
    yMax: 1,
    series: [
      { id: 'diagonal', label: 'Perfect calibration', values: props.view.diagonal, color: 'muted', kind: 'reference', silent: true },
      { id: 'observed', label: 'Observed share of bad days', values: props.view.observed, color: 'series1', kind: 'line', dots: true },
    ],
    formatX: (x) => formatPercent(x),
    formatY: (v) => formatPercent(v),
    note: (i) => {
      const n = props.view.counts[i]
      if (n == null) return undefined
      return `Predicted ${formatPercent(props.view.x[i]!)}. ${n} test days in this bin${n < SPARSE_BIN_DAYS ? ' (few days, so noisy)' : ''}.`
    },
  }))

  const sparse = () => props.view.bins.filter((b) => b.n > 0 && b.n < SPARSE_BIN_DAYS)

  return (
    <ChartFrame
      id="calibration"
      title="Reliability of the predicted probabilities"
      axisNote="Horizontal: mean predicted probability in the bin. Vertical: share of those days that were bad. Test period only."
      legend={legendFor(spec())}
      summary={describeCalibration(props.view)}
      caption={
        <Show when={sparse().length > 0}>
          <span>
            Bins with fewer than {SPARSE_BIN_DAYS} test days are noisy:{' '}
            <For each={sparse()}>
              {(b, i) => (
                <>
                  {i() > 0 ? ', ' : ''}
                  {formatPercent(b.lo)} to {formatPercent(b.hi)} ({b.n})
                </>
              )}
            </For>
            .
          </span>
        </Show>
      }
      table={{
        caption: 'Calibration bins on the test period',
        columns: ['Probability bin', 'Mean predicted', 'Observed share', 'Test days'],
        rows: props.view.bins.map((b) => [
          `${formatPercent(b.lo)} to ${formatPercent(b.hi)}`,
          b.predicted === null ? 'No test days' : formatPercent(b.predicted, 1),
          b.observed === null ? 'No test days' : formatPercent(b.observed, 1),
          String(b.n),
        ]),
      }}
    >
      <UPlotChart spec={spec()} label="Reliability diagram" describedBy="calibration-summary" />
    </ChartFrame>
  )
}
