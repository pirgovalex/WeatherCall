import { For } from 'solid-js'

interface DataTableProps {
  caption: string
  columns: string[]
  rows: string[][]
  /** Open by default; charts keep theirs closed. */
  summary?: string
}

/**
 * Text twin of a chart. Every value in a chart is reachable here without a
 * pointer, and the table works with screen readers.
 */
export default function DataTable(props: DataTableProps) {
  return (
    <details class="mt-3 text-sm">
      <summary class="cursor-pointer rounded px-1 py-1 font-medium text-primary hover:underline">
        {props.summary ?? 'Show the values as a table'}
      </summary>
      <div class="mt-2 max-h-80 overflow-auto rounded-md border border-rule">
        <table class="w-full border-collapse text-left tabular-nums">
          <caption class="sr-only">{props.caption}</caption>
          <thead class="sticky top-0 bg-sunken">
            <tr>
              <For each={props.columns}>
                {(column) => (
                  <th scope="col" class="px-3 py-2 font-semibold text-ink">
                    {column}
                  </th>
                )}
              </For>
            </tr>
          </thead>
          <tbody>
            <For each={props.rows}>
              {(row) => (
                <tr class="border-t border-rule">
                  <th scope="row" class="px-3 py-1.5 font-normal text-ink">
                    {row[0]}
                  </th>
                  <For each={row.slice(1)}>{(cell) => <td class="px-3 py-1.5 text-ink-2">{cell}</td>}</For>
                </tr>
              )}
            </For>
          </tbody>
        </table>
      </div>
    </details>
  )
}
