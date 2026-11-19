import type { components } from '../api/generated'

export const baselineNames = {
  baseline_solar_first: 'solar-first / charge-on-arrival rule',
  baseline_time_of_use: 'simple time-of-use rule',
}
export const caveats = [
  "Out-of-sample: test days are in 2024; the forecast models were trained on 2022-2023 (reports/forecast_metrics.json).",
  "Synthetic load: the factory base load is synthetic (data.synthetic_load, #7); no real meter data.",
  "Simulated PV: solar output is simulated with pvlib from NASA POWER / Open-Meteo weather (#6), not metered.",
  "Synthetic outages: the load-shedding calendar is a seeded synthetic calendar (#9), not a utility record.",
  "Forecast MAE and P10-P90 coverage are measured with observed weather as forecast input (tier-0), NOT a true day-ahead weather forecast, so real-world error would be larger.",
  "Because the forecast is near-perfect here, settlement against actuals changes little; this backtest does not measure robustness to real forecast error.",
  "Baselines are built on ACTUAL solar and load (real-time rules, perfect information) and settled with the same recourse as the optimiser: conservative for the optimiser.",
  "EV: both baselines charge the vans from 00:00 (off-peak) because today's required energy is due before 07:00; a literal 'charge on arrival' at 18:00 would pay the peak tariff. This understates the savings by about 144 kWh x 3.29 BDT (about 474 BDT) per day, so the comparison is conservative.",
  "Headline cost and CO2 are ADJUSTED: an end-of-day battery shortfall (end SoC below the start) is charged at the off-peak tariff / one-way efficiency and the grid CO2 factor; raw figures are reported too.",
  "HVAC runs at its occupied minimum in every method (no thermal model), so HVAC is not a source of savings.",
  "Settlement reuses the Risk agent's shortfall recourse: when actual solar beats the plan with no gap to close, the extra solar is curtailed instead of replacing grid import. This only penalises the optimiser (the baselines are built on actuals).",
  "The grid CO2 factor is flat (no hourly grid-carbon data), so shifting grid use in time cannot cut CO2; CO2 changes come only from diesel vs grid and battery round-trip losses."
]
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
      forecast: { label: caveats[4], days: 366, solar_mae_kw: 12.1234, load_mae_kw: 8.1234,
        solar_p10_p90_coverage_pct: 90.3, solar_daylight_p10_p90_coverage_pct: 80.1, load_p10_p90_coverage_pct: 88.4,
        coverage_definition: 'Share of slots with p10 <= actual <= p90.' } },
    config: { first_day: '2024-01-01', last_day: '2024-12-31', n_days: 366, test_year: 2024,
      train_years: [2022, 2023], headline_weight: 0.5, git: { commit: 'abcdef1234567890' },
      provenance: { signals: 'Grid factor 0.62 kg/kWh (test source); Diesel factor 2.68 kg/litre (test source)',
        load: 'Synthetic office load', pv: 'Simulated PV', weather: 'Observed weather', outages: 'Synthetic outage calendar' } }, run: {},
  }
}
