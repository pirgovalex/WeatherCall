import { For } from 'solid-js'
import type { DriverBar } from '../../transform/risk'

const MINUS = '−'

function signed(value: number): string {
  const text = Math.abs(value).toFixed(2)
  if (value > 0) return `+${text}`
  if (value < 0) return `${MINUS}${text}`
  return text
}

const DIRECTION_TEXT = {
  raises: 'raises the risk',
  lowers: 'lowers the risk',
  neutral: 'does not change the risk',
}

/**
 * Signed bars for the largest drivers of one prediction. A bar to the right of
 * the centre line raises the probability, a bar to the left lowers it. The sign
 * and the words "raises" or "lowers" are written out, so colour is not needed.
 */
export default function DriverBars(props: { bars: DriverBar[] }) {
  return (
    <div>
      <div class="mb-1 grid grid-cols-[minmax(0,9rem)_1fr_3rem] items-end gap-3 text-xs text-ink-3 sm:grid-cols-[minmax(0,11rem)_1fr_3rem]">
        <span />
        <span class="flex justify-between whitespace-nowrap">
          <span>Lowers</span>
          <span>Raises</span>
        </span>
        <span />
      </div>
      <ul class="divide-y divide-rule border-y border-rule" aria-label="Largest drivers of this score">
        <For each={props.bars}>
          {(bar) => (
            <li class="grid grid-cols-[minmax(0,9rem)_1fr_3rem] items-center gap-3 py-2 sm:grid-cols-[minmax(0,11rem)_1fr_3rem]">
              <span class="text-sm text-ink">
                {bar.label}
                <span class="sr-only">: {DIRECTION_TEXT[bar.direction]}</span>
              </span>
              <svg viewBox="0 0 200 14" preserveAspectRatio="none" class="block h-3.5 w-full" aria-hidden="true">
                <rect x="0" y="5" width="200" height="4" class="fill-sunken" />
                <rect
                  x={bar.scaled >= 0 ? 100 : 100 + bar.scaled * 98}
                  y="1"
                  width={Math.abs(bar.scaled) * 98}
                  height="12"
                  class={bar.direction === 'raises' ? 'fill-up' : 'fill-down'}
                />
                <rect x="99" y="0" width="2" height="14" class="fill-ink-3" />
              </svg>
              <span class="text-right text-sm font-semibold tabular-nums text-ink">{signed(bar.contribution)}</span>
            </li>
          )}
        </For>
      </ul>
      <p class="mt-2 text-xs text-ink-3">
        Contributions are on the log-odds scale and only the largest are listed, so they do not add up to the whole score.
      </p>
    </div>
  )
}
