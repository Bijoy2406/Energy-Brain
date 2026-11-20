import { useState } from 'react'
import type { RefObject } from 'react'
import { api } from '../api/client'
import { useWhatIf } from '../hooks/useWhatIf'
import CloudEventChart from '../chart/CloudEventChart'
import ImpactTable from './ImpactTable'
import PlanWarnings from './PlanWarnings'
import { PlanSkeleton } from './Skeleton'
import { slotTimes } from './timeOptions'

export default function CloudEventPanel({ date, weight, open, onOpenChange, cooldown }: { date: string; weight: number; cooldown: RefObject<number>; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [time, setTime] = useState('13:00')
  const [drop, setDrop] = useState(60)
  const [duration, setDuration] = useState(120)
  const { data, error, loading, retryAfter, run, retry } = useWhatIf(api.simulateCloud, cooldown)
  const unsafe = !!data && (!data.safe || !data.risk_ok || !data.after.safe || !data.after.risk_ok || data.after.plan.risk?.ok === false)
  return <section className="panel what-if-panel" id="cloud-event-panel" aria-label="Cloud event">
    <p className="honesty-strip">A simulated what-if on a historical replay day, not live weather.</p>
    <details open={open} onToggle={event => onOpenChange(event.currentTarget.open)}>
      <summary onClick={event => { event.preventDefault(); onOpenChange(!open) }}>Cloud event</summary>
      <div hidden={!open}>
      <form onSubmit={event => { event.preventDefault(); void run({ date, w: weight, time, drop_pct: drop, duration_minutes: duration }) }}>
        <div className="what-if-controls">
          <label>Cloud start time<select value={time} onChange={event => setTime(event.target.value)}>{slotTimes.map(slot => <option key={slot}>{slot}</option>)}</select></label>
          <label>Solar drop %<input type="range" min={10} max={100} step={1} value={drop} onChange={event => setDrop(event.target.valueAsNumber)} /><output>{drop} %</output></label>
          <label>Cloud duration<select value={duration} onChange={event => setDuration(Number(event.target.value))}>{[1, 2, 3, 4].map(hours => <option key={hours} value={hours * 60}>{hours} {hours === 1 ? 'hour' : 'hours'}</option>)}</select></label>
        </div>
        <button type="submit" disabled={!date || loading || retryAfter > 0}>Trigger cloud event</button>
      </form>
      {retryAfter > 0 && !error && <p role="status">Try again in {retryAfter} seconds.</p>}
      {loading && <div aria-busy="true"><p role="status">Re-planning simulated cloud event…</p><PlanSkeleton /></div>}
      {error && <div className="warning" role="alert"><p>{error.message}</p>{retryAfter > 0 && <p role="status">Try again in {retryAfter} seconds.</p>}<button type="button" disabled={retryAfter > 0 || loading} onClick={retry}>Retry cloud event</button></div>}
      {data && <section role="region" aria-label="Simulated cloud event result">
        <h3>Simulated cloud event result</h3>
        <PlanWarnings plan={data.after} unsafe={unsafe} banners={data.banners} />
        <p>Re-planned in {data.replan_seconds.toFixed(1)} s</p>
        <CloudEventChart response={data} />
        <ImpactTable before={data.before.plan.totals} after={data.after.plan.totals} delta={data.delta} unsafe={unsafe} />
      </section>}
      </div>
    </details>
  </section>
}
