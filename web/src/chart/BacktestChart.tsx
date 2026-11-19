import { useId, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { BacktestData, Baseline } from '../backtest/data'
import { useBacktestDays } from '../backtest/data'
import { compareDailyCost } from '../backtest/comparison'
import { formatBdt } from '../formatting'
import { useReducedMotion } from '../hooks/useReducedMotion'

type ChartRow = { date: string; weekday: string; outage: boolean; baselineCost: number; optimiserCost: number; difference: number }
function DailyTooltip({ row, name }: { row?: ChartRow; name: string }) {
  if (!row) return null
  return <div className="chart-tooltip">
    <strong>{row.date} · {row.weekday}</strong>
    <p>{row.outage ? 'Load-shedding day (hatched bar)' : 'Other day (solid bar)'}</p>
    <dl>
      <div><dt>{name}</dt><dd>{formatBdt(row.baselineCost)} BDT</dd></div>
      <div><dt>Energy Brain</dt><dd>{formatBdt(row.optimiserCost)} BDT</dd></div>
      <div><dt>Energy Brain cost vs {name}</dt><dd>{formatBdt(Math.abs(row.difference))} BDT {row.difference >= 0 ? 'less' : 'more'}</dd></div>
    </dl>
  </div>
}

export default function BacktestChart({ source, weight }: { source: BacktestData; weight: number }) {
  const [baseline, setBaseline] = useState<Baseline>('baseline_solar_first')
  const result = useBacktestDays(source, weight, true)
  const reducedMotion = useReducedMotion()
  const id = useId().replace(/:/g, '')
  const rows = useMemo(() => {
    if (!result?.ok) return []
    return result.data.days.flatMap((day): ChartRow[] => {
      if (!day.included) return []
      const baselineCost = day.methods[baseline]?.adjusted_cost_bdt ?? null
      const optimiserCost = day.methods.optimiser?.adjusted_cost_bdt ?? null
      const saving = compareDailyCost(baselineCost, optimiserCost)
      return saving && baselineCost !== null && optimiserCost !== null
        ? [{ date: day.date, weekday: day.weekday, outage: day.outage_slots > 0, baselineCost, optimiserCost, difference: saving.difference }] : []
    })
  }, [result, baseline])
  const ticks = rows.filter((row, index) => index === 0 || row.date.slice(0, 7) !== rows[index - 1].date.slice(0, 7)).map((row) => row.date)
  const month = (date: string) => new Date(`${date}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' })
  const name = result?.ok ? result.data.baseline_names[baseline] : 'selected baseline'

  return <section className="panel daily-chart-panel" aria-label="Daily cost comparison">
    <h2>Daily cost comparison</h2>
    <div className="segmented-control" role="group" aria-label="Daily comparison baseline">
      <button type="button" aria-pressed={baseline === 'baseline_solar_first'} onClick={() => setBaseline('baseline_solar_first')}>Solar-first</button>
      <button type="button" aria-pressed={baseline === 'baseline_time_of_use'} onClick={() => setBaseline('baseline_time_of_use')}>Time-of-use</button>
    </div>
    {!result ? <p role="status">Loading daily backtest values for w = {weight}…</p>
      : !result.ok ? <p className="warning" role="alert">{result.error.kind !== 'aborted' && result.error.message}</p>
        : <>
          <h3 id={`${id}-label`}>Daily cost saved vs {name} (computed from the daily values returned by the API)</h3>
          <p className="muted">Adjusted costs · BDT · w = {result.data.weight}. Positive bars mean Energy Brain costs less than {name}; negative bars mean it costs more. Excluded days and missing settlements are omitted.</p>
          <ul className="chart-legend" aria-label="Daily chart legend">
            <li><span className="backtest-outage-swatch" aria-hidden="true" />Load-shedding days: hatched bars</li>
            <li><span className="backtest-other-swatch" aria-hidden="true" />Other days: solid bars</li>
          </ul>
          {rows.length ? <div className="chart-frame daily-cost-chart">
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <BarChart data={rows} accessibilityLayer aria-label={`Daily cost saved in BDT vs ${name}`} margin={{ top: 10, right: 8, bottom: 0, left: 0 }} barCategoryGap={0}>
                <defs><pattern id={`${id}-outage`} width="5" height="5" patternUnits="userSpaceOnUse">
                  <rect width="5" height="5" fill="var(--backtest-outage)" />
                  <path d="M-1,1 l2,-2 M0,5 l5,-5 M4,6 l2,-2" stroke="var(--surface)" strokeWidth="1.5" />
                </pattern></defs>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="date" ticks={ticks} tickFormatter={month} minTickGap={12} tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} />
                <YAxis width={58} tickFormatter={formatBdt} tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} />
                <ReferenceLine y={0} stroke="var(--muted)" />
                <Bar dataKey="difference" name={`Cost saved vs ${name} (BDT)`} isAnimationActive={!reducedMotion} animationDuration={300}>
                  {rows.map((row) => <Cell key={row.date} fill={row.outage ? `url(#${id}-outage)` : 'var(--backtest-other)'} />)}
                </Bar>
                <Tooltip isAnimationActive={!reducedMotion} content={({ active, payload }) => <DailyTooltip row={active ? payload?.[0]?.payload as ChartRow : undefined} name={name} />} />
              </BarChart>
            </ResponsiveContainer>
          </div> : <p>No included daily settlements available.</p>}
          <p className="muted">synthetic data, 2024 replay</p>
        </>}
  </section>
}
