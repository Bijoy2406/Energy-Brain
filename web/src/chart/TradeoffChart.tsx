import { useId } from 'react'
import { CartesianGrid, ComposedChart, Line, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import type { PlotGroup } from '../tradeoff/tradeoffData'
import { axisScale } from '../tradeoff/tradeoffData'
import { formatBdt, formatKg, formatHours, formatLitres } from '../formatting'
import { useReducedMotion } from '../hooks/useReducedMotion'

function PointTooltip({ group }: { group?: PlotGroup }) {
  if (!group) return null
  return <div className="chart-tooltip"><strong>{group.label}</strong><dl>
    <div><dt>Cost</dt><dd>{formatBdt(group.cost_bdt)} BDT</dd></div>
    <div><dt>CO2</dt><dd>{formatKg(group.co2_kg)} kg</dd></div>
    <div><dt>Generator hours</dt><dd>{formatHours(group.generator_hours)} h</dd></div>
    <div><dt>Diesel</dt><dd>{formatLitres(group.diesel_litres)} litres</dd></div>
  </dl><p>{group.efficient ? 'efficient' : 'not efficient'} · {group.safe ? 'safe' : 'NOT safe'} · {group.status}</p></div>
}

export default function TradeoffChart({ groups, frontier, currentGroup, onSelect }: {
  groups: PlotGroup[]; frontier: PlotGroup[]; currentGroup?: number; onSelect: (group: PlotGroup) => void
}) {
  const id = useId()
  const reducedMotion = useReducedMotion()
  const x = axisScale(groups.map(g => g.cost_bdt)), y = axisScale(groups.map(g => g.co2_kg))
  return <section aria-label="Cost versus CO2 chart" aria-describedby={id}>
    <p id={id} className="muted">Cost increases to the right; CO2 increases upwards. Filled dots are efficient; hollow dots are not efficient. The line joins efficient plans. A ring marks your slider. Select a dot with a click or Enter to inspect its setting.</p>
    <div className="chart-frame tradeoff-chart">
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <ComposedChart accessibilityLayer aria-label="Cost (BDT) versus CO2 (kg), one dot per distinct plan" margin={{ top: 50, right: 40, bottom: 30, left: 12 }}>
          <CartesianGrid stroke="var(--line)" />
          <XAxis type="number" dataKey="cost_bdt" domain={x.domain} ticks={x.ticks} tickFormatter={formatBdt} tick={{ fill: 'var(--muted)', fontSize: 10 }} minTickGap={18}
            label={{ value: 'Cost (BDT)', position: 'bottom', fill: 'var(--muted)', fontSize: 12 }} />
          <YAxis type="number" dataKey="co2_kg" domain={y.domain} ticks={y.ticks} tickFormatter={formatKg} width={58} tick={{ fill: 'var(--muted)', fontSize: 10 }}
            label={{ value: 'CO2 (kg)', angle: -90, position: 'insideLeft', offset: -8, fill: 'var(--muted)', fontSize: 12 }} />
          <Line data={frontier} dataKey="co2_kg" stroke="var(--accent)" dot={false} activeDot={false} isAnimationActive={!reducedMotion} animationDuration={300} tooltipType="none" />
          <Scatter data={groups} name="Distinct plans" isAnimationActive={false} shape={(props: unknown) => {
            const { cx, cy, payload: group } = props as { cx: number; cy: number; payload: PlotGroup }
            if (!group) return <g />
            return <g className="tradeoff-dot" role="button" tabIndex={0} aria-label={`Inspect ${group.label}, ${formatBdt(group.cost_bdt)} BDT, ${formatKg(group.co2_kg)} kg CO2, ${group.efficient ? 'efficient' : 'not efficient'}`}
              onClick={() => onSelect(group)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(group) } }}>
              <title>{`${group.label}: ${formatBdt(group.cost_bdt)} BDT, ${formatKg(group.co2_kg)} kg CO2, ${formatHours(group.generator_hours)} generator hours, ${formatLitres(group.diesel_litres)} litres, ${group.efficient ? 'efficient' : 'not efficient'}`}</title>
              <circle cx={cx} cy={cy} r={22} fill="transparent" />
              {group.group === currentGroup && <circle className="slider-ring" cx={cx} cy={cy} r={13} fill="none" stroke="var(--chart-demand)" strokeWidth={2} />}
              <circle className="plan-dot" cx={cx} cy={cy} r={7} fill={group.efficient ? 'var(--accent)' : 'var(--surface)'} stroke="var(--accent)" strokeWidth={2} />
              <text x={cx} y={cy - 25} textAnchor="middle" fill="var(--chart-demand)" fontSize={11} pointerEvents="none">{group.label}</text>
              {!group.efficient && <text x={cx} y={cy + 32} textAnchor="middle" fill="var(--chart-demand)" fontSize={11} pointerEvents="none">not efficient</text>}
            </g>
          }} />
          <Tooltip isAnimationActive={!reducedMotion} content={({ active, payload }) => <PointTooltip group={active ? payload?.[0]?.payload as PlotGroup : undefined} />} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  </section>
}
