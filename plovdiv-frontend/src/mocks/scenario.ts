/**
 * Mock scenarios. They exist so every state of the interface can be reached
 * without a backend: open the app with `?mock=<name>` while mock mode is on.
 */
export type Scenario = 'default' | 'historical' | 'empty' | 'unavailable' | 'error' | 'slow'

export const SCENARIOS: readonly Scenario[] = ['default', 'historical', 'empty', 'unavailable', 'error', 'slow']

let current: Scenario = 'default'

export function parseScenario(value: string | null | undefined): Scenario {
  return SCENARIOS.includes(value as Scenario) ? (value as Scenario) : 'default'
}

export function setScenario(scenario: Scenario): void {
  current = scenario
}

export function getScenario(): Scenario {
  return current
}
