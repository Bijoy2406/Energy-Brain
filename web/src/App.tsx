import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client'
import type { ApiResult, DayInfo, PlanResponse } from './api/client'
import PlanSummary from './components/PlanSummary'
import CloudEventPanel from './components/CloudEventPanel'
import OverridePanel from './components/OverridePanel'
import DemoGuide from './components/DemoGuide'
import WeightSlider from './components/WeightSlider'
import ExplainerPanel from './components/ExplainerPanel'
import BacktestView from './components/BacktestView'
import TradeoffView from './components/TradeoffView'
import { PlanSkeleton, SelectSkeleton } from './components/Skeleton'
import { createBacktestData } from './backtest/data'
import type { BacktestData } from './backtest/data'
import HeaderAtmosphere from './components/HeaderAtmosphere'
import ShinyText from './fx/ShinyText'
import { useReducedMotion } from './hooks/useReducedMotion'
import { useDashboardMotion } from './hooks/useDashboardMotion'
import ThemeToggle from './components/ThemeToggle'
import './App.css'


type SolvedPlan = { response: PlanResponse; requestedWeight: number }

function PlanPanel({ date, weight, onPlan, source, onSeeLimits }: { date: string; weight: number; onPlan: (plan: SolvedPlan) => void; source: BacktestData; onSeeLimits: () => void }) {
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
        if (result.ok) onPlan({ response: result.data, requestedWeight: request.w })
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
      {plan ? <PlanSummary plan={plan} source={source} selectedDate={date} onSeeLimits={onSeeLimits} /> : loading && <PlanSkeleton />}
    </section>
  </>
}

type Tab = 'plan' | 'backtest' | 'tradeoff'
const tabOrder: Tab[] = ['plan', 'tradeoff', 'backtest']
const hashTab = (): Tab => window.location.hash === '#tradeoff' ? 'tradeoff' : ['#backtest', '#limits'].includes(window.location.hash) ? 'backtest' : 'plan'

