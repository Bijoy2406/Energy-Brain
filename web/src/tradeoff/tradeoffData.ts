import type { components } from '../api/generated'
import { formatBdt, formatKg } from '../formatting'
export type ParetoResponse = components['schemas']['ParetoResponse']
export type ParetoPoint = components['schemas']['ParetoPoint']
export type PlotGroup = ParetoPoint & { weights: number[]; label: string }
export const formatWeight = (weight: number) => Number(weight.toFixed(2)).toString()

export function groupPoints(response: ParetoResponse): PlotGroup[] {
  const groups = new Map<number, PlotGroup>()
  for (const point of [...response.points].sort((a, b) => a.weight - b.weight)) {
    const existing = groups.get(point.group)
    if (existing) existing.weights.push(point.weight)
    else groups.set(point.group, { ...point, weights: [point.weight], label: '' })
  }
  return [...groups.values()].map(group => ({ ...group,
    label: `w ${formatWeight(group.weights[0])}${group.weights.length > 1 ? ` - ${formatWeight(group.weights.at(-1)!)}` : ''}`,
  }))
}

export function frontierGroups(response: ParetoResponse): PlotGroup[] {
  const groups = groupPoints(response)
  return response.frontier.flatMap(point => {
    const group = groups.find(group => group.group === point.group)
    return group ? [{ ...group, ...point }] : []
  })
}

export function axisScale(values: number[]): { domain: [number, number]; ticks: number[] } {
  if (!values.length) return { domain: [1, 2], ticks: [1, 2] }
  const min = Math.min(...values), max = Math.max(...values)
  const padding = Math.max((max - min) * 0.02, Math.max(Math.abs(min), Math.abs(max)) * 0.01, 0.01)
  const step = 10 ** Math.floor(Math.log10(Math.max((max - min + padding * 2) / 4, 0.001)))
  let low = Math.floor((min - padding) / step) * step
  const high = Math.ceil((max + padding) / step) * step
  if (low === 0) low = min > 0 ? min / 2 : -padding
  const ticks = Array.from({ length: 5 }, (_, i) => Math.round(low + (high - low) * i / 4))
  return { domain: [low, high], ticks: [...new Set(ticks)].filter(t => t >= low && t <= high) }
}

export function nearestWeight(response: ParetoResponse, value: number): ParetoPoint | null {
  return [...response.points].sort((a, b) => a.weight - b.weight).reduce<ParetoPoint | null>((best, point) =>
    !best || Math.abs(point.weight - value) < Math.abs(best.weight - value) - 1e-10 ? point : best, null)
}

/** Sole calculation of differences, used for both the frontier summary and dominance explanations. */
export function tradeoffSummary(response: Pick<ParetoResponse, 'frontier'>) {
  if (response.frontier.length < 2) return null
  const first = response.frontier[0], last = response.frontier.at(-1)!
  const delta = (from: number, to: number) => {
    const difference = to - from
    return { difference, percent: from === 0 ? null : difference / from * 100,
      direction: difference === 0 ? 'equal' : difference > 0 ? 'more' : 'less' }
  }
  return { cost: delta(first.cost_bdt, last.cost_bdt), co2: delta(first.co2_kg, last.co2_kg) }
}

export function explainInefficientPlan(
  group: Pick<PlotGroup, 'label' | 'cost_bdt' | 'co2_kg'>,
  better: Pick<PlotGroup, 'label' | 'cost_bdt' | 'co2_kg'>,
): string {
  const costDiff = group.cost_bdt - better.cost_bdt
  const co2Diff = group.co2_kg - better.co2_kg

  const costRounded = Math.round(costDiff)
  const co2Rounded = Math.round(co2Diff)

  // Cost and CO2 both slightly more but below display rounding (< 0.5)
  if (costDiff > 0 && costRounded === 0 && co2Diff > 0 && co2Rounded === 0) {
    return `The ${group.label} plan costs about the same and emits about the same CO2 compared with ${better.label}, so it is not an efficient choice.`
  }

  // Same CO2: difference below 0.05 kg
  if (co2Diff < 0.05) {
    const costText = costRounded === 0 ? 'about the same' : `${formatBdt(costDiff)} BDT more`
    return `The ${group.label} plan costs ${costText} and saves no CO2 compared with ${better.label}, so it is not an efficient choice.`
  }

  // Same cost (0 BDT difference or below 1e-6) but more CO2
  if (costDiff <= 1e-6) {
    const co2Text = co2Rounded === 0 ? 'about the same CO2' : `${formatKg(co2Diff)} kg more CO2`
    return `The ${group.label} plan costs the same and emits ${co2Text} compared with ${better.label}, so it is not an efficient choice.`
  }

  // More cost and more CO2
  const costText = costRounded === 0 ? 'about the same' : `${formatBdt(costDiff)} BDT more`
  const co2Text = co2Rounded === 0 ? 'about the same CO2' : `${formatKg(co2Diff)} kg more CO2`
  const also = costRounded > 0 ? 'also ' : ''
  return `The ${group.label} plan costs ${costText} and ${also}emits ${co2Text} compared with ${better.label}, so it is not an efficient choice.`
}
