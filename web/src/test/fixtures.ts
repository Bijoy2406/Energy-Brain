import type { components } from '../api/generated'

type Schemas = components['schemas']
const slots = () => Array<number>(96).fill(0)
const flags = () => Array<boolean>(96).fill(false)
const start = '2024-07-02T00:00:00+06:00'

export const days: Schemas['DaysResponse'] = {
  first_day: '2022-01-01', last_day: '2026-11-20',
  days: ['2024-07-01', '2024-07-02'].map((date) => ({
    date, weekday: date === '2024-07-02' ? 'Tue' : 'Mon', workday: true,
    weekly_off: false, holiday: false, outage: false, outage_slots: 0,
    outage_blocks: [], label: date === '2024-07-02' ? 'Demo day: monsoon' : 'Ordinary workday',
    demo: date === '2024-07-02', dynamic: false, forecast_tier: 'model',
    forecast_tier_source: 'planned', signal_fallback: false, in_backtest: true,
  })),
}

// Complete API-shaped test data. These numbers never appear in the application.
export function planFixture(overrides: Partial<Schemas['PlanResponse']> = {}): Schemas['PlanResponse'] {
  return {
    date: '2024-07-02', weight: 0.5, weight_requested: 0.5, safe: true, risk_ok: true,
    banners: [], forecast_fallback: { used: false, note: null, fields: null },
    signal_fallback: { used: false, note: null, fields: null }, forecast_source: 'model',
    cached: false, computed_at: start,
    plan: {
      start, weight: 0.5, status: 'optimal', risk: null, solve_seconds: 0.1,
      reserve_margin_kwh: 0, fallback_used: false, notes: [],
      schedule: {
        grid_import_kw: slots(), solar_used_kw: slots(), solar_curtailed_kw: slots(),
        battery_charge_kw: slots(), battery_discharge_kw: slots(), battery_soc_kwh: slots(),
        hvac_kw: slots(), ev_charge_kw: slots(), generator_kw: slots(), base_load_kw: slots(),
      },
      totals: {
        cost_bdt: 152702.707123, co2_kg: 9092.852456, grid_kwh: 0, solar_kwh: 0,
        generator_kwh: 0, generator_hours: 0, diesel_litres: 0,
        slot_cost_bdt: slots(), slot_co2_kg: slots(),
      },
    },
    forecast: {
      start, solar: { p10_kw: slots(), p50_kw: slots(), p90_kw: slots() },
      load: { p10_kw: slots(), p50_kw: slots(), p90_kw: slots() }, fallback_used: false, note: null,
    },
    signals: {
      start, tariff_bdt_per_kwh: slots(), grid_co2_kg_per_kwh: slots(),
      diesel_price_bdt_per_l: slots(), diesel_co2_kg_per_l: slots(), outage: flags(),
    },
    assets: {
      start, grid: { max_import_kw: 1 },
      battery: { capacity_kwh: 1, max_charge_kw: 1, max_discharge_kw: 1, round_trip_efficiency: 1,
        soc_min_kwh: 0, soc_max_kwh: 1, initial_soc_kwh: 0 },
      hvac: { rated_kw: 1, min_kw_when_occupied: 0, occupied: flags(), comfort_min_c: 22,
        comfort_max_c: 26, max_precool_hours: 0 },
      ev: { plugged_in: flags(), charger_kw: 0, energy_required_kwh: 0, arrival_slot: 0, departure_slot: 95 },
      generator: { rated_kw: 1, min_kw: 0, fuel_l_per_kwh: 0.3 },
    },
    ...overrides,
  }
}
