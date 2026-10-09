import type { JSX } from 'solid-js'

interface SectionProps {
  id: string
  title: string
  intro?: JSX.Element
  children: JSX.Element
  /** Controls placed beside the heading on wide screens. */
  actions?: JSX.Element
}

export default function Section(props: SectionProps) {
  return (
    <section aria-labelledby={`${props.id}-title`} class="mt-12 first:mt-0">
      <div class="mb-4 flex flex-col gap-3 border-t border-rule pt-4 sm:flex-row sm:items-end sm:justify-between">
        <div class="max-w-2xl">
          <h2 id={`${props.id}-title`} class="text-xl font-semibold tracking-tight text-ink">
            {props.title}
          </h2>
          {props.intro ? <p class="mt-1 text-sm text-ink-2">{props.intro}</p> : null}
        </div>
        {props.actions}
      </div>
      {props.children}
    </section>
  )
}
