import { useId, useMemo } from 'react'
import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceArea,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { PlanResponse } from '../api/client'
import { buildScheduleRows, formatKw, formatKwh, outageBlocks, peakSlots } from './scheduleData'
import type { ScheduleRow } from './scheduleData'
import { useReducedMotion } from '../hooks/useReducedMotion'

// Four series colours that stay distinguishable for colour-blind readers (checked with protan, deutan and tritan
// simulation): amber and crimson are the brand colours, teal and blue were added for battery and grid.
const sources = [
  { key: 'solar', name: 'Solar', color: '#F6AA1C' },
  { key: 'batteryDischarge', name: 'Battery discharge', color: '#1FA896' },
  { key: 'grid', name: 'Grid import', color: '#2F5FA8' },
  { key: 'generator', name: 'Generator', color: '#941B0C' },
] as const

function SlotTooltip({ row, battery }: { row?: ScheduleRow; battery: boolean }) {
  if (!row) return null
  return <div className="chart-tooltip">
    <strong>{row.time} · Asia/Dhaka</strong>
    <dl>
      {battery ? <div><dt>Battery SoC (end of slot)</dt><dd>{formatKwh(row.soc)} kWh</dd></div> : <>
        {sources.map((source) => <div key={source.key}>
          <dt>{source.name}</dt><dd>{formatKw(row[source.key])} kW</dd>
        </div>)}
        <div><dt>Demand incl. battery charging</dt><dd>{formatKw(row.demand)} kW</dd></div>
      </>}
    </dl>
    {row.outage && <p>Grid outage</p>}
  </div>
}

export default function ScheduleCharts({ response }: { response: PlanResponse }) {
  const id = useId()
  const reducedMotion = useReducedMotion()
  const animation = { isAnimationActive: !reducedMotion, animationBegin: 0, animationDuration: 300, animationEasing: 'ease' as const }
  const rows = useMemo(() => buildScheduleRows(response), [response])
  const data = useMemo(() => {
    const points = rows.map((row, slot) => ({ ...row, slot }))
    // Extend the last slot's step to its end boundary without inventing a slot
    // or changing the planner's rows, totals, or end-of-slot SoC values.
    if (rows.length > 0) points.push({ ...rows[rows.length - 1], slot: rows.length })
    return points
  }, [rows])
  const outages = outageBlocks(rows)
  const peakTimes = new Set(peakSlots(rows).map((row) => row.time))
  const peaks = outageBlocks(rows.map((row) => ({ ...row, outage: peakTimes.has(row.time) })))
  const ticks = rows.flatMap((row, slot) => row.time.endsWith(':00') && Number(row.time.slice(0, 2)) % 3 === 0 ? [slot] : [])
  const xAxis = {
    dataKey: 'slot', type: 'number' as const, domain: [0, rows.length], ticks,
    tickFormatter: (slot: number) => rows[slot]?.time ?? '',
    tick: { fill: 'var(--muted)', fontSize: 10 }, minTickGap: 0,
    axisLine: false, tickLine: false, height: 30,
  }
  // Numeric boundaries shade the entire slot, even a single-slot outage at day end.
  const position = (time: string) => {
    const index = rows.findIndex((row) => row.time === time)
    return index < 0 ? rows.length : index
  }
  const outageAreas = outages.map(([from, to]) => <ReferenceArea key={from}
    x1={position(from)} x2={position(to)} fill="var(--outage-shade)" fillOpacity={0.3} />)
  const { soc_min_kwh: min, soc_max_kwh: max } = response.assets.battery

  return <div className={`schedule-charts ${response.safe ? '' : 'diagnostic-charts'}`}>
    {!response.safe && <p className="diagnostic-label">DIAGNOSTIC ONLY - NOT SAFE TO EXECUTE</p>}
    <div className="chart-plots">
      <section className="chart-section" role="region" aria-label="Energy supply and demand chart" aria-describedby={`${id}-energy-description`}>
        <h3>Energy supply &amp; demand <span className="muted">kW</span></h3>
        <p id={`${id}-energy-description`} className="muted chart-description">
          Stacked power sources and total demand including battery charging, in 15-minute slots. Site time: Asia/Dhaka.
        </p>
        <ul className="chart-legend" aria-label="Chart legend">
          {sources.map((source) => <li key={source.key}><span aria-hidden="true" style={{ background: source.color }} />{source.name}</li>)}
          <li><span className="demand-swatch" aria-hidden="true" />Demand incl. battery charging</li>
          <li><span className="outage-swatch" aria-hidden="true" />Grid outage</li>
          <li><span className="peak-swatch" aria-hidden="true" />Peak tariff</li>
        </ul>
        <div className="chart-frame energy-chart">
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <AreaChart data={data} syncId={id} margin={{ top: 12, right: 12, bottom: 0, left: 0 }} accessibilityLayer aria-label="Energy in kW">
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis {...xAxis} />
              <YAxis width={48} tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} />
              {peaks.map(([from, to]) => <ReferenceArea key={from} x1={position(from)} x2={position(to)}
                fill="var(--peak-shade)" fillOpacity={0.18} />)}
              {outageAreas}
              {sources.map((source) => <Area key={source.key} type="stepAfter" dataKey={source.key} name={source.name}
                stackId="supply" stroke={source.color} fill={source.color} fillOpacity={0.65} {...animation} />)}
              <Line type="stepAfter" dataKey="demand" name="Demand incl. battery charging" stroke="var(--chart-demand)"
                strokeWidth={2} strokeDasharray="6 3" dot={false} {...animation} />
              <Tooltip isAnimationActive={!reducedMotion} content={({ active, payload }) => <SlotTooltip row={active ? payload?.[0]?.payload as ScheduleRow : undefined} battery={false} />} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </section>
      <section className="chart-section" role="region" aria-label="Battery state of charge chart" aria-describedby={`${id}-battery-description`}>
        <h3>Battery state of charge <span className="muted">kWh</span></h3>
        <p id={`${id}-battery-description`} className="muted chart-description">Stored energy at the end of each slot. Dashed lines mark the minimum and maximum limits; grey bands mark grid outages.</p>
        <div className="chart-frame battery-chart">
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <LineChart data={data} syncId={id} margin={{ top: 16, right: 12, bottom: 0, left: 0 }} accessibilityLayer aria-label="Battery SoC in kWh">
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis {...xAxis} />
              <YAxis width={48} domain={[(dataMin: number) => Math.min(dataMin, min), (dataMax: number) => Math.max(dataMax, max)]}
                tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} />
              {outageAreas}
              <ReferenceLine y={min} stroke="var(--muted)" strokeDasharray="5 4" label={{ value: 'Min', position: 'insideBottomRight', fill: 'var(--muted)', fontSize: 11 }} />
              <ReferenceLine y={max} stroke="var(--muted)" strokeDasharray="5 4" label={{ value: 'Max', position: 'insideTopRight', fill: 'var(--muted)', fontSize: 11 }} />
              <Line type="linear" dataKey="soc" name="Battery SoC (end of slot)" stroke="#1FA896" strokeWidth={2.5} dot={false} {...animation} />
              <Tooltip isAnimationActive={!reducedMotion} content={({ active, payload }) => <SlotTooltip row={active ? payload?.[0]?.payload as ScheduleRow : undefined} battery />} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>
    <p className="muted chart-provenance">Synthetic load, simulated PV, synthetic outages. Historical replay.</p>
    <p className="muted">Forecast: {response.forecast_fallback.used ? 'fallback' : 'trained model'} ({response.forecast_source})</p>
  </div>
}
