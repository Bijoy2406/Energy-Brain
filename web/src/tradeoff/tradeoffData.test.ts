import { describe, expect, it } from 'vitest'
import { axisScale, explainInefficientPlan, groupPoints, frontierGroups, nearestWeight, tradeoffSummary } from './tradeoffData'
import { paretoFixture } from '../test/paretoFixture'
describe('trade-off data', () => {
  it('groups API values and sorts weights, preserving frontier ordering', () => {
    const data = paretoFixture()
    const groups = groupPoints({ ...data, points: [...data.points].reverse() })
    expect(groups.find(g => g.group === 1)).toMatchObject({ weights: [0.4, 1], label: 'w 0.4 - 1', cost_bdt: 100000 })
    expect(groups.find(g => g.group === 0)?.label).toBe('w 0')
    expect(frontierGroups(data).map(g => g.group)).toEqual([1, 0])
  })
  it.each([[1000], [1000, 1001]])('pads nonzero axes for %j', (...values) => {
    const { domain, ticks } = axisScale(values)
    expect(domain[0]).toBeGreaterThan(0)
    expect(domain[0]).toBeLessThan(Math.min(...values) - 9)
    expect(domain[1]).toBeGreaterThan(Math.max(...values) + 9)
    expect(ticks.every(Number.isInteger)).toBe(true)
  })
  it('chooses the lower solved weight on a tie and returns its group', () => {
    expect(nearestWeight(paretoFixture(), 0.2)).toMatchObject({ weight: 0, group: 0 })
  })
  it('computes signed differences and percentages only between frontier endpoints', () => {
    expect(tradeoffSummary(paretoFixture())).toEqual({ cost: { difference: 2000, percent: 2, direction: 'more' }, co2: { difference: -10, percent: -1, direction: 'less' } })
    const data = paretoFixture()
    expect(tradeoffSummary({ ...data, frontier: [data.frontier[0]] })).toBeNull()
  })
})
it('keeps a single plotted plan and calculates no trade-off for one efficient plan', () => {
  const data = paretoFixture()
  const point = data.points[0]
  const response = { ...data, points: [point, { ...point, weight: 1, same_as_weight: 0 }], frontier: [point] }
  expect(groupPoints(response)).toHaveLength(1)
  expect(groupPoints(response)[0].label).toBe('w 0 - 1')
  expect(tradeoffSummary(response)).toBeNull()
})
it('returns directions and unavailable percentages for zero baselines without infinities', () => {
  const data = paretoFixture()
  const response = { frontier: [{ ...data.points[0], cost_bdt: 0, co2_kg: 0 }, { ...data.points[1], cost_bdt: 1, co2_kg: 0 }] }
  expect(tradeoffSummary(response)).toEqual({ cost: { difference: 1, percent: null, direction: 'more' }, co2: { difference: 0, percent: null, direction: 'equal' } })
})

describe('explainInefficientPlan', () => {
  const better = { label: 'w 0.05 - 1', cost_bdt: 100000, co2_kg: 1000 }

  it('explains plans with same CO2 (zero difference and difference below 0.05 kg)', () => {
    const zeroDiff = { label: 'w 0', cost_bdt: 101776, co2_kg: 1000 }
    expect(explainInefficientPlan(zeroDiff, better)).toBe(
      'The w 0 plan costs 1,776 BDT more and saves no CO2 compared with w 0.05 - 1, so it is not an efficient choice.',
    )
    const belowTolerance = { label: 'w 0', cost_bdt: 101776, co2_kg: 1000.04 }
    expect(explainInefficientPlan(belowTolerance, better)).toBe(
      'The w 0 plan costs 1,776 BDT more and saves no CO2 compared with w 0.05 - 1, so it is not an efficient choice.',
    )
  })

  it('explains plans with higher cost and higher CO2', () => {
    const moreCo2 = { label: 'w 0', cost_bdt: 101776, co2_kg: 1005 }
    expect(explainInefficientPlan(moreCo2, better)).toBe(
      'The w 0 plan costs 1,776 BDT more and also emits 5 kg more CO2 compared with w 0.05 - 1, so it is not an efficient choice.',
    )
  })

  it('explains plans with same cost but higher CO2', () => {
    const sameCost = { label: 'w 0', cost_bdt: 100000, co2_kg: 1005 }
    expect(explainInefficientPlan(sameCost, better)).toBe(
      'The w 0 plan costs the same and emits 5 kg more CO2 compared with w 0.05 - 1, so it is not an efficient choice.',
    )
  })

  it('explains plans where cost and CO2 are both slightly more but below display rounding', () => {
    const roundedBelow = { label: 'w 0', cost_bdt: 100000.3, co2_kg: 1000.2 }
    expect(explainInefficientPlan(roundedBelow, better)).toBe(
      'The w 0 plan costs about the same and emits about the same CO2 compared with w 0.05 - 1, so it is not an efficient choice.',
    )
  })
})
