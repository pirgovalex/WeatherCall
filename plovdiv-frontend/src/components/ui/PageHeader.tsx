import type { JSX } from 'solid-js'

export default function PageHeader(props: { title: string; children?: JSX.Element }) {
  return (
    <div class="mb-8 max-w-3xl">
      <h1 class="text-3xl font-semibold tracking-tight text-ink">{props.title}</h1>
      {props.children ? <p class="mt-2 text-base text-ink-2">{props.children}</p> : null}
    </div>
  )
}
