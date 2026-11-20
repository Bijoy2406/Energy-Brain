import type { PlanResponse } from '../api/client'
import type { BacktestData } from '../backtest/data'
import ScheduleCharts from '../chart/ScheduleCharts'
import { formatBdt, formatKg, formatHours } from '../formatting'
import AnimatedValue from './AnimatedValue'
import SameDayComparison from './SameDayComparison'
import PlanWarnings from './PlanWarnings'

export default function PlanSummary({ plan, source, selectedDate, onSeeLimits, comparison = true }: { plan: PlanResponse; source: BacktestData; selectedDate: string; onSeeLimits: () => void; comparison?: boolean }) {
  const safe = plan.safe && plan.risk_ok && plan.plan.risk?.ok !== false && plan.override?.feasible !== false
  return <>
    <PlanWarnings plan={plan} />
    <div className="plan-status">
      <span className="status-badge">{plan.plan.status}</span>
      <span className={`status-badge ${safe ? 'safe-badge' : 'unsafe-badge'}`}>{safe ? 'Safe' : 'NOT safe'}</span>
      {plan.cached && <span className="status-badge">cached</span>}
      {plan.weight !== plan.weight_requested && <span className="status-badge">Solved at w = {plan.weight.toFixed(2)}</span>}
    </div>
    <dl className="summary-cards">
      <div tabIndex={0}><dt>Cost · BDT</dt><dd><AnimatedValue value={plan.plan.totals.cost_bdt} format={formatBdt} /></dd></div>
      <div tabIndex={0}><dt>CO2 · kg</dt><dd><AnimatedValue value={plan.plan.totals.co2_kg} format={formatKg} /></dd></div>
      <div tabIndex={0}><dt>Generator hours · h</dt><dd><AnimatedValue value={plan.plan.totals.generator_hours} format={formatHours} /></dd></div>
    </dl>
    <p className="muted">Totals are reported directly by the planner.</p>
    {comparison && <SameDayComparison plan={plan} source={source} selectedDate={selectedDate} onSeeLimits={onSeeLimits} />}
    <ScheduleCharts response={safe ? plan : { ...plan, safe: false }} />
  </>
}

