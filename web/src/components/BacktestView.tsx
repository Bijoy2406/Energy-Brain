import { useRef, useState } from 'react'
import { baselines, useBacktestDays, useBacktestReport } from '../backtest/data'
import type { BacktestData, Comparison, Report, WeightSummary } from '../backtest/data'
import { savingWords } from '../backtest/comparison'
import { formatBdt, formatHours, formatInteger, formatKg, formatKw, formatLitres, formatPercent } from '../formatting'
import AnimatedValue from './AnimatedValue'
import BacktestCaveats from './BacktestCaveats'
import ReportDownload from './ReportDownload'
import BacktestChart from '../chart/BacktestChart'
import { useLimitsNavigation } from '../hooks/useLimitsNavigation'

function ComparisonCard({ name, values, requested, onSeeLimits }: { name: string; values: Comparison; requested: number; onSeeLimits: () => void }) {
  return <article tabIndex={0} className="panel comparison-card" aria-label={`Gridpilot AI vs ${name}`}>
    <p className="eyebrow">GRIDPILOT AI VS BASELINE</p>
    <h3>{name}</h3>
    <p className="comparison-result"><AnimatedValue value={values.cost_saved_bdt} format={amount => `${savingWords(values.cost_saved_pct, 'cost')} (${formatBdt(Math.abs(amount))} BDT ${values.cost_saved_bdt >= 0 ? 'less' : 'more'})`} /></p>
    <p className="comparison-result"><AnimatedValue value={values.co2_saved_kg} format={amount => `${savingWords(values.co2_saved_pct, 'CO2')} (${formatKg(Math.abs(amount))} kg ${values.co2_saved_kg >= 0 ? 'less' : 'more'})`} /></p>
    <p className="muted">Adjusted results vs {name} · synthetic data, 2024 replay. <button type="button" className="limits-link" onClick={onSeeLimits}>See limits</button></p>
    <ul className="comparison-facts">
      <li><AnimatedValue value={Math.abs(values.generator_hours_saved)} format={formatHours} /> {values.generator_hours_saved >= 0 ? 'fewer' : 'more'} generator hours vs {name}</li>
      <li><AnimatedValue value={Math.abs(values.diesel_litres_saved)} format={formatLitres} /> {values.diesel_litres_saved >= 0 ? 'fewer' : 'more'} diesel litres vs {name}</li>
      <li>Costlier than the baseline on {formatInteger(values.days_cost_worse)} of {formatInteger(requested)} days ({name})</li>
      <li>Median per-day cost saving vs {name}: {savingWords(values.per_day_cost_saved_pct.median, 'cost')}</li>
      <li>Load-shedding days ({formatInteger(values.outage_days.days)}): {savingWords(values.outage_days.cost_saved_pct, 'cost')} vs {name} / {savingWords(values.outage_days.co2_saved_pct, 'CO2')} vs {name}</li>
      <li>Other days ({formatInteger(values.no_outage_days.days)}): {savingWords(values.no_outage_days.cost_saved_pct, 'cost')} vs {name} / {savingWords(values.no_outage_days.co2_saved_pct, 'CO2')} vs {name}</li>
    </ul>
  </article>
}

function DaysCovered({ report, health }: { report: Report; health: WeightSummary['plan_health'] }) {
  const days = report.summary.days
  const checks = [[health.not_optimal, 'not optimal'], [health.unsafe, 'unsafe'], [health.risk_not_ok, 'Risk not ok']] as const
  return <section className="panel" aria-label="Days covered">
    <h2>Days covered</h2>
    <p>{formatInteger(days.included)} of {formatInteger(days.requested)} days included · {formatInteger(days.outage_days)} load-shedding days · {formatInteger(days.workdays)} workdays · {formatInteger(days.excluded)} excluded</p>
    {!!days.excluded_dates.length && <div><h3>Excluded dates</h3><ul>{days.excluded_dates.map((day, index) => <li key={index}>{typeof day === 'string' ? day : `${day.date}: ${day.reason ?? day.excluded_reason ?? 'excluded'}`}</li>)}</ul></div>}
    <p>{checks.every(([count]) => count === 0) ? 'All plans optimal / safe / Risk ok.' : `Plan health: ${checks.map(([count, label]) => `${formatInteger(count)} ${label}`).join(' / ')}.`}
      {' '}{formatInteger(health.days_planned)} days planned · {formatInteger(health.replanned)} replanned · {formatInteger(health.fallback_used)} fallback used</p>
    <p className="muted">synthetic data, 2024 replay</p>
  </section>
}

function ForecastQuality({ report }: { report: Report }) {
  const forecast = report.summary.forecast
  return <section className="panel" aria-label="Forecast quality">
    <h2>Forecast quality</h2>
    <dl className="forecast-metrics">
      <div tabIndex={0}><dt>Solar MAE</dt><dd><AnimatedValue value={forecast.solar_mae_kw} format={formatKw} /> kW</dd></div>
      <div tabIndex={0}><dt>Load MAE</dt><dd><AnimatedValue value={forecast.load_mae_kw} format={formatKw} /> kW</dd></div>
      <div tabIndex={0}><dt>Solar P10–P90 coverage</dt><dd><AnimatedValue value={forecast.solar_p10_p90_coverage_pct} format={formatPercent} /> %</dd></div>
      <div tabIndex={0}><dt>Solar daylight-only P10–P90 coverage</dt><dd><AnimatedValue value={forecast.solar_daylight_p10_p90_coverage_pct} format={formatPercent} /> %</dd></div>
      <div tabIndex={0}><dt>Load P10–P90 coverage</dt><dd><AnimatedValue value={forecast.load_p10_p90_coverage_pct} format={formatPercent} /> %</dd></div>
    </dl>
    <p className="muted">{forecast.label}</p>
    <p className="muted">{forecast.coverage_definition}</p>
    <p className="muted">synthetic data, 2024 replay</p>
  </section>
}

