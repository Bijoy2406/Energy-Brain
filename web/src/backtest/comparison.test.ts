import { describe, expect, it } from 'vitest'
import { compareDailyCost, savingWords } from './comparison'
import { backtestDaysFixture, backtestFixture } from '../test/backtestFixture'

describe('daily cost comparison', () => {
  it('matches the API percentage and aggregate saved BDT using adjusted daily costs', () => {
    const results = backtestDaysFixture().days.map((day) => compareDailyCost(
      day.methods.baseline_solar_first.adjusted_cost_bdt, day.methods.optimiser.adjusted_cost_bdt,
    )!)
    const summary = backtestFixture().summary.by_weight['0.5'].vs.baseline_solar_first
    expect(results[0]).toEqual({ difference: 20, percent: summary.cost_saved_pct })
    expect(Math.abs(results.reduce((sum, value) => sum + value.difference, 0) - summary.cost_saved_bdt)).toBeLessThan(1)
  })
  it('preserves negative savings and handles zero and missing baseline costs', () => {
    expect(compareDailyCost(100, 110)).toEqual({ difference: -10, percent: -10 })
    expect(compareDailyCost(0, 10)).toEqual({ difference: -10, percent: null })
    expect(compareDailyCost(null, 10)).toBeNull()
    expect(compareDailyCost(100, null)).toBeNull()
  })
  it('keeps the unrounded difference and percentage for consistency checks', () => {
    const result = compareDailyCost(169145.81, 153157.6373)!
    expect(result.difference).toBe(15988.172699999996)
    expect(result.percent).toBe(result.difference / 169145.81 * 100)
  })
})

describe('comparison wording', () => {
  it.each([
    [0.03, 'about the same'], [-0.04, 'about the same'], [0, 'about the same'],
    [0.049999, 'about the same'], [-0.049999, 'about the same'],
    [0.05, '0.1 % lower'], [-0.05, '0.1 % higher'],
    [4.7, '4.7 % lower'], [-0.3, '0.3 % higher'],
  ])('describes %s percent as %s for cost and CO2', (value, expected) => {
    expect(savingWords(value, 'cost')).toBe(`${expected} cost`)
    expect(savingWords(value, 'CO2')).toBe(`${expected} CO2`)
  })
  it('keeps missing comparisons unavailable', () => {
    expect(savingWords(null, 'cost')).toBe('cost comparison unavailable')
  })
})
