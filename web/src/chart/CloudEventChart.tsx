import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { CloudResponse } from '../api/client'
import { buildScheduleRows } from './scheduleData'
import { formatKw, formatKwh } from '../formatting'
import { useReducedMotion } from '../hooks/useReducedMotion'

const series = [
  { key: 'grid', label: 'Grid import', color: 'var(--c-blue)', unit: 'kW', format: formatKw, axis: 'power' },
  { key: 'generator', label: 'Generator', color: 'var(--c-orange)', unit: 'kW', format: formatKw, axis: 'power' },
  { key: 'soc', label: 'Battery SoC', color: 'var(--c-teal)', unit: 'kWh', format: formatKwh, axis: 'soc' },
] as const

export default function CloudEventChart({ response }: { response: CloudResponse }) {
  const reducedMotion = useReducedMotion()
  const before = buildScheduleRows(response.before)
  const after = buildScheduleRows(response.after)
  const data = before.map((row, slot) => ({ slot, time: row.time, gridBefore: row.grid, gridAfter: after[slot]?.grid,
    generatorBefore: row.generator, generatorAfter: after[slot]?.generator, socBefore: row.soc, socAfter: after[slot]?.soc }))
  if (data.length) data.push({ ...data[data.length - 1], slot: data.length, time: '24:00' })
  const { start, end } = response.affected_slots
  return <section className="chart-section" role="region" aria-label="Before and after cloud event chart">
    <h3>Whole-day simulated cloud comparison</h3>
    <p className="muted">Site time: Asia/Dhaka. Dashed = before; solid = after. Muted prefix before {response.event.time} is frozen; the remaining day is re-planned. Amber shading marks the affected window. Battery SoC is end of slot, on the right axis.</p>
    <ul className="chart-legend" aria-label="Cloud chart legend">{series.map(s => <li key={s.key}><span style={{ background: s.color }} aria-hidden="true" />{s.label} ({s.unit})</li>)}</ul>
    <div className="chart-frame cloud-chart">
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart data={data} accessibilityLayer aria-label="Cloud event power and battery SoC" margin={{ top: 12, right: 0, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="slot" type="number" domain={[0, before.length]} ticks={[0, 24, 48, 72, 96]} tickFormatter={slot => slot === 96 ? '24:00' : before[slot]?.time ?? ''} tick={{ fill: 'var(--muted)', fontSize: 10 }} />
          <YAxis yAxisId="power" width={40} tick={{ fill: 'var(--muted)', fontSize: 10 }} />
          <YAxis yAxisId="soc" orientation="right" width={44} tick={{ fill: 'var(--muted)', fontSize: 10 }} />
          <ReferenceArea yAxisId="power" x1={0} x2={start} fill="var(--outage-shade)" fillOpacity={0.2} />
          <ReferenceArea yAxisId="power" x1={start} x2={end} fill="var(--peak-shade)" fillOpacity={0.25} />
          <ReferenceLine yAxisId="power" x={start} stroke="var(--muted)" strokeDasharray="3 3" />
          {series.flatMap(s => ['Before', 'After'].map(phase => <Line key={`${s.key}${phase}`} dataKey={`${s.key}${phase}`} name={`${s.label} ${phase.toLowerCase()}`} yAxisId={s.axis} type="stepAfter" stroke={s.color} strokeWidth={phase === 'After' ? 2 : 1.5} strokeDasharray={phase === 'Before' ? '6 3' : undefined} dot={false} isAnimationActive={!reducedMotion} animationDuration={300} />))}
          <Tooltip isAnimationActive={!reducedMotion} content={({ active, payload }) => {
            const row = active ? payload?.[0]?.payload as typeof data[number] | undefined : undefined
            if (!row) return null
            return <div className="chart-tooltip"><strong>{row.time} · Asia/Dhaka</strong><dl>{series.flatMap(s => (['Before', 'After'] as const).map(phase => {
              const value = row[`${s.key}${phase}`]
              return <div key={`${s.key}${phase}`}><dt>{s.label} {phase.toLowerCase()}</dt><dd>{value === undefined ? 'Unavailable' : `${s.format(value)} ${s.unit}`}</dd></div>
            }))}</dl></div>
          }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  </section>
}
