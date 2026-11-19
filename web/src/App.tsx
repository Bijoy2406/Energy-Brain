import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { ApiResult, DayInfo, PlanResponse } from './api/client'
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
    <dl className="summary-cards">
      <div><dt>status</dt><dd>{plan.plan.status}</dd></div>
      <div><dt>safe</dt><dd>{String(plan.safe)}</dd></div>
      <div><dt>cost_bdt</dt><dd>{plan.plan.totals.cost_bdt}</dd></div>
      <div><dt>co2_kg</dt><dd>{plan.plan.totals.co2_kg}</dd></div>
    </dl>
    <p className="muted">Totals are reported directly by the planner.</p>
  </>
}

function PlanPanel({ date }: { date: string }) {
  const [result, setResult] = useState<ApiResult<PlanResponse> | null>(null)

  useEffect(() => {
    let active = true
    void api.plan({ date, w: 0.5 }).then((response) => {
      if (active) setResult(response)
    })
    return () => { active = false }
  }, [date])

  if (!result) return <p role="status">Loading plan…</p>
  if (!result.ok) return <p className="warning" role="alert">{result.error.message}</p>
  return <PlanSummary plan={result.data} />
}

export default function App() {
  const [days, setDays] = useState<ApiResult<DayInfo[]> | null>(null)
  const [selectedDate, setSelectedDate] = useState('')

  useEffect(() => {
    let active = true
    void api.days().then((response) => {
      if (!active) return
      if (!response.ok) {
        setDays(response)
        return
      }
      const available = response.data.days
      setSelectedDate(available.find((day) => day.date === '2024-07-02')?.date ?? available[0]?.date ?? '')
      setDays({ ok: true, data: available })
    })
    return () => { active = false }
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
          {days && !days.ok && <p className="warning" role="alert">{days.error.message}</p>}
          {days?.ok && (days.data.length > 0 ? (
            <div className="day-field">
              <label htmlFor="planning-day">Planning day</label>
              <select id="planning-day" value={selectedDate} onChange={(event) => setSelectedDate(event.target.value)}>
                {days.data.map((day) => <option key={day.date} value={day.date}>{day.date} — {day.label}</option>)}
              </select>
            </div>
          ) : <p>No planning days available.</p>)}
        </section>
        <div className="workspace">
          <section className="panel schedule-panel" aria-labelledby="schedule-title">
            <div className="panel-heading"><h2 id="schedule-title">Schedule dashboard</h2><span className="muted">Daily plan</span></div>
            {selectedDate && <PlanPanel key={selectedDate} date={selectedDate} />}
            <div className="placeholder"><h3>Schedule view</h3><p>The daily schedule will appear here.</p></div>
          </section>
          <aside className="panel chat-panel" aria-labelledby="chat-title">
            <p className="eyebrow">PLAN EXPLAINER</p><h2 id="chat-title">Ask about your plan</h2>
            <p className="muted">Chat is coming soon. Explanations will use the planner’s results.</p>
            <div className="chat-placeholder">Your plan’s story, backed by its numbers.</div>
          </aside>
        </div>
      </main>
      <footer>Energy Brain · Site time: Asia/Dhaka</footer>
    </div>
  )
}
