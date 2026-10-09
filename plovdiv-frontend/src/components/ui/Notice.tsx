import type { JSX } from 'solid-js'

type Tone = 'info' | 'warning' | 'danger'

const TONES: Record<Tone, string> = {
  info: 'bg-info border-info-edge',
  warning: 'bg-notice border-notice-edge',
  danger: 'bg-danger border-danger-edge',
}

interface NoticeProps {
  tone: Tone
  title: string
  children?: JSX.Element
  /** Alert interrupts assistive technology; use it only for errors. */
  live?: 'status' | 'alert'
  testId?: string
}

/** A bordered message. Meaning is carried by the title and text, the colour only reinforces it. */
export default function Notice(props: NoticeProps) {
  return (
    <div
      role={props.live ?? 'note'}
      data-testid={props.testId}
      class={`rounded-md border-l-4 px-4 py-3 text-sm ${TONES[props.tone]}`}
    >
      <p class="font-semibold text-ink">{props.title}</p>
      {props.children ? <div class="mt-1 text-ink-2">{props.children}</div> : null}
    </div>
  )
}
