import { createMemo, Show } from 'solid-js'
import { useCalibration } from '../../queries/hooks'
import { toCalibrationView } from '../../transform/calibration'
import CalibrationChart from '../charts/CalibrationChart'
import QueryBoundary from '../ui/QueryBoundary'

export default function CalibrationPanel(props: { modelId: string }) {
  const query = useCalibration(() => props.modelId)
  const view = createMemo(() => (query.data ? toCalibrationView(query.data) : null))
  return (
    <QueryBoundary
      query={query}
      what="calibration bins"
      isEmpty={(d) => d.bin_lo.length === 0}
      emptyTitle="No calibration bins"
      emptyText="The model has no calibration bins on its test period."
    >
      {() => <Show when={view()}>{(v) => <CalibrationChart view={v()} />}</Show>}
    </QueryBoundary>
  )
}
