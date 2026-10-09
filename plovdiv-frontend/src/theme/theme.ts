import { createSignal } from 'solid-js'

export type ThemeChoice = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'plovdiv-theme'

function readStored(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

function apply(choice: ThemeChoice): void {
  const root = document.documentElement
  if (choice === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', choice)
}

const [choice, setChoiceSignal] = createSignal<ThemeChoice>('system')
/** Bumped whenever the effective colours may have changed, so canvases can redraw. */
const [paletteVersion, setPaletteVersion] = createSignal(0)

let started = false

/** Apply the stored choice and watch the system setting. Safe to call more than once. */
export function initTheme(): void {
  if (started || typeof window === 'undefined') return
  started = true
  const stored = readStored()
  setChoiceSignal(stored)
  apply(stored)
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    setPaletteVersion((v) => v + 1)
  })
}

export function setTheme(next: ThemeChoice): void {
  setChoiceSignal(next)
  apply(next)
  try {
    if (next === 'system') window.localStorage.removeItem(STORAGE_KEY)
    else window.localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // Storage can be unavailable; the choice then lasts for this page view only.
  }
  setPaletteVersion((v) => v + 1)
}

export const themeChoice = choice
export const themePaletteVersion = paletteVersion

/** Read a colour token from the document, as the browser resolved it. */
export function cssColor(token: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim()
  return value || fallback
}
