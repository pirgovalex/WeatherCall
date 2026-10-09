import DailyPanel from '../components/history/DailyPanel'
import TrendPanel from '../components/history/TrendPanel'
import PageHeader from '../components/ui/PageHeader'
import Section from '../components/ui/Section'

export default function History() {
  return (
    <>
      <PageHeader title="History">
        How PM10 has changed over the months at each station, and how much of that change is explained by the weather.
      </PageHeader>
      <Section
        id="trend"
        title="Monthly PM10 trend"
        intro={
          <>
            Observed is the average of the valid daily means in each month, as the station recorded it. Weather-adjusted is the same
            average after the estimated effect of weather has been removed, so that a windy, rainy month and a calm, dry one can be
            compared on equal terms. The shaded band shows how uncertain the weather-adjusted value is. Where the lines differ,
            weather made that month cleaner or dirtier than usual; where they agree, the change came from something else.
          </>
        }
      >
        <TrendPanel />
      </Section>
      <Section
        id="daily"
        title="Daily means"
        intro="Daily averages for the most recent period, one point per EEA day. A day with fewer than 18 valid hours is not a valid day and is drawn as a hollow marker."
      >
        <DailyPanel />
      </Section>
    </>
  )
}
