import { useEffect, useId, useRef, useState } from 'react'
import { api } from '../api/client'
import type { ApiResult } from '../api/client'
import type { components } from '../api/generated'
import AnswerText from './AnswerText'

type Answer = components['schemas']['ExplainResponse']
type Question = components['schemas']['ExplainRequest']
type Message = { id: number; request: Question; result?: ApiResult<Answer>; cancelled?: boolean }

const suggestions = [
  'Why did you run the generator at 7 PM?',
  "Why didn't we charge the battery at 2 PM?",
  'When does the battery charge today, and why then?',
  'What will today cost, and how much CO2?',
  'Is this plan safe, and what did the Risk agent find?',
]
const refusals: Record<string, string> = {
  missing_data: 'The explainer does not have the data needed to answer this question.',
  off_topic: 'The explainer can only answer questions about the energy plan.',
  change_request: 'The explainer can explain the plan but cannot change or control it.',
  language: 'Only English questions are supported.',
}

function AnswerMessage({ answer, date, weight }: { answer: Answer; date: string; weight: number }) {
  return <>
    {!!answer.banners?.length && <div className="warning" role="alert"><ul>{answer.banners.map((banner, index) => <li key={index}>{banner}</li>)}</ul></div>}
    <p className="muted answer-context">Day {answer.date}, w = {answer.weight.toFixed(2)}</p>
    {(answer.date !== date || answer.weight !== weight) && <p className="muted">This answer is about a different day or weight than the one shown.</p>}
    <div className="answer-badges">
      {answer.verified && <span className="status-badge safe-badge">Verified</span>}
      {answer.cached && <span className="status-badge">Stored answer</span>}
      {answer.refusal_reason && answer.refusal_reason !== 'unverified' && <span className="status-badge">Not answered</span>}
    </div>
    {answer.cached && <p className="muted">recorded {answer.cached_recorded_at?.split('T')[0] ?? 'unknown'}, model {answer.cached_model ?? 'unknown'}</p>}
    {answer.live_error && <p className="muted">Live call failed: {answer.live_error}</p>}
    {answer.refusal_reason === 'unverified' && <div className="warning" role="alert">
      <h3>Some numbers could not be verified</h3>
      <ul>{answer.unverified_numbers?.map((number, index) => <li key={index}>{number}</li>)}</ul>
    </div>}
    {answer.refusal_reason && refusals[answer.refusal_reason] && <p className="muted">{refusals[answer.refusal_reason]}</p>}
    <AnswerText text={answer.answer} provenance={answer.provenance} />
    <details className="answer-sources">
      <summary>Sources</summary>
      <h3>Tool calls</h3>
      {answer.tool_calls?.length ? <ul>{answer.tool_calls.map((call, index) => <li key={index}>
        <code>{call.name}</code><pre>{JSON.stringify(call.arguments ?? {}, null, 2)}</pre>
      </li>)}</ul> : <p className="muted">No tool calls returned.</p>}
      <table>
        <caption>Answer provenance</caption>
        <thead><tr><th scope="col">Text</th><th scope="col">Tool</th><th scope="col">Field</th></tr></thead>
        <tbody>{answer.provenance?.map((source, index) => <tr key={index}><td>{source.text}</td><td>{source.tool}</td><td>{source.field}</td></tr>)}</tbody>
      </table>
    </details>
  </>
}

