import { useId } from 'react'

export default function BacktestCaveats({ caveats }: { caveats: string[] }) {
  const id = useId()
  return <aside className="warning backtest-caveats" aria-labelledby={id}>
    <h3 id={id}>Limits of this replay</h3>
    <ul>{caveats.map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>
  </aside>
}
