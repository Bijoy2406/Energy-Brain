import { useId, useState } from 'react'
import type { Ref } from 'react'
import { groupCaveats } from '../backtest/caveats'

export default function BacktestCaveats({ caveats, headingRef }: { caveats: string[]; headingRef?: Ref<HTMLHeadingElement> }) {
  const id = useId()
  const groups = groupCaveats(caveats)
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set())
  const allOpen = groups.length > 0 && groups.every(({ title }) => openGroups.has(title))
  return <section id="limits" className="panel backtest-caveats" aria-labelledby={id}>
    <div className="panel-heading">
      <div>
        <h2 id={id} tabIndex={-1} ref={headingRef}>Limits and methodology</h2>
        <p className="muted limits-count">{caveats.length} {caveats.length === 1 ? 'limit' : 'limits'} in {groups.length} {groups.length === 1 ? 'group' : 'groups'}</p>
      </div>
      <button type="button" className="limits-link" aria-controls={`${id}-groups`} onClick={() => setOpenGroups(new Set(allOpen ? [] : groups.map(({ title }) => title)))}>
        {allOpen ? 'Collapse all' : 'Expand all'}
      </button>
    </div>
    <div id={`${id}-groups`}>
      {groups.map(({ title, items }) => <details key={title} className="limits-group" open={openGroups.has(title)} onToggle={(event) => {
        const open = event.currentTarget.open
        setOpenGroups((previous) => {
          if (previous.has(title) === open) return previous
          const next = new Set(previous)
          if (open) next.add(title)
          else next.delete(title)
          return next
        })
      }}>
        <summary onKeyDown={(event) => {
          if (event.key !== 'Enter' && event.key !== ' ') return
          event.preventDefault()
          if (!event.repeat) event.currentTarget.click()
        }}>
          <svg className="limits-chevron" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
          <span>{title}</span><span className="muted limits-group-count">{items.length} {items.length === 1 ? 'limit' : 'limits'}</span>
        </summary>
        <ul>{items.map((caveat, index) => <li key={index}>{caveat}</li>)}</ul>
      </details>)}
    </div>
  </section>
}