export default function ExplainerPanel({ date, weight }: { date: string; weight: number }) {
  const id = useId()
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [pending, setPending] = useState<number | null>(null)
  const [retryAfter, setRetryAfter] = useState(0)
  const active = useRef<AbortController | null>(null)
  const sequence = useRef(0)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const history = useRef<HTMLDivElement>(null)
  const returnFocus = useRef(false)
  const coolingDown = retryAfter > 0
  const disabled = pending !== null || coolingDown || !date

  useEffect(() => () => { active.current?.abort() }, [])
  useEffect(() => {
    if (!coolingDown) return
    const timer = setInterval(() => setRetryAfter((seconds) => Math.max(0, seconds - 1)), 1000)
    return () => clearInterval(timer)
  }, [coolingDown])
  useEffect(() => {
    if (pending === null && !coolingDown && returnFocus.current) {
      textarea.current?.focus()
      returnFocus.current = false
    }
  }, [pending, coolingDown])
  useEffect(() => {
    if (history.current) history.current.scrollTop = history.current.scrollHeight
  }, [messages, pending])

  async function send(request: Question) {
    const trimmed = request.question.trim()
    if (!trimmed || trimmed.length > 500 || !request.date || coolingDown) return
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    const messageId = ++sequence.current
    const payload = { ...request, question: trimmed }
    setMessages((previous) => [
      ...previous.map((message) => message.result || message.cancelled ? message : { ...message, cancelled: true }),
      { id: messageId, request: payload },
    ])
    setPending(messageId)
    setQuestion('')
    returnFocus.current = true
    const result = await api.explain(payload, controller.signal)
    if (controller.signal.aborted || active.current !== controller) return
    setPending(null)
    if (!result.ok && result.error.kind === 'aborted') {
      setMessages((previous) => previous.map((message) => message.id === messageId ? { ...message, cancelled: true } : message))
      return
    }
    if (!result.ok && result.error.kind === 'api' && result.error.status === 429 && result.error.code === 'rate_limited') {
      setRetryAfter(Math.max(0, Math.ceil(result.error.retry_after_s ?? 0)))
    }
    setMessages((previous) => previous.map((message) => message.id === messageId ? { ...message, result } : message))
  }

  return <aside className="panel chat-panel" aria-labelledby={`${id}-title`}>
    <div className="chat-content">
      <div className="chat-heading"><p className="eyebrow">PLAN EXPLAINER</p><h2 id={`${id}-title`}>Ask about your plan</h2>
        <p className="muted">Answers use the planner’s results. A live answer can take about 6 seconds.</p>
      </div>
      <div className="chat-scroll" ref={history} role="region" aria-label="Explainer conversation" tabIndex={0}>
        <div className="suggested-questions" aria-label="Suggested questions">
          {suggestions.map((suggestion) => <button key={suggestion} type="button" disabled={coolingDown || !date}
            onClick={() => { void send({ date, w: weight, question: suggestion }) }}>{suggestion}</button>)}
        </div>
        <div className="chat-messages" aria-live="polite" aria-relevant="additions text">
          {messages.map((message) => <article className="chat-message" key={message.id} aria-label="Explainer exchange">
            <p className="chat-question">{message.request.question}</p>
            {message.cancelled && <p className="muted">Request cancelled.</p>}
            {message.result && (() => {
              const result = message.result
              if (!result.ok && result.error.kind === 'aborted') return null
              const error = !result.ok || !!result.data.error
              return <div className={error ? 'warning unsafe explainer-error' : 'explainer-answer'} role={error ? 'alert' : undefined}>
                {result.ok ? <AnswerMessage answer={result.data} date={date} weight={weight} /> : <p>{result.error.kind !== 'aborted' && (result.error.kind === 'network'
                  ? 'The explainer is unreachable. Check the connection and try again.' : result.error.message)}</p>}
                {error && (result.ok || (!result.ok && result.error.kind !== 'validation')) && <button type="button" disabled={disabled}
                  onClick={() => { void send(message.request) }}>Retry</button>}
              </div>
            })()}
          </article>)}
          {pending !== null && <p className="chat-loading" role="status"><span className="explainer-spinner" aria-hidden="true" />Asking the explainer...</p>}
        </div>
      </div>
      <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); if (!disabled) void send({ date, w: weight, question }) }}>
        {coolingDown && <p className="warning" role="status">Try again in {retryAfter} seconds.</p>}
        <label htmlFor={`${id}-question`}>Ask about the plan</label>
        <textarea id={`${id}-question`} ref={textarea} rows={2} minLength={1} maxLength={500} value={question} disabled={disabled}
          aria-describedby={`${id}-counter ${id}-hint`} onChange={(event) => setQuestion(event.target.value.slice(0, 500))}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              if (!disabled) void send({ date, w: weight, question })
            }
          }} />
        <div className="chat-composer-actions"><span className="muted" id={`${id}-counter`}>{question.length}/500</span>
          <button type="submit" disabled={disabled || !question.trim()}>Send</button></div>
        <p className="muted" id={`${id}-hint`}>Enter to send · Shift+Enter for a new line</p>
      </form>
    </div>
  </aside>
}
