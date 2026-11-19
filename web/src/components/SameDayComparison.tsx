import type { PlanResponse } from '../api/client'
import { baselines, useBacktestDays } from '../backtest/data'
import type { BacktestData } from '../backtest/data'
import { compareDailyCost, savingWords } from '../backtest/comparison'
import { formatBdt, formatKg } from '../formatting'
import BacktestCaveats from './BacktestCaveats'

export default function SameDayComparison({ plan, source, selectedDate }: { plan: PlanResponse; source: BacktestData; selectedDate: string }) {
  const outside = selectedDate < '2024-01-01' || selectedDate > '2024-12-31'
  const current = plan.date === selectedDate
  const supported = [0, 0.5, 1].includes(plan.weight)
  const result = useBacktestDays(source, !outside && supported && current ? plan.weight : null)
  const day = result?.ok ? result.data.days.find((row) => row.date === selectedDate) : undefined
  const explanation = outside ? 'Date outside the backtest.' : !current ? `Waiting for the solved plan for ${selectedDate}.` : !supported ? 'Move the slider to 0, 0.5 or 1 to compare this day.' : null
  return <section className="same-day-card chart-section" aria-label="Baseline vs Energy Brain, same day">
    <h3>Baseline vs Energy Brain, same day</h3>
    <p className="muted">synthetic data, 2024 replay · {selectedDate}{current && ` · solved w = ${plan.weight}`}</p>
    {explanation ? <p className="muted">{explanation} The backtest covers 2024 days at w = 0, 0.5 and 1.</p>
      : !result ? <p role="status">Loading same-day comparison…</p>
        : !result.ok ? <p className="muted">Same-day comparison unavailable. {result.error.kind !== 'aborted' && result.error.message}</p>
          : !day ? <p className="muted">Date outside the backtest returned by the API. The backtest covers 2024 days at w = 0, 0.5 and 1.</p>
            : !day.included ? <p className="muted">This day was excluded: {day.excluded_reason ?? 'No settlement available.'}</p>
              : <>
                <table className="comparison-table">
                  <caption>Adjusted backtest settlement for {day.date}</caption>
                  <thead><tr><th scope="col">Method</th><th scope="col">Cost (BDT)</th><th scope="col">CO2 (kg)</th></tr></thead>
                  <tbody>{(['optimiser', ...baselines] as const).map((key) => {
                    const method = day.methods[key]
                    const name = key === 'optimiser' ? 'Energy Brain' : result.data.baseline_names[key]
                    const saving = key === 'optimiser' ? null : compareDailyCost(method?.adjusted_cost_bdt ?? null, day.methods.optimiser?.adjusted_cost_bdt ?? null)
                    return <tr key={key}>
                      <th scope="row">{name}{saving && <span className="daily-saving">
                        Energy Brain {saving.difference >= 0 ? 'saves' : 'costs'} {formatBdt(Math.abs(saving.difference))} BDT{saving.difference < 0 && ' more'}
                        {' '}({saving.percent === null ? 'percentage unavailable: zero baseline cost' : savingWords(saving.percent, 'cost')})
                      </span>}</th>
                      <td>{method?.adjusted_cost_bdt == null ? 'Unavailable' : formatBdt(method.adjusted_cost_bdt)}</td>
                      <td>{method?.adjusted_co2_kg == null ? 'Unavailable' : formatKg(method.adjusted_co2_kg)}</td>
                    </tr>
                  })}</tbody>
                </table>
                <p className="muted">Adjusted figures price any battery end-of-day shortfall. These are historical settled values.</p>
              </>}
    {result?.ok && !explanation && <BacktestCaveats caveats={result.data.caveats} />}
  </section>
}
