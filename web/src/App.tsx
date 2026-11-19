import { useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import type { ApiResult, DayInfo, PlanResponse } from './api/client'
import ScheduleCharts from './chart/ScheduleCharts'
import { formatBdt, formatKg, formatHours } from './chart/scheduleData'
import WeightSlider from './components/WeightSlider'
import AnimatedValue from './components/AnimatedValue'
import ExplainerPanel from './components/ExplainerPanel'
import './App.css'

function PlanSummary({ plan }: { plan: PlanResponse }) {
  const warnings = [...new Set([
    ...plan.banners,
    ...(plan.forecast_fallback.used ? [plan.forecast_fallback.note ?? 'Forecast fallback used.'] : []),
    ...(plan.signal_fallback.used ? [plan.signal_fallback.note ?? 'Signal fallback used.'] : []),
  ])]

  return <>
    {(!plan.safe || warnings.length > 0) && (
      <section className={`warning ${!plan.safe ? 'unsafe' : ''}`} role="alert" aria-label="Plan warnings">
        <h3>{!plan.safe ? 'NOT SAFE TO EXECUTE' : 'Plan warnings'}</h3>
        {!plan.safe && <p>Diagnostic only. This plan does not meet every hard constraint and must not be executed.</p>}
        {warnings.length > 0 && <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
      </section>
    )}
    <div className="plan-status">
      <span className="status-badge">{plan.plan.status}</span>
      <span className={`status-badge ${plan.safe ? 'safe-badge' : 'unsafe-badge'}`}>{plan.safe ? 'Safe' : 'NOT safe'}</span>
      {plan.cached && <span className="status-badge">cached</span>}
      {plan.weight !== plan.weight_requested && <span className="status-badge">Solved at w = {plan.weight.toFixed(2)}</span>}
    </div>
    <dl className="summary-cards">
      <div tabIndex={0}><dt>Cost · BDT</dt><dd><AnimatedValue value={plan.plan.totals.cost_bdt} format={formatBdt} /></dd></div>
      <div tabIndex={0}><dt>CO2 · kg</dt><dd><AnimatedValue value={plan.plan.totals.co2_kg} format={formatKg} /></dd></div>
      <div tabIndex={0}><dt>Generator hours · h</dt><dd><AnimatedValue value={plan.plan.totals.generator_hours} format={formatHours} /></dd></div>
    </dl>
    <p className="muted">Totals are reported directly by the planner.</p>
    <ScheduleCharts response={plan} />
  </>
}

function PlanPanel({ date, weight, onPlan }: { date: string; weight: number; onPlan: (plan: PlanResponse) => void }) {
  const request = useMemo(() => ({ date, w: weight }), [date, weight])
  const [state, setState] = useState<{
    request: typeof request
    result: ApiResult<PlanResponse>
    previousPlan: PlanResponse | null
  } | null>(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => {
      void api.plan(request, controller.signal).then((result) => {
        if (!active || (!result.ok && result.error.kind === 'aborted')) return
        if (result.ok) onPlan(result.data)
        setState((previous) => ({
          request, result,
          previousPlan: result.ok ? result.data : previous?.previousPlan ?? null,
        }))
      })
    }, 250)
    return () => {
      active = false
      clearTimeout(timer)
      controller.abort()
    }
  }, [request, onPlan])

  const loading = state?.request !== request
  const plan = state?.previousPlan
  const error = !loading && state && !state.result.ok && state.result.error.kind !== 'aborted' ? state.result.error : null
  return <>
    {loading && <p role="status">Loading plan…{plan && ` Showing previous plan: ${plan.date}.`}</p>}
    {error && <p className="warning" role="alert">{error.message}</p>}
    {error && plan && <p className="muted">Showing previous plan: {plan.date}.</p>}
    <section aria-label="Plan results" aria-busy={loading} className={loading || error ? 'plan-results is-stale' : 'plan-results'}>
      {plan && <PlanSummary plan={plan} />}
    </section>
  </>
}

export default function App() {
  const [days, setDays] = useState<ApiResult<DayInfo[]> | null>(null)
  const [selectedDate, setSelectedDate] = useState('')
  const [weight, setWeight] = useState(0.5)
  const [shownPlan, setShownPlan] = useState<PlanResponse | null>(null)
  const selectedDay = days?.ok ? days.data.find((day) => day.date === selectedDate) : undefined

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    void api.days(controller.signal).then((response) => {
      if (!active || (!response.ok && response.error.kind === 'aborted')) return
      if (!response.ok) {
        setDays(response)
        return
      }
      const available = response.data.days
      setSelectedDate(available.find((day) => day.date === '2024-07-02')?.date ?? available[0]?.date ?? '')
      setDays({ ok: true, data: available })
    })
    return () => { active = false; controller.abort() }
  }, [])

  return (
    <div className="app-shell">
      <header className="page-header">
        <div className="brand-mark" aria-hidden="true">EB</div>
        <div><p className="eyebrow">SITE ENERGY PLANNING</p><h1>Energy Brain</h1></div>
        <span className="replay-label">Historical replay</span>
      </header>
      <p className="honesty-strip">Synthetic load, simulated PV, synthetic outages. Historical replay.</p>
      <main>
        <section className="day-toolbar" aria-label="Day selection">
          <div><h2>Daily overview</h2><p className="muted">Review the planner’s cost and carbon results.</p></div>
          {!days && <p role="status">Loading days…</p>}
          {days && !days.ok && days.error.kind !== 'aborted' && <p className="warning" role="alert">{days.error.message}</p>}
          {days?.ok && (days.data.length > 0 ? (
            <div className="day-field">
              <label htmlFor="planning-day">Planning day</label>
              <select id="planning-day" value={selectedDate} title={selectedDay && `${selectedDay.date} — ${selectedDay.label}`}
                onChange={(event) => setSelectedDate(event.target.value)}>
                {days.data.map((day) => <option key={day.date} value={day.date}>{day.date} — {day.label}</option>)}
              </select>
            </div>
          ) : <p>No planning days available.</p>)}
        </section>
        <div className="workspace">
          <section className="panel schedule-panel" aria-labelledby="schedule-title">
            <div className="panel-heading"><h2 id="schedule-title">Schedule dashboard</h2><span className="muted">Daily plan</span></div>
            <WeightSlider value={weight} onChange={setWeight} />
            {selectedDate && <PlanPanel date={selectedDate} weight={weight} onPlan={setShownPlan} />}
          </section>
          <ExplainerPanel date={selectedDate} weight={shownPlan?.weight ?? weight} />
        </div>
      </main>
      <footer>Energy Brain · Site time: Asia/Dhaka</footer>
    </div>
  )
}
