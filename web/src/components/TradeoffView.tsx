import { useState } from 'react'
import type { DayInfo } from '../api/client'
import TradeoffChart from '../chart/TradeoffChart'
import { TradeoffSkeleton } from './Skeleton'
import { formatBdt, formatKg, formatHours, formatPercent } from '../formatting'
import { usePareto } from '../hooks/usePareto'
import { explainInefficientPlan, formatWeight, frontierGroups, groupPoints, nearestWeight, tradeoffSummary } from '../tradeoff/tradeoffData'
import type { PlotGroup } from '../tradeoff/tradeoffData'

function changeWords(delta: { difference: number; percent: number | null }, unit: string) {
  if (delta.percent !== null && Math.abs(delta.percent) < 0.05) return 'about the same'
  const sign = delta.difference > 0 ? '+' : delta.difference < 0 ? '-' : ''
  return `${sign}${formatBdt(Math.abs(delta.difference))} ${unit}${delta.percent === null ? ' (percentage unavailable)' : ` (${sign}${formatPercent(Math.abs(delta.percent))} %)`}`
}

export default function TradeoffView({ date, days, weight, active, onDateChange, onUseSetting }: {
  date: string; days: DayInfo[]; weight: number; active: boolean; onDateChange: (date: string) => void; onUseSetting: (weight: number) => void
}) {
  const { data, loading, error, retryAfter, retry } = usePareto(date, active)
  const [selection, setSelection] = useState<{ date: string; group: number } | null>(null)
  const groups = data ? groupPoints(data) : []
  const frontier = data ? frontierGroups(data) : []
  const nearest = data ? nearestWeight(data, weight) : null
  const summary = data ? tradeoffSummary(data) : null
  const selected = selection?.date === data?.date ? groups.find(g => g.group === selection?.group) : undefined
  const stale = loading || !!error || data?.date !== date
  const selectGroup = (group: PlotGroup) => { if (!stale && data) setSelection({ date: data.date, group: group.group }) }
  return <div className="tradeoff-view">
    <section className="panel">
      <h2>Cost vs CO2 trade-off for {date || 'the selected day'}</h2>
      <p>Each dot is a distinct plan the optimiser can make by moving the slider. Plans that give the same cost and CO2 share one dot.</p>
      <div className="day-field"><label htmlFor="tradeoff-day">Trade-off day</label><select id="tradeoff-day" value={date} onChange={event => onDateChange(event.target.value)}>
        {days.map(day => <option key={day.date} value={day.date}>{day.date} — {day.label}</option>)}
      </select></div>
    </section>
    {loading && <p role="status">Solving the trade-off for this day... first load takes a few seconds</p>}
    {!date && <p role="status">Choose an available planning day to see the trade-off.</p>}
    {error && <div role="alert" className="warning"><p>{error.message}</p>
      {retryAfter > 0 && <p role="status">Try again in {retryAfter} seconds.</p>}
      <button type="button" disabled={retryAfter > 0 || loading} onClick={retry}>Retry</button>
    </div>}
    {!data && loading && <TradeoffSkeleton />}
    {data && <section className={`panel tradeoff-results ${stale ? 'is-stale' : ''}`} aria-label="Trade-off results" aria-busy={loading}>
      {stale && <p>Showing previous result: {data.date}. Settings are unavailable until this day finishes loading.</p>}
      <TradeoffChart groups={groups} frontier={frontier} currentGroup={nearest?.group} onSelect={selectGroup} />
      {nearest && <p className="weight-value">your slider: w = {formatWeight(weight)} (nearest solved weight {formatWeight(nearest.weight)}) · {groups.find(g => g.group === nearest.group)?.label}</p>}
      {summary ? <p className="tradeoff-summary">From the cheapest to the cleanest efficient plan: cost {changeWords(summary.cost, 'BDT')}, CO2 {changeWords(summary.co2, 'kg')} <span className="muted">(computed from the points above)</span></p>
        : frontier.length > 0 ? <><p>Every weight gives the same plan on this day, so there is no cost-versus-CO2 trade-off to show. The grid CO2 factor is flat on this site, so most days have only one to three distinct plans.</p>
          {days.some(day => day.date === '2024-03-09') && <button type="button" onClick={() => onDateChange('2024-03-09')}>Try the sunniest day (2024-03-09)</button>}</>
          : <p>No efficient safe plans are available for this day.</p>}
      {groups.filter(group => !group.efficient).map(group => {
        const better = frontier.find(point => point.cost_bdt <= group.cost_bdt && point.co2_kg <= group.co2_kg)
        return <p key={group.group}>{better
          ? explainInefficientPlan(group, better)
          : `The ${group.label} plan is not efficient${!group.safe ? ' and is NOT safe to execute' : ''}. Status: ${group.status}.`}</p>
      })}
      {selected && <section className="tradeoff-selection" aria-label="Selected setting">
        <h3>w = {selected.label.slice(2)}: cost {formatBdt(selected.cost_bdt)} BDT, CO2 {formatKg(selected.co2_kg)} kg</h3>
        <p>{formatHours(selected.generator_hours)} generator hours · {selected.safe ? 'safe' : 'NOT safe'} · {selected.status}</p>
        <button type="button" disabled={stale || !selected.safe} onClick={() => onUseSetting(selected.weights[0])}>Use this setting</button>
      </section>}
      <details className="tradeoff-table"><summary>Plan data table</summary>
        <table><caption>API planned values for {data.date}; one row per distinct plan</caption><thead><tr>
          {['Weights', 'Cost (BDT)', 'CO2 (kg)', 'Generator hours', 'Efficient', 'Safe'].map(label => <th scope="col" key={label}>{label}</th>)}
        </tr></thead><tbody>{groups.map(group => <tr key={group.group}><th scope="row">{group.label}</th><td>{formatBdt(group.cost_bdt)}</td><td>{formatKg(group.co2_kg)}</td><td>{formatHours(group.generator_hours)}</td><td>{group.efficient ? 'Yes' : 'No — not efficient'}</td><td>{group.safe ? 'Yes' : 'No'}</td></tr>)}</tbody></table>
      </details>
      <aside className="tradeoff-notes" aria-label="Trade-off basis and limitations">
        <p>{data.basis}</p><p>{data.weights_meaning}</p>
        <p>The grid CO2 factor is flat, so shifting grid use in time barely changes CO2; the CO2 differences come from diesel use and battery losses</p>
        <p>These are forecast plan comparisons before settlement, not measured savings. Only the sampled weights are shown.</p>
        {data.date >= '2022-01-01' && data.date <= '2024-12-31' && <p>synthetic data, 2024 replay</p>}
        {data.banners.map((banner, index) => <p className="warning" key={index}>{banner}</p>)}
        {data.forecast_fallback.used && <p>Forecast fallback: {data.forecast_fallback.note ?? 'Forecast fallback used.'}</p>}
        {data.signal_fallback.used && <p>Signal fallback: {data.signal_fallback.note ?? 'Signal fallback used.'}</p>}
      </aside>
    </section>}
  </div>
}
