import { useState } from 'react'
import type { RefObject } from 'react'
import { api } from '../api/client'
import type { PlanResponse, SlotOverride } from '../api/client'
import type { BacktestData } from '../backtest/data'
import { useWhatIf } from '../hooks/useWhatIf'
import { slotTimes } from './timeOptions'
import { PlanSkeleton } from './Skeleton'
import PlanSummary from './PlanSummary'
import ImpactTable from './ImpactTable'

type Row = { id: number; time: string; battery: string; generator: string }

function validate(rows: Row[], assets: PlanResponse['assets']): string[] {
  const errors: string[] = []
  if (rows.length === 0 || rows.length > 8) errors.push('Add between 1 and 8 override rows.')
  if (new Set(rows.map(row => row.time)).size !== rows.length) errors.push('Override times must be unique.')
  rows.forEach((row, index) => {
    const prefix = `Row ${index + 1}: `
    if (!slotTimes.includes(row.time)) errors.push(`${prefix}choose a time on the 15-minute grid.`)
    if (row.battery === '' && row.generator === '') errors.push(`${prefix}enter at least one battery or generator decision.`)
    if (row.battery !== '' && (!Number.isFinite(Number(row.battery)) || Number(row.battery) < -assets.battery.max_discharge_kw || Number(row.battery) > assets.battery.max_charge_kw)) errors.push(`${prefix}Battery must be between -${assets.battery.max_discharge_kw} and ${assets.battery.max_charge_kw} kW.`)
    const generator = Number(row.generator)
    if (row.generator !== '' && (!Number.isFinite(generator) || generator < 0 || (generator !== 0 && (generator < assets.generator.min_kw || generator > assets.generator.rated_kw)))) errors.push(`${prefix}Generator must be 0 or between ${assets.generator.min_kw} and ${assets.generator.rated_kw} kW.`)
  })
  return errors
}

export default function OverridePanel({ date, weight, baseline, source, onSeeLimits, cooldown }: { date: string; weight: number; cooldown: RefObject<number>; baseline: PlanResponse | null; source: BacktestData; onSeeLimits: () => void }) {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<Row[]>([])
  const [nextId, setNextId] = useState(1)
  const [errors, setErrors] = useState<string[]>([])
  const { data, error, loading, retryAfter, run, clear, retry } = useWhatIf(api.plan, cooldown)
  function edit(id: number, field: 'time' | 'battery' | 'generator', value: string) {
    clear()
    setErrors([])
    setRows(previous => previous.map(row => row.id === id ? { ...row, [field]: value } : row))
  }
  const unsafe = !!data && (!data.safe || !data.risk_ok || data.plan.risk?.ok === false || data.override?.feasible === false)
  return <section className="panel what-if-panel" aria-label="Operator overrides">
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary onClick={event => { event.preventDefault(); setOpen(!open) }}>Operator overrides</summary>
      <div hidden={!open}>
      <p>Simulated operator decisions on a historical replay day. Up to 8 slots; blank fields leave that asset to the optimiser.</p>
      <p className="muted">The Explainer always explains the default optimiser plan, including while overrides are shown.</p>
      {!baseline && <p role="status">Wait for the current optimiser plan to load asset limits.</p>}
      <form noValidate onSubmit={event => {
        event.preventDefault()
        if (!baseline) return
        const validation = validate(rows, baseline.assets)
        setErrors(validation)
        if (validation.length) return
        const overrides: SlotOverride[] = rows.map(row => ({ time: row.time,
          ...(row.battery === '' ? {} : { battery_kw: Number(row.battery) }),
          ...(row.generator === '' ? {} : { generator_kw: Number(row.generator) }),
        }))
        void run({ date, w: weight, overrides })
      }}>
        {rows.map((row, index) => <fieldset className="override-row" key={row.id}>
          <legend>Override {index + 1}</legend>
          <label>Time<select aria-label={`Override time, row ${index + 1}`} value={row.time} onChange={event => edit(row.id, 'time', event.target.value)}>{slotTimes.map(slot => <option key={slot}>{slot}</option>)}</select></label>
          <label>Battery kW (+ charge / - discharge)<input type="number" step="any" aria-label={`Battery kW, row ${index + 1} (+ charge / - discharge)`} value={row.battery} min={baseline ? -baseline.assets.battery.max_discharge_kw : undefined} max={baseline?.assets.battery.max_charge_kw} onChange={event => edit(row.id, 'battery', event.target.value)} /></label>
          <label>Generator kW<input type="number" step="any" min={0} max={baseline?.assets.generator.rated_kw} aria-label={`Generator kW, row ${index + 1}`} value={row.generator} onChange={event => edit(row.id, 'generator', event.target.value)} /><span className="muted">0 = off; otherwise {baseline ? `${baseline.assets.generator.min_kw}–${baseline.assets.generator.rated_kw} kW` : 'within asset limits'}</span></label>
          <button type="button" aria-label={`Remove override ${index + 1}`} onClick={() => { clear(); setErrors([]); setRows(previous => previous.filter(value => value.id !== row.id)) }}>Remove row</button>
        </fieldset>)}
        <div className="what-if-actions">
          <button type="button" disabled={rows.length >= 8 || !baseline} onClick={() => {
            clear(); setErrors([])
            setRows(previous => [...previous, { id: nextId, time: slotTimes.find(time => !previous.some(row => row.time === time)) ?? '00:00', battery: '', generator: '' }])
            setNextId(value => value + 1)
          }}>Add override</button>
          <button type="submit" disabled={!baseline || loading || retryAfter > 0}>Re-solve with overrides</button>
          <button type="button" onClick={() => { clear(); setRows([]); setErrors([]) }}>Back to the optimiser's plan</button>
        </div>
      </form>
      {errors.length > 0 && <div className="warning" role="alert"><ul>{errors.map(message => <li key={message}>{message}</li>)}</ul></div>}
      {error && <div className="warning" role="alert"><p>{error.message}</p>{retryAfter > 0 && <p role="status">Try again in {retryAfter} seconds.</p>}<button type="button" disabled={retryAfter > 0 || loading} onClick={retry}>Retry overrides</button></div>}
      {retryAfter > 0 && !error && <p role="status">Try again in {retryAfter} seconds.</p>}
      {loading && <div aria-busy="true"><p role="status">Re-solving with operator overrides…</p><PlanSkeleton /></div>}
      {data && <section role="region" aria-label="Plan with your overrides">
        <h3>Plan with your overrides</h3>
        <p className="muted">Simulated override result · {data.date}, w = {data.weight.toFixed(2)}</p>
        {data.override ? <aside className="override-impact" aria-label="Override impact">
          <p>Feasible: {data.override.feasible ? 'Yes' : 'No'}</p>
          <ImpactTable before={data.override.baseline} after={data.plan.totals} delta={data.override.delta} unsafe={unsafe} override />
        </aside> : <p className="warning" role="alert">The server did not return override impact data.</p>}
        <PlanSummary plan={data} source={source} selectedDate={date} onSeeLimits={onSeeLimits} comparison={false} />
      </section>}
      </div>
    </details>
  </section>
}
