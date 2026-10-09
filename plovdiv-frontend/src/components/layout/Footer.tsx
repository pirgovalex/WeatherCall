import { For, Show } from 'solid-js'
import { useMetaContext } from '../../queries/metaContext'
import { formatPlovdivDateTimeYear, isoToEpoch } from '../../transform/time'

export default function Footer() {
  const { query } = useMetaContext()
  return (
    <footer class="mt-16 border-t border-rule pt-6 text-sm text-ink-2">
      <Show when={query.data} fallback={<p>Data attribution and run details appear once the API answers.</p>}>
        {(meta) => (
          <div class="grid gap-6 md:grid-cols-[2fr_1fr]">
            <div>
              <h2 class="font-semibold text-ink">Data attribution</h2>
              <ul class="mt-1 list-disc space-y-1 pl-5">
                <For each={meta().attribution}>{(line) => <li>{line}</li>}</For>
              </ul>
              <p class="mt-2">
                Values are gridded model output, not station measurements, unless a series is labelled as a station
                measurement.
              </p>
            </div>
            <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt class="text-ink-3">Run</dt>
              <dd class="break-all text-ink tabular-nums">{meta().run_id}</dd>
              <dt class="text-ink-3">Generated</dt>
              <dd class="text-ink tabular-nums">
                {formatPlovdivDateTimeYear(isoToEpoch(meta().generated_at_utc))} (Plovdiv)
              </dd>
              <dt class="text-ink-3">Contract</dt>
              <dd class="text-ink">version {meta().schema_version}</dd>
            </dl>
          </div>
        )}
      </Show>
    </footer>
  )
}
