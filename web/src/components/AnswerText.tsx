import { Fragment, useId, useState } from 'react'
import type { ReactNode } from 'react'
import type { components } from '../api/generated'

type Source = components['schemas']['NumberSource']

function SourceMark({ source, children }: { source: Source; children: ReactNode }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const description = `${source.tool} -> ${source.field} (${source.kind})`
  return <span className="source-anchor">
    <mark tabIndex={0} role="button" title={description} aria-expanded={open}
      aria-describedby={open ? id : undefined}
      aria-label={`${source.text}: ${description}`}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={(event) => { if (event.currentTarget !== document.activeElement) setOpen(false) }}
      onClick={() => setOpen(true)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false) }
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setOpen((value) => !value) }
      }}>
      {children}
    </mark>
    {open && <span id={id} role="tooltip" className="source-popover">{description}</span>}
  </span>
}

const word = /[\p{L}\p{N}_]/u

// Use exact substrings only: no parsing, rounding or substitution of answer numbers.
function inline(text: string, sources: Source[]) {
  const pieces = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part) => ({
    bold: part.startsWith('**') && part.endsWith('**'),
    text: part.startsWith('**') && part.endsWith('**') ? part.slice(2, -2) : part,
  }))
  const plain = pieces.map((piece) => piece.text).join('')
  const matches: { start: number; end: number; source: Source }[] = []
  for (const source of [...sources].filter((source) => source.text).sort((a, b) => b.text.length - a.text.length)) {
    let start = plain.indexOf(source.text)
    while (start !== -1) {
      const end = start + source.text.length
      if (!(word.test(source.text[0]) && word.test(plain[start - 1] ?? ''))
        && !(word.test(source.text.at(-1) ?? '') && word.test(plain[end] ?? ''))
        && !matches.some((match) => start < match.end && end > match.start)) {
        matches.push({ start, end, source })
      }
      start = plain.indexOf(source.text, start + 1)
    }
  }
  matches.sort((a, b) => a.start - b.start)
  function formatted(from: number, to: number): ReactNode[] {
    let offset = 0
    return pieces.map((piece, index) => {
      const start = offset
      offset += piece.text.length
      const value = piece.text.slice(Math.max(0, from - start), Math.max(0, Math.min(offset, to) - start))
      return piece.bold ? <strong key={index}>{value}</strong> : <Fragment key={index}>{value}</Fragment>
    })
  }
  const result: ReactNode[] = []
  let cursor = 0
  for (const match of matches) {
    result.push(<Fragment key={`text-${cursor}`}>{formatted(cursor, match.start)}</Fragment>)
    result.push(<SourceMark key={`source-${match.start}`} source={match.source}>{formatted(match.start, match.end)}</SourceMark>)
    cursor = match.end
  }
  result.push(<Fragment key={`text-${cursor}`}>{formatted(cursor, plain.length)}</Fragment>)
  return result
}

export default function AnswerText({ text, provenance = [] }: { text: string; provenance?: Source[] }) {
  const blocks: { list: boolean; lines: string[] }[] = []
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) { blocks.push({ list: false, lines: [] }); continue }
    const list = line.startsWith('- ')
    const previous = blocks.at(-1)
    const value = list ? line.slice(2) : line
    if (previous && previous.list === list && previous.lines.length) previous.lines.push(value)
    else blocks.push({ list, lines: [value] })
  }
  return <div className="answer-text">{blocks.filter((block) => block.lines.length).map((block, index) => block.list
    ? <ul key={index}>{block.lines.map((line, item) => <li key={item}>{inline(line, provenance)}</li>)}</ul>
    : <p key={index}>{inline(block.lines.join('\n'), provenance)}</p>)}</div>
}
