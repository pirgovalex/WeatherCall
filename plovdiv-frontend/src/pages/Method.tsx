import { For } from 'solid-js'
import type { JSX } from 'solid-js'
import ModelCardProse from '../components/forecasts/ModelCardProse'
import ModelGate from '../components/forecasts/ModelGate'
import PageHeader from '../components/ui/PageHeader'
import Section from '../components/ui/Section'
import { useMetaContext } from '../queries/metaContext'

function Prose(props: { children: JSX.Element }) {
  return <div class="max-w-3xl space-y-3 text-base text-ink-2">{props.children}</div>
}

const SOURCE_TYPES: [string, string][] = [
  ['station', 'Measured at a monitoring station.'],
  ['analysis', 'A model estimate of current conditions for a grid cell. Not a station measurement.'],
  ['reanalysis_provisional', 'A reanalysis value that can still be revised. Not a station measurement.'],
  ['reanalysis_final', 'The final reanalysis value. Not a station measurement.'],
  ['forecast', 'A forecast for a time that had not yet happened when it was issued.'],
]

const READING_GUIDES: { id: string; title: string; text: string }[] = [
  {
    id: 'now',
    title: 'Now',
    text: 'The top card gives the model\'s probability that the next day is a bad day for air quality, for the station you choose. A notice appears whenever the row is a past evaluation row instead of a live forecast. Below it are the newest hourly values for each variable and a chart where you can pick a variable and a period.',
  },
  {
    id: 'history',
    title: 'History',
    text: 'Each point is one month of PM10 at a station. Compare the observed line with the weather-adjusted line: the gap is the part of the month attributed to weather. Hover a point, or open the table, to see how many valid days the month rests on. Hollow markers in the daily chart are days with too little data to count.',
  },
  {
    id: 'forecasts',
    title: 'Forecasts',
    text: 'The reliability chart checks whether stated probabilities match how often bad days happened; points on the diagonal are ideal. The table compares the model with two simple baselines. The verification section stays empty until archived forecasts have been compared with outcomes.',
  },
]

export default function Method() {
  const { query } = useMetaContext()
  return (
    <>
      <PageHeader title="Method">
        Where the data comes from, how the risk model was built and tested, and how to read each page.
      </PageHeader>

      <Section id="sources" title="Data sources and attribution">
        <Prose>
          <ul class="list-disc space-y-2 pl-5">
            <li>
              Weather and air-quality data: <a class="text-primary underline" href="https://open-meteo.com/">Open-Meteo</a>, licensed
              under CC BY 4.0. Historical weather is served as reanalysis (ERA5) by Open-Meteo; weather forecasts come from the ECMWF
              IFS model through Open-Meteo.
            </li>
            <li>
              Air-quality forecasts: Copernicus Atmosphere Monitoring Service (CAMS) Europe. Contains modified Copernicus
              Atmosphere Monitoring Service information.
            </li>
            <li>
              Station observations from the European Environment Agency (EEA) verified air-quality data, as reported by Bulgaria:
              daily PM10 and PM2.5 means for the Kamenitsa station and daily PM10 means for the Trakia station in Plovdiv. These are measurements. In mock mode the
              station-based figures are synthetic.
            </li>
          </ul>
          <p>
            Values from the modelled sources are gridded model output, not station measurements. They are only ground truth if
            station data is loaded alongside them, and every series states which kind of value it is.
          </p>
          <p>
            Attribution as reported by the API for the current run:{' '}
            <span class="text-ink">{query.data?.attribution.join('; ')}</span>.
          </p>
        </Prose>
      </Section>

      <Section id="labels" title="How values are labelled" intro="Every series carries a source type so that modelled values are never mistaken for measurements.">
        <dl class="grid max-w-3xl gap-x-8 gap-y-3 sm:grid-cols-[auto_1fr]">
          <For each={SOURCE_TYPES}>
            {([name, text]) => (
              <>
                <dt class="font-semibold text-ink">
                  <code class="text-sm">{name}</code>
                </dt>
                <dd class="text-ink-2">{text}</dd>
              </>
            )}
          </For>
        </dl>
      </Section>

      <Section id="time" title="Time and places">
        <Prose>
          <p>
            The warehouse stores time in UTC. This dashboard shows hourly times in Europe/Sofia, and handles the autumn clock
            change, when the local day has 25 hours. Daily air-quality figures follow the EEA day, which runs on UTC+1, and are
            shown as plain dates.
          </p>
          <p>
            Weather and air-quality sources place Plovdiv in different grid cells, so a weather value and an air-quality value
            for the same hour describe slightly different spots.
          </p>
        </Prose>
      </Section>

      <Section id="model" title="The risk model" intro="Written from the model card published by the warehouse.">
        <ModelGate>{(modelId) => <ModelCardProse modelId={modelId()} />}</ModelGate>
      </Section>

      <Section id="reading" title="How to read each page">
        <div class="grid max-w-3xl gap-6">
          <For each={READING_GUIDES}>
            {(guide) => (
              <div>
                <h3 class="text-base font-semibold text-ink">{guide.title}</h3>
                <p class="mt-1 text-ink-2">{guide.text}</p>
              </div>
            )}
          </For>
        </div>
      </Section>
    </>
  )
}
