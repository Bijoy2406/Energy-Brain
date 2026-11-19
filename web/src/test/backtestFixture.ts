import type { components } from '../api/generated'

export const baselineNames = {
  baseline_solar_first: 'solar-first / charge-on-arrival rule',
  baseline_time_of_use: 'simple time-of-use rule',
}
export const caveats = ['Synthetic load, simulated PV, synthetic outages.', 'Observed weather as input; not a true day-ahead forecast.', 'Baselines use actual solar and load.']
const point = (cost: number, co2 = 20) => ({ adjusted_cost_bdt: cost, adjusted_co2_kg: co2, cost_bdt: cost - 10, co2_kg: co2 - 1 })
export function backtestDaysFixture(weight = 0.5): components['schemas']['BacktestDaysResponse'] {
  return { weight, headline_weight: 0.5, weights: [0.5, 1, 0], baseline_names: baselineNames, caveats,
    days: ['2024-07-01', '2024-07-02'].map((date, index) => ({
      date, weekday: index ? 'Tue' : 'Mon', workday: true, outage_slots: index ? 6 : 0,
      included: true, excluded_reason: null, forecast_fallback_used: false,
      methods: { optimiser: point(weight === 1 ? 70 : weight === 0 ? 110 : 80),
        baseline_solar_first: point(100), baseline_time_of_use: point(90) },
    })),
  }
}
const split = (days: number, cost: number) => ({ days, cost_saved_pct: cost, co2_saved_pct: -0.3 })
const comparison = (cost: number, saved: number) => ({
  cost_saved_pct: cost, co2_saved_pct: -0.3, cost_saved_bdt: saved, co2_saved_kg: -8.388,
  generator_hours_saved: 56, diesel_litres_saved: -12.25, days_cost_worse: 3, days_co2_worse: 5,
  per_day_cost_saved_pct: { min: -2, median: cost, max: 30 },
  outage_days: split(138, 25), no_outage_days: split(228, -2), workdays: split(250, 20), off_days: split(116, 10),
})
export function backtestFixture() {
  const byWeight = Object.fromEntries([0.5, 1, 0].map((weight) => [String(weight), {
    weight, totals: {}, vs: {
      baseline_solar_first: comparison(weight === 1 ? 30 : weight === 0 ? -10 : 20, weight === 1 ? 60 : weight === 0 ? -20 : 40),
      baseline_time_of_use: comparison(11.1111, 20),
    }, plan_health: { days_planned: 366, not_optimal: 0, unsafe: 0, risk_not_ok: 0, replanned: 2, fallback_used: 0 },
  }]))
  return { report: 'small test fixture', schema_version: 1, caveats,
    headline: { weight: 0.5, baseline_names: baselineNames, lines: [] },
    summary: { days: { requested: 366, included: 366, excluded: 0, excluded_dates: [], outage_days: 138, workdays: 250 },
      headline_weight: 0.5, by_weight: byWeight,
      forecast: { label: caveats[1], days: 366, solar_mae_kw: 12.1234, load_mae_kw: 8.1234,
        solar_p10_p90_coverage_pct: 90.3, solar_daylight_p10_p90_coverage_pct: 80.1, load_p10_p90_coverage_pct: 88.4,
        coverage_definition: 'Share of slots with p10 <= actual <= p90.' } },
    config: { first_day: '2024-01-01', last_day: '2024-12-31', n_days: 366, test_year: 2024,
      train_years: [2022, 2023], headline_weight: 0.5, git: { commit: 'abcdef1234567890' },
      provenance: { signals: 'Grid factor 0.62 kg/kWh (test source); Diesel factor 2.68 kg/litre (test source)',
        load: 'Synthetic office load', pv: 'Simulated PV', weather: 'Observed weather', outages: 'Synthetic outage calendar' } }, run: {},
  }
}
