import { createEffect, createSignal, onCleanup, onMount, untrack } from 'solid-js'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { cssColor, themePaletteVersion } from '../../theme/theme'
import type { ChartSpec, ColorToken } from './spec'

const FONT = '12px "Atkinson Hyperlegible Next Variable", system-ui, sans-serif'

const TOKENS: Record<ColorToken, { variable: string; fallback: string }> = {
  series1: { variable: '--c-s1', fallback: '#2a78d6' },
  series2: { variable: '--c-s2', fallback: '#eb6834' },
  muted: { variable: '--c-ink-3', fallback: '#55677b' },
  ink: { variable: '--c-ink', fallback: '#0e1a26' },
}

function color(token: ColorToken): string {
  return cssColor(TOKENS[token].variable, TOKENS[token].fallback)
}

/** "#rrggbb" to "rgba(r, g, b, a)"; other formats are returned unchanged. */
export function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return hex
  const n = parseInt(m[1]!, 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

function tzDateFor(mode: ChartSpec['xMode']) {
  const zone = mode === 'time-plovdiv' ? 'Europe/Sofia' : 'UTC'
  return (ts: number) => uPlot.tzDate(new Date(ts * 1000), zone)
}

/** Axis labels for time scales: 24-hour clock below a day, dates above it, in the chart's own zone. */
function timeAxisValues(mode: ChartSpec['xMode']) {
  const timeZone = mode === 'time-plovdiv' ? 'Europe/Sofia' : 'UTC'
  const clock = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
  const day = new Intl.DateTimeFormat('en-GB', { timeZone, day: 'numeric', month: 'short' })
  const month = new Intl.DateTimeFormat('en-GB', { timeZone, month: 'short', year: 'numeric' })
  return (_u: uPlot, splits: number[], _axis: number, _space: number, incr: number) =>
    splits.map((v) => {
      const d = new Date(v * 1000)
      if (incr >= 27 * 86_400) return month.format(d)
      if (incr >= 86_400) return day.format(d)
      const t = clock.format(d)
      return t === '00:00' ? day.format(d) : t
    })
}

interface Tooltip {
  root: HTMLDivElement
  update: (u: uPlot, spec: ChartSpec) => void
}

function createTooltip(): Tooltip {
  const root = document.createElement('div')
  root.className =
    'pointer-events-none absolute left-0 top-0 z-10 hidden min-w-40 max-w-64 rounded-md border border-rule bg-surface px-3 py-2 text-xs text-ink shadow-md'
  root.setAttribute('aria-hidden', 'true')
  const head = document.createElement('div')
  head.className = 'mb-1 font-semibold text-ink-2'
  const rows = document.createElement('div')
  rows.className = 'space-y-1'
  const note = document.createElement('div')
  note.className = 'mt-1 text-ink-2'
  root.append(head, rows, note)

  return {
    root,
    update(u, spec) {
      const idx = u.cursor.idx
      const left = u.cursor.left ?? -1
      if (idx === null || idx === undefined || left < 0) {
        root.classList.add('hidden')
        return
      }
      head.textContent = spec.formatX(spec.x[idx]!)
      rows.replaceChildren()
      for (const s of spec.series) {
        if (s.silent || s.kind === 'edge') continue
        const value = s.values[idx]
        const row = document.createElement('div')
        row.className = 'flex items-center gap-2'
        const key = document.createElement('span')
        key.className = 'inline-block h-0.5 w-3 shrink-0'
        key.style.backgroundColor = color(s.color)
        const strong = document.createElement('span')
        strong.className = 'font-semibold tabular-nums'
        strong.textContent = value === null || value === undefined ? 'No value' : spec.formatY(value)
        const label = document.createElement('span')
        label.className = 'text-ink-2'
        label.textContent = s.label
        row.append(key, strong, label)
        rows.append(row)
      }
      note.textContent = spec.note?.(idx) ?? ''
      note.classList.toggle('hidden', !note.textContent)
      root.classList.remove('hidden')
      const overW = u.over.clientWidth
      const w = root.offsetWidth
      const x = left + 14 + w > overW ? left - 14 - w : left + 14
      const y = Math.max(0, Math.min((u.cursor.top ?? 0) - 12, u.over.clientHeight - root.offsetHeight))
      root.style.transform = `translate(${Math.max(0, x)}px, ${y}px)`
    },
  }
}

function buildOptions(spec: ChartSpec, width: number, height: number, tooltip: Tooltip): uPlot.Options {
  const surface = cssColor('--c-surface', '#fbfcfd')
  const muted = color('muted')
  const grid = cssColor('--c-grid', '#e3e9ee')
  const rule = cssColor('--c-rule', '#d0d9e2')
  const timeMode = spec.xMode !== 'value'

  const index = new Map(spec.series.map((s, i) => [s.id, i + 1]))

  const series: uPlot.Series[] = [{}]
  for (const s of spec.series) {
    const stroke = color(s.color)
    const base: uPlot.Series = {
      label: s.label,
      spanGaps: false,
      stroke: s.kind === 'edge' ? 'rgba(0,0,0,0)' : stroke,
      width: s.kind === 'reference' ? 1 : s.kind === 'edge' ? 0 : 2,
      points: { show: false },
    }
    if (s.kind === 'reference') base.dash = [5, 4]
    if (s.kind === 'markers') {
      base.paths = () => null
      base.points = { show: true, size: 9, width: 2, stroke, fill: s.hollow ? surface : stroke }
    } else if (s.dots) {
      base.points = { show: true, size: 8, width: 2, stroke: surface, fill: stroke }
    }
    series.push(base)
  }

  const bands: uPlot.Band[] = (spec.bands ?? []).flatMap((b) => {
    const upper = index.get(b.upper)
    const lower = index.get(b.lower)
    return upper && lower ? [{ series: [upper, lower] as [number, number], fill: withAlpha(color(b.color), 0.16) }] : []
  })

  const xValues = (_u: uPlot, splits: number[]) => splits.map((v) => spec.formatX(v))

  return {
    width,
    height,
    padding: [10, 28, 0, 0],
    legend: { show: false },
    cursor: {
      x: true,
      y: false,
      drag: { x: false, y: false, setScale: false },
      points: {
        size: 9,
        width: 2,
        stroke: surface,
        fill: (u, seriesIdx) => String(u.series[seriesIdx]?.stroke ?? muted),
      },
    },
    select: { show: false, left: 0, top: 0, width: 0, height: 0 },
    tzDate: timeMode ? tzDateFor(spec.xMode) : undefined,
    scales: {
      x: {
        time: timeMode,
        range: spec.xMin !== undefined && spec.xMax !== undefined ? [spec.xMin, spec.xMax] : undefined,
      },
      y: {
        range: (_u, min, max) => {
          const lo = spec.yMin !== undefined ? spec.yMin : min - (max - min || 1) * 0.08
          const hi = spec.yMax !== undefined ? spec.yMax : max + (max - min || 1) * 0.08
          return [lo, hi]
        },
      },
    },
    axes: [
      {
        stroke: muted,
        font: FONT,
        grid: { stroke: grid, width: 1 },
        ticks: { stroke: rule, width: 1, size: 5 },
        border: { stroke: rule, width: 1 },
        values: timeMode ? timeAxisValues(spec.xMode) : xValues,
      },
      {
        stroke: muted,
        font: FONT,
        size: 52,
        grid: { stroke: grid, width: 1 },
        ticks: { show: false },
        border: { show: false },
        values: (_u, splits) => splits.map((v) => spec.formatY(v)),
      },
    ],
    series,
    bands,
    hooks: {
      ready: [(u) => u.over.appendChild(tooltip.root)],
      setCursor: [(u) => tooltip.update(u, spec)],
    },
  }
}

function buildData(spec: ChartSpec): uPlot.AlignedData {
  return [spec.x, ...spec.series.map((s) => s.values)] as uPlot.AlignedData
}

interface UPlotChartProps {
  spec: ChartSpec
  /** Accessible name; the longer description goes in `describedBy`. */
  label: string
  describedBy?: string
}

/**
 * The only component that talks to uPlot. It draws the spec, re-draws when the
 * theme or data changes, follows the container width, shows a tooltip on hover
 * and lets keyboard users step through points with the arrow keys.
 */
export default function UPlotChart(props: UPlotChartProps) {
  let hostEl: HTMLDivElement | undefined
  let plot: uPlot | undefined
  let tooltip: Tooltip | undefined
  const [announcement, setAnnouncement] = createSignal('')
  const [fontsReady, setFontsReady] = createSignal(0)
  const [focusIndex, setFocusIndex] = createSignal<number | null>(null)

  const heightFor = (width: number) => (width < 480 ? 240 : 300)

  createEffect(() => {
    const spec = props.spec
    themePaletteVersion()
    fontsReady()
    untrack(() => {
      plot?.destroy()
      plot = undefined
      const host = hostEl!
      const width = host.clientWidth || 640
      tooltip = createTooltip()
      plot = new uPlot(buildOptions(spec, width, heightFor(width), tooltip), buildData(spec), host)
    })
  })

  onMount(() => {
    const observer = new ResizeObserver(() => {
      const el = hostEl
      if (!plot || !el || el.clientWidth === 0) return
      plot.setSize({ width: el.clientWidth, height: heightFor(el.clientWidth) })
    })
    observer.observe(hostEl!)
    document.fonts?.ready.then(() => setFontsReady((v) => v + 1)).catch(() => undefined)
    onCleanup(() => {
      observer.disconnect()
      plot?.destroy()
      plot = undefined
    })
  })

  const moveTo = (index: number) => {
    const spec = props.spec
    if (!plot || spec.x.length === 0) return
    const clamped = Math.max(0, Math.min(spec.x.length - 1, index))
    setFocusIndex(clamped)
    const left = plot.valToPos(spec.x[clamped]!, 'x')
    plot.setCursor({ left, top: 24 })
    setAnnouncement(
      spec.announce?.(clamped) ??
        `${spec.formatX(spec.x[clamped]!)}: ${spec.series
          .filter((s) => !s.silent && s.kind !== 'edge')
          .map((s) => `${s.label} ${s.values[clamped] == null ? 'no value' : spec.formatY(s.values[clamped]!)}`)
          .join(', ')}`,
    )
  }

  const onKeyDown = (event: KeyboardEvent) => {
    const current = focusIndex() ?? -1
    const last = props.spec.x.length - 1
    const keys: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowLeft: current < 0 ? last : current - 1,
      Home: 0,
      End: last,
    }
    const next = keys[event.key]
    if (next === undefined) return
    event.preventDefault()
    moveTo(next)
  }

  const onBlur = () => {
    setFocusIndex(null)
    setAnnouncement('')
    plot?.setCursor({ left: -10, top: -10 })
  }

  return (
    <div>
      <div
        ref={(el) => {
          hostEl = el
        }}
        role="img"
        tabindex="0"
        aria-label={props.label}
        aria-describedby={props.describedBy}
        onKeyDown={onKeyDown}
        onBlur={onBlur}
        class="w-full rounded-sm"
      />
      <p class="sr-only" aria-live="polite">
        {announcement()}
      </p>
      <p class="mt-1 text-xs text-ink-3">Hover or use the left and right arrow keys to read values.</p>
    </div>
  )
}
