import CalibrationPanel from '../components/forecasts/CalibrationPanel'
import ModelGate from '../components/forecasts/ModelGate'
import PerformanceTable from '../components/forecasts/PerformanceTable'
import TrackRecord from '../components/forecasts/TrackRecord'
import PageHeader from '../components/ui/PageHeader'
import Section from '../components/ui/Section'

export default function Forecasts() {
  return (
    <>
      <PageHeader title="Forecasts">
        How far the air quality risk model can be trusted: whether its probabilities are honest, how it compares with simple
        baselines, and what is not yet known.
      </PageHeader>
      <ModelGate>
        {(modelId) => (
          <>
            <Section
              id="reliability"
              title="Are the probabilities reliable?"
              intro="If the model says 30%, about 30 in 100 such days should turn out bad. Points close to the diagonal mean it does."
            >
              <CalibrationPanel modelId={modelId()} />
            </Section>
            <Section
              id="performance"
              title="Model against simple baselines"
              intro="A model is only useful if it beats simple rules that need no modelling."
            >
              <PerformanceTable modelId={modelId()} />
            </Section>
          </>
        )}
      </ModelGate>
      <Section
        id="verification"
        title="Forecast verification"
        intro="The model's calls compared with what the station measured afterwards."
      >
        <TrackRecord />
      </Section>
    </>
  )
}