function Measurement({ report }: { report: Report }) {
  const { config } = report
  return <details className="panel measurement" open>
    <summary>How this was measured</summary>
    <h3>Solar-first rule</h3>
    <p>HVAC uses the occupied minimum. EVs charge at full charger power from the first required slot (00:00) until their energy is met. Solar serves load first, then charges the battery; the rest is curtailed without export. The battery normally charges from surplus solar only and covers load left by solar, down to the outage backup.</p>
    <h3>Time-of-use rule</h3>
    <p>HVAC also uses the occupied minimum. EVs use off-peak required slots first, then other required slots. Solar follows the same load-first rule. The battery also grid-charges off-peak: up to its maximum charge before the first peak slot and up to its starting charge after the last. It discharges in peak slots only, down to the outage backup. Off-peak and peak mean the day's lowest and highest tariffs.</p>
    <p>Both rules keep battery backup for later outage load beyond the generator rating, grid-charging to build it at the available charge rate. During an outage they use battery energy above that backup, then the generator with its minimum output; surplus backs off discharge, charges the battery, then backs off solar. The backup covers load above the generator rating. Otherwise the grid supplies the rest up to its import limit, then battery and generator cover the excess. To restore starting charge at day end, both use the latest non-outage slots for grid top-up, then replace the latest battery discharge with grid.</p>
    <p>All methods use the same assets, outages and hard constraints and are settled against the day's actual solar and load. The baselines run on actual values. Adjusted cost and CO2 figures price any battery end-of-day shortfall by refilling from the grid at the day's lowest tariff and that slot's grid emission factor, accounting for battery efficiency. A battery surplus earns no credit.</p>
    <h3>Emission factors and data sources</h3>
    <ul>{config.provenance.signals.split('; ').map((signal, index) => <li key={index}>{signal}</li>)}</ul>
    <dl className="provenance-list">{Object.entries(config.provenance).filter(([key]) => key !== 'signals').map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
    <p>Test year: {config.test_year}. Train years: {config.train_years.join(', ')}. Replay: {config.first_day} to {config.last_day}.</p>
    <p>Git commit: <code>{config.git.commit?.slice(0, 7) ?? 'unavailable'}</code></p>
    <p>Reproduce with <code>python -m energy_brain.evaluator</code></p>
  </details>
}

export default function BacktestView({ source, limitsRequest, onSeeLimits }: { source: BacktestData; limitsRequest: number; onSeeLimits: () => void }) {
  const result = useBacktestReport(source)
  const [weight, setWeight] = useState(0.5)
  const dailyResult = useBacktestDays(source, result?.ok ? weight : null, true)
  const content = useRef<HTMLDivElement>(null)
  const limitsHeading = useRef<HTMLHeadingElement>(null)
  useLimitsNavigation(limitsRequest, !!result?.ok && dailyResult !== null, content, limitsHeading)
  if (!result) return <p role="status">Loading backtest results…</p>
  if (!result.ok) return <p className="warning" role="alert">{result.error.kind !== 'aborted' && result.error.message}</p>
  const report = result.data
  const selected = report.summary.by_weight[String(weight)]
  return <div className="backtest-view" ref={content}>
    <div><h2>Backtest results</h2><p className="muted">Baseline vs Gridpilot AI · adjusted settlement · synthetic data, 2024 replay</p></div>
    <div className="segmented-control backtest-weights" role="group" aria-label="Backtest weight">
      {([[0.5, 'Balanced w = 0.5 (headline)'], [1, 'Cheapest w = 1'], [0, 'Cleanest w = 0']] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={weight === value} onClick={() => setWeight(value)}>{label}</button>)}
    </div>
    <p className="warning backtest-caution">Read with caution: synthetic load, simulated PV and synthetic outages; 2024 replay on one simulated site. <button type="button" className="limits-link" onClick={onSeeLimits}>See limits</button></p>
    <ReportDownload firstDay={report.config.first_day} lastDay={report.config.last_day} weight={weight} />
    {selected ? <>
      <div className="comparison-grid">{baselines.map((key) => <ComparisonCard key={key} name={report.headline.baseline_names[key]} values={selected.vs[key]} requested={report.summary.days.requested} onSeeLimits={onSeeLimits} />)}</div>
      <DaysCovered report={report} health={selected.plan_health} />
    </> : <p className="warning">This weight is not in the backtest summary.</p>}
    <ForecastQuality report={report} />
    <BacktestChart result={dailyResult} weight={weight} />
    <Measurement report={report} />
    <BacktestCaveats caveats={report.caveats} headingRef={limitsHeading} />
  </div>
}
