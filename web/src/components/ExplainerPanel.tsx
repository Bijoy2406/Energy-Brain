import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { ApiResult } from '../api/client'
import type { components } from '../api/generated'
import AnswerText from './AnswerText'
import { Listen, Microphone } from './BrowserSpeech'

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
  language: 'English questions are supported.',
}

function BotAvatar() {
  return <span className="chat-avatar" aria-hidden="true">
    <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 4v4M13 4h6M6 15H4v6h2m20-6h2v6h-2" />
      <rect x="7" y="9" width="18" height="17" rx="5" />
      <path d="m17 12-4 7h5l-3 5M11 14v2m10-2v2" />
    </svg>
  </span>
}

function AnswerMessage({ answer, date, weight, onSpeechUse }: { answer: Answer; date: string; weight: number; onSpeechUse: (source: 'server' | 'browser') => void }) {
  return <div>
    {!!answer.banners?.length && <div className="warning" role="alert"><ul>{answer.banners.map((banner, index) => <li key={index}>{banner}</li>)}</ul></div>}
    <p className="muted answer-context">Day {answer.date}, w = {answer.weight.toFixed(2)}</p>
    {(answer.date !== date || answer.weight !== weight) && <p className="muted">This answer is about a different day or weight than the one shown.</p>}
    <div className="answer-badges">
      {!answer.verified && answer.refusal_reason === 'unverified' && <span className="status-badge">Unverified</span>}
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
    <Listen text={answer.answer} onUse={onSpeechUse} />
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
  </div>
}

