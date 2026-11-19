import type { ParetoPoint, ParetoResponse } from '../tradeoff/tradeoffData'
export function paretoFixture(overrides: Partial<ParetoResponse> = {}): ParetoResponse {
  const point = (weight: number, group: number, cost_bdt: number, co2_kg: number, efficient = true): ParetoPoint => ({
    weight, group, cost_bdt, co2_kg, efficient, generator_hours: 2, diesel_litres: 100,
    status: 'optimal', safe: true, risk_ok: true, replans: 0, same_as_weight: null, plan_cached: false,
  })
  const points = [point(0, 0, 102000, 990), point(0.4, 1, 100000, 1000), point(1, 1, 100000, 1000)]
  return { date: '2024-07-02', weights_meaning: '1 = cheapest, 0 = cleanest',
    basis: 'planned totals at the P50 forecast, before settlement against actuals',
    points, frontier: [points[1], points[0]], forecast_fallback: { used: false, note: null, fields: null },
    signal_fallback: { used: false, note: null, fields: null }, banners: [], forecast_source: 'model',
    cached: false, computed_at: '2026-11-19T00:00:00Z', solve_seconds: 1.2, distinct_points: 2, ...overrides }
}
