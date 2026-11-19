import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ApiResult } from '../api/client'
import type { components } from '../api/generated'

export const baselines = ['baseline_solar_first', 'baseline_time_of_use'] as const
export type Baseline = typeof baselines[number]
export type DailyResponse = components['schemas']['BacktestDaysResponse']
export type DayRow = components['schemas']['BacktestDayRow']
type Split = { days: number; cost_saved_pct: number | null; co2_saved_pct: number | null }
export type Comparison = {
  cost_saved_pct: number | null; co2_saved_pct: number | null; cost_saved_bdt: number; co2_saved_kg: number
  generator_hours_saved: number; diesel_litres_saved: number; days_cost_worse: number
  per_day_cost_saved_pct: { min: number | null; median: number | null; max: number | null }
  outage_days: Split; no_outage_days: Split
}
export type WeightSummary = {
  weight: number; vs: Record<Baseline, Comparison>
  plan_health: { days_planned: number; not_optimal: number; unsafe: number; risk_not_ok: number; replanned: number; fallback_used: number }
}
// The generated contract deliberately leaves summary/config open. Refine those two
// fields locally to the documented evaluator report without changing generated types.
export type Report = Omit<components['schemas']['BacktestSummary'], 'summary' | 'config'> & {
  summary: {
    days: { requested: number; included: number; excluded: number; excluded_dates: (string | { date: string; reason?: string; excluded_reason?: string })[]; outage_days: number; workdays: number }
    by_weight: Record<string, WeightSummary>
    forecast: { label: string; days: number; solar_mae_kw: number; load_mae_kw: number; solar_p10_p90_coverage_pct: number; solar_daylight_p10_p90_coverage_pct: number; load_p10_p90_coverage_pct: number; coverage_definition: string }
  }
  config: { first_day: string; last_day: string; test_year: number; train_years: number[]; git: { commit: string | null }; provenance: Record<string, string> }
}

export function createBacktestData() {
  let report: Promise<ApiResult<Report>> | undefined
  const daily = new Map<number, { promise: Promise<ApiResult<DailyResponse>>; pending: boolean }>()
  return {
    report() {
      // One request per mounted application, including React Strict Mode effect replay.
      report ??= api.backtest().then((result) => result as ApiResult<Report>)
      return report
    },
    days(weight: number, refresh = false) {
      const previous = daily.get(weight)
      if (previous && (!refresh || previous.pending)) return previous.promise
      const entry = { promise: api.backtestDays({ weight }), pending: true }
      daily.set(weight, entry)
      void entry.promise.then(() => { entry.pending = false })
      return entry.promise
    },
  }
}
export type BacktestData = ReturnType<typeof createBacktestData>

export function useBacktestReport(source: BacktestData) {
  const [result, setResult] = useState<ApiResult<Report> | null>(null)
  useEffect(() => {
    let active = true
    void source.report().then((response) => { if (active) setResult(response) })
    return () => { active = false }
  }, [source])
  return result
}

export function useBacktestDays(source: BacktestData, weight: number | null, refresh = false) {
  const [state, setState] = useState<{ weight: number; result: ApiResult<DailyResponse> } | null>(null)
  useEffect(() => {
    if (weight === null) return
    let active = true
    void source.days(weight, refresh).then((result) => { if (active) setState({ weight, result }) })
    return () => { active = false }
  }, [source, weight, refresh])
  return state?.weight === weight ? state.result : null
}
