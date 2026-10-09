import type { ModelCardResponse } from '../api/types'

export interface PerformanceRow {
  id: 'model' | 'climatology' | 'persistence'
  name: string
  description: string
  rocAuc: number | null
  /** ROC-AUC minus the model's ROC-AUC; null for the model row or when either side is missing. */
  difference: number | null
  /** Metrics the card publishes for the model only; null for baselines and for metrics not computed. */
  prAuc: number | null
  brier: number | null
  precision: number | null
  recall: number | null
}

/** One row for the model and one per baseline, in the order they are compared. */
export function toPerformance(card: ModelCardResponse): PerformanceRow[] {
  const m = card.metrics
  const diff = (v: number | null) => (m.roc_auc_test === null || v === null ? null : v - m.roc_auc_test)
  const clim = card.baselines.roc_auc_test_climatology
  const pers = card.baselines.roc_auc_test_persistence
  return [
    {
      id: 'model',
      name: 'This model',
      description: card.algorithm.replace(/_/g, ' '),
      rocAuc: m.roc_auc_test,
      difference: null,
      prAuc: m.pr_auc_test,
      brier: m.brier_test,
      precision: m.precision_test,
      recall: m.recall_test,
    },
    {
      id: 'climatology',
      name: 'Climatology baseline',
      description: 'Reference predictor based on typical conditions for the time of year',
      rocAuc: clim,
      difference: diff(clim),
      prAuc: null,
      brier: null,
      precision: null,
      recall: null,
    },
    {
      id: 'persistence',
      name: 'Persistence baseline',
      description: 'Reference predictor that assumes tomorrow resembles today',
      rocAuc: pers,
      difference: diff(pers),
      prAuc: null,
      brier: null,
      precision: null,
      recall: null,
    },
  ]
}

export interface MetricHelp {
  id: string
  name: string
  help: string
}

export const METRIC_HELP: MetricHelp[] = [
  {
    id: 'roc_auc',
    name: 'ROC-AUC',
    help: 'The chance that a randomly chosen bad day gets a higher score than a randomly chosen good day. 0.5 is no better than guessing and 1 is perfect.',
  },
  {
    id: 'pr_auc',
    name: 'PR-AUC',
    help: 'How well the model keeps precision high while it catches more bad days. It is most useful when bad days are rare, and should be compared with the share of bad days rather than with 0.5.',
  },
  { id: 'brier', name: 'Brier score', help: 'The average squared error of the probabilities. Lower is better and 0 is perfect.' },
  { id: 'precision', name: 'Precision', help: 'Of the days the model flagged as bad at its decision threshold, the share that really were bad.' },
  { id: 'recall', name: 'Recall', help: 'Of the days that really were bad, the share the model flagged at its decision threshold.' },
]
