export default function Brand() {
  return (
    <div class="flex items-center gap-3">
      <svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true" class="shrink-0 text-primary">
        <path d="M2 22c5-9 9-9 14 0s9 9 14 0" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
        <path d="M2 14c5-9 9-9 14 0s9 9 14 0" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" opacity="0.45" />
      </svg>
      <div class="leading-tight">
        <p class="text-base font-semibold tracking-tight text-ink">Plovdiv air</p>
        <p class="text-xs text-ink-3">Weather and air quality</p>
      </div>
    </div>
  )
}
