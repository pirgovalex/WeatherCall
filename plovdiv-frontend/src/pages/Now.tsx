import { Show } from 'solid-js'
import ReadingsGrid from '../components/now/ReadingsGrid'
import RiskCard from '../components/now/RiskCard'
import SeriesPanel from '../components/now/SeriesPanel'
import PageHeader from '../components/ui/PageHeader'
import Section from '../components/ui/Section'
import { useMetaContext } from '../queries/metaContext'
import { formatPlovdivDateTimeYear } from '../transform/time'
import { dataWindow } from '../transform/window'

export default function Now() {
  const { query } = useMetaContext()
  return (
    <>
      <PageHeader title="Now">
        Tomorrow's air quality risk for Plovdiv and the newest hourly readings in the warehouse.
      </PageHeader>
      <RiskCard />
      <div class="mt-14">
        <Section
          id="readings"
          title="Latest readings"
          intro={
            <Show when={query.data}>
              {(meta) => (
                <>
                  Newest valid hourly values, up to {formatPlovdivDateTimeYear(dataWindow(meta()).maxEpoch)} Plovdiv time. They are
                  modelled values for a grid cell, not live station measurements. Air quality and weather come from different grid
                  cells around Plovdiv.
                </>
              )}
            </Show>
          }
        >
          <ReadingsGrid />
        </Section>
        <Section
          id="series"
          title="Hourly series"
          intro="Choose a variable and a period. Missing hours appear as gaps, not as interpolated lines."
        >
          <SeriesPanel />
        </Section>
      </div>
    </>
  )
}