export default function ExplainerPanel({ date, weight, demoAsk = false }: { date: string; weight: number; demoAsk?: boolean }) {
  const id = useId()
  const [micMessage, setMicMessage] = useState('')
  const [open, setOpen] = useState(false)
  const [everOpened, setEverOpened] = useState(false)
  const [intro, setIntro] = useState(true)
  const [unread, setUnread] = useState(false)
  const [showSuggestions, setShowSuggestions] = useState(true)
  const openRef = useRef(false)
  const launcher = useRef<HTMLButtonElement>(null)
  const dialog = useRef<HTMLElement>(null)
  const returnToLauncher = useRef(false)

  useEffect(() => {
    if (open || !returnToLauncher.current) return
    returnToLauncher.current = false
    launcher.current?.focus()
  }, [open])

  function close() {
    openRef.current = false
    setOpen(false)
    window.speechSynthesis?.cancel()
    // The launcher is hidden while the chat is open, so it is focused after the re-render (see the effect below).
    returnToLauncher.current = true
  }

  function toggle() {
    if (openRef.current) { close(); return }
    openRef.current = true
    setOpen(true)
    setEverOpened(true)
    setIntro(false)
    setUnread(false)
  }
  const [speechNote, setSpeechNote] = useState<false | 'server' | 'browser'>(false)
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

  const demoWasOpen = useRef(false)
  useEffect(() => {
    if (demoAsk) {
      openRef.current = true
      setOpen(true)
      setEverOpened(true)
      setIntro(false)
      setUnread(false)
      setQuestion(suggestions[0])
    } else if (demoWasOpen.current) {
      openRef.current = false
      setOpen(false)
    }
    demoWasOpen.current = demoAsk
  }, [demoAsk])

  useEffect(() => () => { active.current?.abort(); window.speechSynthesis?.cancel() }, [])
  useEffect(() => {
    const timer = setTimeout(() => setIntro(false), 4500)
    return () => clearTimeout(timer)
  }, [])
  useEffect(() => {
    if (!open) return
    if (textarea.current && !textarea.current.disabled) textarea.current.focus()
    else dialog.current?.focus()
    const escape = (event: KeyboardEvent) => {
      // Let source tooltips consume Escape before closing the whole dialog.
      if (event.key === 'Escape' && !event.defaultPrevented) close()
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [open])
  useEffect(() => {
    if (!open) return
    const phone = window.matchMedia('(max-width: 639px)')
    const previous = document.body.style.overflow
    const sync = () => { document.body.style.overflow = phone.matches ? 'hidden' : previous }
    sync()
    phone.addEventListener('change', sync)
    return () => {
      phone.removeEventListener('change', sync)
      document.body.style.overflow = previous
    }
  }, [open])
  useEffect(() => {
    if (!coolingDown) return
    const timer = setInterval(() => setRetryAfter((seconds) => Math.max(0, seconds - 1)), 1000)
    return () => clearInterval(timer)
  }, [coolingDown])
  useEffect(() => {
    if (open && pending === null && !coolingDown && returnFocus.current) {
      textarea.current?.focus()
      returnFocus.current = false
    }
  }, [open, pending, coolingDown])
  useEffect(() => {
    if (history.current) history.current.scrollTop = history.current.scrollHeight
  }, [messages, pending, open])

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
    setShowSuggestions(false)
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
    if (!openRef.current) setUnread(true)
    setMessages((previous) => previous.map((message) => message.id === messageId ? { ...message, result } : message))
  }

  return createPortal(<>
    <div className="chat-launcher-wrap">
      {!open && <span className={`chat-launcher-label${intro ? ' is-intro' : ''}`}>Ask about your plan</span>}
      <button type="button" ref={launcher} className={`chat-launcher${everOpened ? '' : ' is-unopened'}${open ? ' is-open' : ''}`}
        aria-label={open ? 'Close plan explainer' : 'Open plan explainer'} aria-expanded={open} aria-controls={`${id}-dialog`} onClick={toggle}>
        <span className="chat-launcher-bot"><BotAvatar /></span>
        <svg className="chat-launcher-x" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
        {unread && <span className="chat-unread"><span className="chat-sr-only">Unread answer</span></span>}
      </button>
    </div>
    <aside ref={dialog} id={`${id}-dialog`} className="panel chat-panel" role="dialog" aria-modal="false" aria-labelledby={`${id}-title`} hidden={!open} tabIndex={-1}>
    <div className="chat-content">
      <header className="chat-heading">
        <div className="chat-identity">
          <BotAvatar />
          <div className="chat-title">
            <h2 id={`${id}-title`}>Energy Brain Assistant</h2>
            <p className="chat-subtitle"><span className="chat-online" aria-hidden="true" /><span className="chat-sr-only">Online · </span>Plan explainer</p>
          </div>
          <button type="button" className="chat-close" aria-label="Close plan explainer" onClick={close}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg>
          </button>
        </div>
        <div className="chat-context-row"><span className="chat-context"><span aria-hidden="true" />Day {date || 'not selected'} · w = {weight.toFixed(2)}</span></div>
      </header>
      <div className="chat-scroll" ref={history} role="region" aria-label="Explainer conversation" tabIndex={0}>
        <div className="chat-messages" aria-live="polite" aria-relevant="additions text">
          <div className="chat-assistant chat-welcome">
            <BotAvatar />
            <div className="explainer-answer"><p>Hi! Ask me why the plan charges, discharges or runs the generator.</p></div>
          </div>
          {messages.map((message) => <article className="chat-message" key={message.id} aria-label="Explainer exchange">
            <p className="chat-question">{message.request.question}</p>
            {message.cancelled && <div className="chat-assistant"><BotAvatar /><p className="explainer-answer muted">Request cancelled.</p></div>}
            {message.result && (() => {
              const result = message.result
              if (!result.ok && result.error.kind === 'aborted') return null
              const error = !result.ok || !!result.data.error
              return <div className="chat-assistant"><BotAvatar /><div className={error ? 'warning unsafe explainer-error' : 'explainer-answer'} role={error ? 'alert' : undefined}>
                {result.ok ? <AnswerMessage answer={result.data} date={date} weight={weight} onSpeechUse={setSpeechNote} /> : <p>{result.error.kind !== 'aborted' && (result.error.kind === 'network'
                  ? 'The explainer is unreachable. Check the connection and try again.' : result.error.message)}</p>}
                {error && (result.ok || (!result.ok && result.error.kind !== 'validation')) && <button type="button" disabled={disabled}
                  onClick={() => { void send(message.request) }}>Retry</button>}
              </div></div>
            })()}
          </article>)}
          {pending !== null && <div className="chat-assistant"><BotAvatar /><div className="chat-loading explainer-answer" role="status">
            <span className="chat-typing" aria-hidden="true"><span /><span /><span /></span>
            <span className="chat-sr-only">Asking the explainer...</span>
          </div></div>}
        </div>
      </div>
      {messages.length > 0 && <button type="button" className="chat-suggestions-toggle" aria-expanded={showSuggestions}
        aria-controls={`${id}-suggestions`} onClick={() => setShowSuggestions((value) => !value)}>Suggestions</button>}
      <div id={`${id}-suggestions`} className="suggested-questions" aria-label="Suggested questions" hidden={!showSuggestions}>
        {suggestions.map((suggestion) => <button key={suggestion} type="button" disabled={coolingDown || !date}
          onClick={() => { void send({ date, w: weight, question: suggestion }) }}>{suggestion}</button>)}
      </div>
      <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); if (!disabled) void send({ date, w: weight, question }) }}>
        {coolingDown && <p className="warning" role="status">Try again in {retryAfter} seconds.</p>}
        <label htmlFor={`${id}-question`}>Ask about the plan</label>
        <div className="chat-input-row">
          <textarea placeholder="Ask why, when or how…" id={`${id}-question`} ref={textarea} rows={1} minLength={1} maxLength={500} value={question} disabled={disabled}
            aria-describedby={`${id}-counter ${id}-hint`} onChange={(event) => setQuestion(event.target.value.slice(0, 500))}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                if (!disabled) void send({ date, w: weight, question })
              }
            }} />
          <Microphone disabled={disabled || !open}
            onTranscript={(text) => { setQuestion(text); textarea.current?.focus() }} onUse={setSpeechNote} onMessage={setMicMessage} />
          <button className="chat-send" type="submit" aria-label="Send" disabled={disabled || !question.trim()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m21 3-7 18-4-7-7-4 18-7ZM10 14 21 3" /></svg>
          </button>
        </div>
        {micMessage && <p className="chat-mic-message" role="status">{micMessage}</p>}
        {speechNote === 'browser' && <p className="chat-response-hint muted" role="note">Browser speech services may send audio to the browser vendor’s servers.</p>}
        {speechNote === 'server' && <p className="chat-response-hint muted" role="note">Voice is sent to Groq to turn it into text and is not stored.</p>}
        <div className="chat-composer-actions">
          <p className="muted" id={`${id}-hint`}>Enter to send · Shift+Enter for a new line</p>
          <span className="muted" id={`${id}-counter`}>{question.length}/500</span>
        </div>
        <p className="chat-response-hint muted">Numbers are checked against the plan. Live answers can take a few seconds.</p>
      </form>
    </div>
  </aside>
  </>, document.body)
}