export default function App() {
  const [backtestData] = useState(createBacktestData)
  const [tabs, setTabs] = useState(() => {
    const tab = hashTab()
    return { tab, visited: new Set<Tab>(['plan', tab]), limitsRequest: window.location.hash === '#limits' ? 1 : 0 }
  })
  const shell = useRef<HTMLDivElement>(null)
  const reducedMotion = useReducedMotion()
  useDashboardMotion(shell, tabs.tab)
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([])
  const [days, setDays] = useState<ApiResult<DayInfo[]> | null>(null)
  const [selectedDate, setSelectedDate] = useState('')
  const [weight, setWeight] = useState(0.5)
  const [shownPlan, setShownPlan] = useState<SolvedPlan | null>(null)
  const [demoStep, setDemoStep] = useState<number | null>(null)
  const [cloudOpen, setCloudOpen] = useState(false)
  const [cloudDemo, setCloudDemo] = useState(0)
  const cloudCooldown = useRef(0)
  const overrideCooldown = useRef(0)
  const demoToggle = useRef<HTMLButtonElement>(null)
  const currentPlan = shownPlan?.response.date === selectedDate && shownPlan.requestedWeight === weight ? shownPlan.response : null
  const selectedDay = days?.ok ? days.data.find((day) => day.date === selectedDate) : undefined

  useEffect(() => {
    const syncHash = () => setTabs((previous) => {
      const tab = hashTab()
      return { tab, visited: new Set([...previous.visited, tab]), limitsRequest: window.location.hash === '#limits' ? previous.limitsRequest + 1 : 0 }
    })
    window.addEventListener('hashchange', syncHash)
    return () => window.removeEventListener('hashchange', syncHash)
  }, [])

  function selectTab(tab: Tab) {
    setTabs((previous) => ({ tab, visited: new Set([...previous.visited, tab]), limitsRequest: 0 }))
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${tab === 'plan' ? '' : `#${tab}`}`)
  }

  function seeLimits() {
    setTabs((previous) => ({ tab: 'backtest', visited: new Set([...previous.visited, 'backtest' as const]), limitsRequest: previous.limitsRequest + 1 }))
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#limits`)
  }

  function goDemoStep(step: number) {
    setDemoStep(step)
    if (step === 0) {
      if (days?.ok && days.data.some(day => day.date === '2024-07-02')) setSelectedDate('2024-07-02')
      setWeight(0.5)
    }
    if (step === 5) seeLimits()
    else selectTab('plan')
    if (step === 3) {
      setCloudDemo(value => value + 1)
      setCloudOpen(true)
    }
  }

  function closeDemo() {
    setDemoStep(null)
    demoToggle.current?.focus()
  }

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
    <div className="app-shell" ref={shell}>
      <header className="page-header">
        <HeaderAtmosphere />
        <div className="header-inner container-wrap">
          <div className="brand-cluster">
            <h1 className="brand-heading">
              <span className="brand-lockup" aria-label="Gridpilot AI">
                <ShinyText className="brand-sheen" color="transparent" shineColor="var(--on-brand)" speed={3} delay={5} shineWidth={18} disabled={reducedMotion}>
                  <img src="/logo-mark-light.png" alt="" className="brand-logo" />
                </ShinyText>
                <span className="brand-name" aria-hidden="true">Gridpilot <strong>AI</strong></span>
              </span>
            </h1>
            <span className="brand-subtitle">DHAKA MICROGRID · AI DISPATCH</span>
          </div>
          <div className="header-meta">
            <ThemeToggle />
            <button type="button" ref={demoToggle} className="meta-pill demo-toggle" aria-pressed={demoStep !== null}
              onClick={() => { if (demoStep === null) goDemoStep(0); else closeDemo() }}>Demo mode</button>
            <span className="meta-pill">1,000 kWh BESS</span>
            <span className="meta-pill">96 Slots · 15m</span>
            <span className="replay-label"><span className="replay-dot" aria-hidden="true" />Historical replay</span>
          </div>
        </div>
        {demoStep !== null && <DemoGuide step={demoStep} onStep={goDemoStep} onClose={closeDemo} onPareto={() => selectTab('tradeoff')} />}
      </header>
      <div className="page-body container-wrap">
        <p className="honesty-strip">
          <svg className="strip-icon" viewBox="0 0 20 20" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
          <span>Synthetic load, simulated PV, synthetic outages. Historical replay.</span>
        </p>
        <main>
          <div className="page-tabs" role="tablist" aria-label="Gridpilot AI views">
            {["Today's plan", 'Trade-off', 'Backtest results'].map((label, index) => <button key={label} type="button" role="tab"
              id={`${tabOrder[index]}-tab`} aria-controls={`${tabOrder[index]}-panel`}
              aria-selected={tabs.tab === tabOrder[index]} tabIndex={tabs.tab === tabOrder[index] ? 0 : -1}
              ref={(element) => { tabButtons.current[index] = element }}
              onClick={() => selectTab(tabOrder[index])} onKeyDown={(event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                event.preventDefault()
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3
                selectTab(tabOrder[next])
                tabButtons.current[next]?.focus()
              }}>{label}</button>)}
          </div>
          <div role="tabpanel" id="plan-panel" aria-labelledby="plan-tab" hidden={tabs.tab !== 'plan'} tabIndex={0}>
          <section className="day-toolbar" aria-label="Day selection">
            <div><h2>Daily overview</h2><p className="muted">Review the planner’s cost and carbon results.</p></div>
            {!days && <div className="day-field"><p role="status" className="skeleton-status">Loading days…</p><SelectSkeleton /></div>}
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
              <button type="button" className="limits-link" onClick={() => selectTab('tradeoff')}>See the trade-off curve</button>
              {!days && <PlanSkeleton />}
              {selectedDate && <PlanPanel date={selectedDate} weight={weight} onPlan={setShownPlan} source={backtestData} onSeeLimits={seeLimits} />}
            </section>
          </div>
          <CloudEventPanel key={`cloud-${selectedDate}-${weight}-${cloudDemo}`} date={selectedDate} weight={weight} cooldown={cloudCooldown} open={cloudOpen} onOpenChange={setCloudOpen} />
          <OverridePanel key={`overrides-${selectedDate}-${weight}`} date={selectedDate} weight={weight} cooldown={overrideCooldown} baseline={currentPlan} source={backtestData} onSeeLimits={seeLimits} />
          </div>
          <div role="tabpanel" id="tradeoff-panel" aria-labelledby="tradeoff-tab" hidden={tabs.tab !== 'tradeoff'} tabIndex={0}>
            {tabs.visited.has('tradeoff') && <TradeoffView date={selectedDate} days={days?.ok ? days.data : []} weight={weight}
              active={tabs.tab === 'tradeoff'} onDateChange={setSelectedDate} onUseSetting={(value) => { setWeight(value); selectTab('plan') }} />}
          </div>
          <div role="tabpanel" id="backtest-panel" aria-labelledby="backtest-tab" hidden={tabs.tab !== 'backtest'} tabIndex={0}>
            {tabs.visited.has('backtest') && <BacktestView source={backtestData} limitsRequest={tabs.tab === 'backtest' ? tabs.limitsRequest : 0} onSeeLimits={seeLimits} />}
          </div>
        </main>
      </div>
      <ExplainerPanel demoAsk={demoStep === 4} date={selectedDate} weight={shownPlan?.response.date === selectedDate && shownPlan.requestedWeight === weight ? shownPlan.response.weight : weight} />
      <footer className="site-footer">
        <div className="footer-inner container-wrap">
          <div className="footer-grid">
            <div className="footer-brand">
              <span className="brand-lockup footer-lockup" aria-hidden="true"><img src="/logo-mark-light.png" alt="" className="footer-logo" /><span className="brand-name">Gridpilot <strong>AI</strong></span></span>
              <p className="footer-title">Dhaka Commercial Microgrid</p>
              <p className="footer-desc">
                Multi-agent dispatch optimizer scheduling solar PV, battery storage, EV charging, and diesel backup within HVAC comfort limits.
              </p>
            </div>
            <div className="footer-col">
              <h3>Intelligence Stack</h3>
              <ul>
                <li>MILP Cost &amp; Carbon Planner</li>
                <li>LightGBM P10/P50/P90 Forecasters</li>
                <li>Risk Verification &amp; LLM Explainer</li>
              </ul>
            </div>
            <div className="footer-col">
              <h3>Operational Scope</h3>
              <ul>
                <li>15-Minute Resolution (96 Slots/Day)</li>
                <li>Peak Tariff Window: 17:00–23:00 BST</li>
                <li>2024 Full-Year Backtest Replay</li>
              </ul>
            </div>
          </div>
          <div className="footer-bottom">
            <span>Gridpilot AI · Site time: Asia/Dhaka</span>
            <div className="footer-signature" aria-hidden="true">
              <span className="swatch" style={{ background: '#0B2545' }} />
              <span className="swatch" style={{ background: '#2F6FDB' }} />
              <span className="swatch" style={{ background: '#F5A623' }} />
              <span className="swatch" style={{ background: '#14A38B' }} />
              <span className="swatch" style={{ background: '#C2410C' }} />
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}
