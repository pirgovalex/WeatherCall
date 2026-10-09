export default function VerificationEmpty() {
  return (
    <div
      data-testid="verification-empty"
      class="rounded-lg border border-dashed border-rule bg-sunken px-5 py-6"
    >
      <h3 class="text-base font-semibold text-ink">Forecast verification has not started</h3>
      <div class="mt-2 max-w-2xl space-y-2 text-sm text-ink-2">
        <p>
          Verification compares each forecast, captured when it was issued, with what was observed afterwards. It needs forecasts
          to be archived first, then enough days to pass for the outcomes to be known.
        </p>
        <p>
          This section fills in as forecast captures accumulate. Until then there is nothing to show here, and no numbers are
          made up to fill the space. The test-period results above come from past data, not from archived forecasts.
        </p>
      </div>
    </div>
  )
}
