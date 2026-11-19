import { formatPercent } from '../formatting'

/** The only calculation of daily savings, shared by the chart and same-day card. */
export function compareDailyCost(baseline: number | null, optimiser: number | null) {
  if (baseline === null || optimiser === null) return null
  const difference = baseline - optimiser
  return { difference, percent: baseline === 0 ? null : difference / baseline * 100 }
}

export function savingWords(saved: number | null, metric: string) {
  if (saved === null) return `${metric} comparison unavailable`
  if (Math.abs(saved) < 0.05) return `about the same ${metric}`
  return `${formatPercent(Math.abs(saved))} % ${saved > 0 ? 'lower' : 'higher'} ${metric}`
}
