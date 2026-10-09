import { formatPercent, formatProbability } from '../../transform/units'

interface RiskMeterProps {
  probability: number
  /** Probability at or above which the model counts a day as a predicted bad day. */
  threshold: number | undefined
  /** Accessible name, naming what the probability is for. */
  label: string
}

/**
 * Horizontal meter from 0 to 100 percent with the model's decision threshold
 * marked. The bar is drawn in SVG so its geometry is attributes, not inline
 * styles. Whether the score is above the threshold is always written out next to
 * it; the fill colour only reinforces that.
 */
export default function RiskMeter(props: RiskMeterProps) {
  const shown = () => formatProbability(props.probability)
  const pct = () => shown().percent
  const above = () => props.threshold !== undefined && props.probability >= props.threshold

  return (
    <div>
      <div
        role="meter"
        aria-label={props.label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct()}
        aria-valuetext={shown().spoken}
        class="overflow-hidden rounded-full bg-primary-soft"
      >
        <svg viewBox="0 0 1000 24" preserveAspectRatio="none" class="block h-6 w-full" aria-hidden="true">
          <rect x="0" y="0" width={props.probability * 1000} height="24" class={above() ? 'fill-up' : 'fill-primary'} />
          {props.threshold !== undefined ? (
            <>
              <rect x={props.threshold * 1000 - 5} y="0" width="10" height="24" class="fill-surface" />
              <rect x={props.threshold * 1000 - 2} y="0" width="4" height="24" class="fill-ink" />
            </>
          ) : null}
        </svg>
      </div>
      <div class="mt-1 flex justify-between text-xs text-ink-3 tabular-nums" aria-hidden="true">
        <span>0%</span>
        <span>25%</span>
        <span>50%</span>
        <span>75%</span>
        <span>100%</span>
      </div>
      {props.threshold !== undefined ? (
        <p class="mt-2 flex items-start gap-2 text-sm text-ink-2" data-testid="threshold-note">
          <svg viewBox="0 0 10 16" width="10" height="16" aria-hidden="true" class="mt-0.5 shrink-0 text-ink">
            <rect x="3" y="0" width="4" height="16" fill="currentColor" />
          </svg>
          <span>
            The marker is the model's decision threshold, {formatPercent(props.threshold)}. At or above it the model counts
            the day as a predicted bad day. This score is {above() ? 'at or above' : 'below'} it.
          </span>
        </p>
      ) : null}
    </div>
  )
}
