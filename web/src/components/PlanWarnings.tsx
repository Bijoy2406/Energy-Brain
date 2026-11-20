import type { PlanResponse } from '../api/client'

export default function PlanWarnings({ plan, unsafe = false, banners = [] }: { plan: PlanResponse; unsafe?: boolean; banners?: string[] }) {
  const notSafe = unsafe || !plan.safe || !plan.risk_ok || plan.plan.risk?.ok === false || plan.override?.feasible === false
  const warnings = [...new Set([
    ...banners, ...plan.banners,
    ...(plan.forecast_fallback.used ? [plan.forecast_fallback.note ?? 'Forecast fallback used.'] : []),
    ...(plan.signal_fallback.used ? [plan.signal_fallback.note ?? 'Signal fallback used.'] : []),
  ])]
  if (!notSafe && warnings.length === 0) return null
  return <section className={`warning ${notSafe ? 'unsafe' : ''}`} role="alert" aria-label="Plan warnings">
    <h3>{notSafe ? 'NOT SAFE TO EXECUTE' : 'Plan warnings'}</h3>
    {notSafe && <p>Diagnostic only. This plan does not meet every hard constraint and must not be executed.</p>}
    {warnings.length > 0 && <ul>{warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>}
  </section>
}
