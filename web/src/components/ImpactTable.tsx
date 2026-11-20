import type { components } from '../api/generated'
import { formatBdt, formatKg, formatHours, formatLitres } from '../formatting'

type Totals = components['schemas']['Totals']
type Delta = components['schemas']['TotalsDelta']
const metrics = [
  { key: 'cost_bdt', label: 'Cost (BDT)', format: formatBdt },
  { key: 'co2_kg', label: 'CO2 (kg)', format: formatKg },
  { key: 'generator_hours', label: 'Generator hours (h)', format: formatHours },
  { key: 'diesel_litres', label: 'Diesel (litres)', format: formatLitres },
] as const

export default function ImpactTable({ before, after, delta, unsafe, override = false }: { before: Totals; after: Totals; delta: Delta; unsafe: boolean; override?: boolean }) {
  return <>
    {unsafe && <p className="warning">Cost changes are diagnostic only; this unsafe plan must not be executed.</p>}
    <div className="what-if-table-scroll" role="region" aria-label="Plan totals comparison" tabIndex={0}>
      <table className="comparison-table what-if-table">
        <caption>{override ? 'Baseline vs operator override' : 'Before vs after simulated cloud event'} · API planned totals; change = after minus before.</caption>
        <thead><tr><th scope="col">Metric</th><th scope="col">{override ? 'Baseline' : 'Before'}</th><th scope="col">{override ? 'Override' : 'After'}</th><th scope="col">Change{unsafe ? ' (diagnostic)' : ''}</th></tr></thead>
        <tbody>{metrics.map(({ key, label, format }) => <tr key={key}><th scope="row">{label}</th><td>{format(before[key])}</td><td>{format(after[key])}</td><td>{delta[key] < 0 ? '-' : '+'}{format(Math.abs(delta[key]))}</td></tr>)}</tbody>
      </table>
    </div>
  </>
}
