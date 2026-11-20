import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import type { components } from './api/generated'
import { days, planFixture } from './test/fixtures'
import { backtestDaysFixture } from './test/backtestFixture'
import { resetTranscriptionCheck } from './components/voiceSupport'

type Answer = components['schemas']['ExplainResponse']
const questions = [
  'Why did you run the generator at 7 PM?',
  "Why didn't we charge the battery at 2 PM?",
  'When does the battery charge today, and why then?',
  'What will today cost, and how much CO2?',
  'Is this plan safe, and what did the Risk agent find?',
]
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
function answer(overrides: Partial<Answer> = {}): Answer {
  return {
    question: questions[0], language: 'en', answer: 'Generator supplies **269.8 kW**.',
    verified: true, refusal_reason: null, error: null, banners: [],
    tool_calls: [{ name: 'get_slot', arguments: { time: '19:00' }, result: { generator_kw: 269.8 } }],
    provenance: [{ text: '269.8 kW', tool: 'get_slot', field: 'generator_kw', kind: 'number', call: 0 }],
    unverified_numbers: [], rounds: 2, model: 'demo-model', prompt_version: 'v3',
    date: '2024-07-02', weight: 0.5, weight_requested: 0.5, cached: false,
    cached_recorded_at: null, cached_model: null, live_error: null, ...overrides,
  }
}
// Server voice input is off unless a test turns it on: /health then fails like any other unmocked request.
let voice: { available: boolean; transcribe: (init: RequestInit) => Promise<Response> } | null = null
function mockApi(explain: (init: RequestInit) => Promise<Response> = async () => json(answer()), plan = planFixture()) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
    if (voice && url.endsWith('/health')) return json({ transcription_available: voice.available })
    if (voice && url.includes('/transcribe')) return voice.transcribe(init)
    if (url.includes('/backtest/days')) return json(backtestDaysFixture())
    if (url.endsWith('/days')) return json(days)
    if (url.endsWith('/plan')) return json(plan)
    if (url.endsWith('/explain')) return explain(init)
    throw new Error('Unexpected request')
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
async function start() {
  render(<App />)
  await screen.findByText('152,703')
  fireEvent.click(screen.getByRole('button', { name: 'Open plan explainer' }))
  return screen.getByRole('textbox', { name: 'Ask about the plan' })
}
function ask() { fireEvent.click(screen.getByRole('button', { name: questions[0] })) }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); voice = null; resetTranscriptionCheck() })

it('sends exact suggestions with selected date and solved weight; shows verified provenance and sources', async () => {
  const fetchMock = mockApi(undefined, planFixture({ weight: 0.31, weight_requested: 0.305 }))
  const input = await start()
  for (const question of questions) expect(screen.getByRole('button', { name: question })).toBeInTheDocument()
  ask()
  expect(await screen.findByText('Verified')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/explain', expect.objectContaining({
    body: JSON.stringify({ date: '2024-07-02', w: 0.31, question: questions[0] }), signal: expect.any(AbortSignal),
  }))
  expect(screen.getByText('Day 2024-07-02, w = 0.50')).toBeInTheDocument()
  const mark = screen.getByRole('button', { name: '269.8 kW: get_slot -> generator_kw (number)' })
  expect(mark).toHaveAttribute('tabindex', '0')
  expect(mark).toHaveAttribute('title', 'get_slot -> generator_kw (number)')
  await waitFor(() => expect(input).toHaveFocus())
  expect(mark.tagName).toBe('MARK')
  act(() => mark.focus())
  expect(screen.getByRole('tooltip')).toHaveTextContent('get_slot -> generator_kw (number)')
  fireEvent.keyDown(mark, { key: 'Escape' })
  fireEvent.click(mark)
  expect(screen.getByRole('tooltip')).toHaveTextContent('get_slot -> generator_kw (number)')
  fireEvent.keyDown(mark, { key: 'Escape' })
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  // The bulky "Sources" dropdown was removed; each number's source stays available in its tooltip.
  expect(screen.queryByText('Sources')).not.toBeInTheDocument()
})

it('uses requested weight before a plan is available and the current selected date', async () => {
  const fetchMock = mockApi()
  render(<App />)
  const picker = await screen.findByRole('combobox')
  fireEvent.click(screen.getByRole('button', { name: 'Open plan explainer' }))
  fireEvent.change(picker, { target: { value: '2024-07-01' } })
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.7' } })
  ask()
  await screen.findByText('Verified')
  expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/explain', expect.objectContaining({
    body: JSON.stringify({ date: '2024-07-01', w: 0.7, question: questions[0] }),
  }))
})

it('shows banners first, stored metadata and live failure', async () => {
  mockApi(async () => json(answer({ cached: true, cached_recorded_at: '2026-11-18T16:32:20+06:00',
    cached_model: 'stored-model', live_error: 'timeout', banners: ['NOT SAFE TO EXECUTE', 'Forecast fallback used.'] })))
  await start()
  ask()
  expect(await screen.findByText('Stored answer')).toBeInTheDocument()
  expect(screen.getByText(/recorded 2026-11-18.*model stored-model/)).toBeInTheDocument()
  expect(screen.getByText('Live call failed: timeout')).toBeInTheDocument()
  const warning = screen.getByText('NOT SAFE TO EXECUTE')
  expect(warning.closest('.warning')).toHaveTextContent('Forecast fallback used.')
  expect(warning.compareDocumentPosition(screen.getByRole('button', { name: '269.8 kW: get_slot -> generator_kw (number)' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('warns about unverified numbers without formatting them', async () => {
  mockApi(async () => json(answer({ verified: false, refusal_reason: 'unverified', unverified_numbers: ['1\u202f234.50 kWh'] })))
  await start()
  ask()
  expect(await screen.findByText('Some numbers could not be verified')).toBeInTheDocument()
  expect(screen.getByText('1\u202f234.50 kWh', { normalizer: (text) => text })).toBeInTheDocument()
  expect(screen.queryByText('Verified')).not.toBeInTheDocument()
})

it.each([
  ['missing_data', 'The explainer does not have the data needed to answer this question.'],
  ['off_topic', 'The explainer can only answer questions about the energy plan.'],
  ['change_request', 'The explainer can explain the plan but cannot change or control it.'],
  ['language', 'English questions are supported.'],
])('shows %s refusal and its answer', async (refusal_reason, explanation) => {
  mockApi(async () => json(answer({ verified: false, refusal_reason, answer: 'I cannot answer this question.' })))
  await start()
  ask()
  expect(await screen.findByText('Not answered')).toBeInTheDocument()
  expect(screen.getByText(explanation)).toBeInTheDocument()
  expect(screen.getByText('I cannot answer this question.')).toBeInTheDocument()
})

it.each(['no_key', 'bad_config', 'network', 'timeout', 'rate_limit', 'blocked_model', 'bad_tool_call', 'max_rounds', 'api_error'])(
  'shows %s as an error and retries the same question', async (error) => {
    const fetchMock = mockApi(async () => json(answer({ verified: false, error, answer: 'The explainer is unavailable.' })))
    await start()
    ask()
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.getByText('The explainer is unavailable.').closest('[role="alert"]')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/explain'))).toHaveLength(2))
    const calls = fetchMock.mock.calls.filter(([url]) => url.endsWith('/explain'))
    expect(calls[1][1].body).toBe(calls[0][1].body)
  },
)

it('shows HTTP validation messages without JSON', async () => {
  mockApi(async () => json({ detail: [{ loc: ['body', 'question'], msg: 'Question is invalid', type: 'value_error' }] }, 422))
  await start()
  ask()
  expect(await screen.findByText('body.question: Question is invalid')).toBeInTheDocument()
})

it('disables sending during the 429 countdown and re-enables at zero', async () => {
  mockApi(async () => json({ detail: { code: 'rate_limited', message: 'Please wait before asking again.', retry_after_s: 3 } }, 429))
  const input = await start()
  vi.useFakeTimers()
  await act(async () => ask())
  fireEvent.click(screen.getByRole('button', { name: 'Suggestions' }))
  expect(screen.getByText('Try again in 3 seconds.')).toBeInTheDocument()
  expect(input).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  expect(screen.getByRole('button', { name: questions[0] })).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(screen.getByText('Try again in 2 seconds.')).toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(input).toBeEnabled()
  expect(screen.getByRole('button', { name: questions[0] })).toBeEnabled()
  expect(screen.queryByText(/Try again in/)).not.toBeInTheDocument()
  fireEvent.change(input, { target: { value: 'Another question' } })
  expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
})

it('shows a network error with Retry and recovers', async () => {
  let fail = true
  mockApi(async () => { if (fail) throw new TypeError('Failed to fetch'); return json(answer()) })
  const input = await start()
  ask()
  expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent(/connection|unreachable/i)
  fail = false
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Verified')).toBeInTheDocument()
  expect(input).toHaveFocus()
})

it('renders safe paragraphs, bold and bullets, preserves narrow spaces, and matches longest provenance without breaking words', async () => {
  const provenance = [
    { text: '269.8', tool: 'get_slot', field: 'short', kind: 'number', call: 0 },
    { text: '269.8 kW', tool: 'get_slot', field: 'generator_kw', kind: 'number', call: 0 },
  ]
  mockApi(async () => json(answer({ answer: '**Power** is 269.8 kW. Again 269.8 kW; x269.8 kWz.\n\n- First\n- Second\n\n<script>alert(1)</script>\n\n1\u202f234.50 BDT', provenance })))
  await start()
  ask()
  expect(await screen.findByText('Power', { selector: 'strong' })).toBeInTheDocument()
  expect(screen.getByText('First', { selector: 'li' })).toBeInTheDocument()
  expect(screen.getByText('Second', { selector: 'li' })).toBeInTheDocument()
  expect(screen.getByText('<script>alert(1)</script>', { selector: 'p' })).toBeInTheDocument()
  expect(document.querySelector('script')).not.toBeInTheDocument()
  expect(screen.getByText('1\u202f234.50 BDT', { selector: 'p', normalizer: (text) => text }).textContent).toBe('1\u202f234.50 BDT')
  expect(document.querySelectorAll('mark')).toHaveLength(2)
  for (const mark of document.querySelectorAll('mark')) expect(mark).toHaveTextContent('269.8 kW')
})

it('aborts on a second suggestion, ignores late results, and aborts on unmount', async () => {
  const requests: { init: RequestInit; resolve: (response: Response) => void }[] = []
  mockApi((init) => new Promise((resolve) => requests.push({ init, resolve })))
  const view = render(<App />)
  await screen.findByText('152,703')
  fireEvent.click(screen.getByRole('button', { name: 'Open plan explainer' }))
  ask()
  expect(screen.getByText('Asking the explainer...')).toBeInTheDocument()
  expect(screen.getByRole('textbox')).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Suggestions' }))
  fireEvent.click(screen.getByRole('button', { name: questions[1] }))
  expect(requests[0].init.signal?.aborted).toBe(true)
  await act(async () => requests[1].resolve(json(answer({ answer: 'Current answer' }))))
  expect(screen.getByText('Current answer')).toBeInTheDocument()
  await act(async () => requests[0].resolve(json(answer({ answer: 'Obsolete answer' }))))
  expect(screen.queryByText('Obsolete answer')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Suggestions' }))
  ask()
  view.unmount()
  expect(requests[2].init.signal?.aborted).toBe(true)
})

it('keeps history and labels answers about a different selected day', async () => {
  mockApi()
  await start()
  ask()
  await screen.findByText('Verified')
  fireEvent.change(screen.getByRole('combobox', { name: 'Planning day' }), { target: { value: '2024-07-01' } })
  expect(screen.getByText('This answer is about a different day or weight than the one shown.')).toBeInTheDocument()
  expect(screen.getByText('Day 2024-07-02, w = 0.50')).toBeInTheDocument()
})

it('rejects empty input, limits to 500 characters, trims questions and distinguishes Enter from Shift+Enter', async () => {
  const user = userEvent.setup()
  const fetchMock = mockApi()
  const input = await start()
  expect(input).toHaveAttribute('maxlength', '500')
  expect(screen.getByText('0/500')).toBeInTheDocument()
  fireEvent.change(input, { target: { value: '   ' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/explain'))).toHaveLength(0)
  await user.clear(input)
  await user.type(input, 'a'.repeat(501))
  expect(input).toHaveValue('a'.repeat(500))
  expect(screen.getByText('500/500')).toBeInTheDocument()
  await user.clear(input)
  await user.type(input, '  Why?{Shift>}{Enter}{/Shift}Now  ')
  expect(input).toHaveValue('  Why?\nNow  ')
  await user.keyboard('{Enter}')
  expect(await screen.findByText('Verified')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/explain', expect.objectContaining({
    body: JSON.stringify({ date: '2024-07-02', w: 0.5, question: 'Why?\nNow' }),
  }))
})

it('opens a labelled dialog, closes from the header and launcher, and returns focus on Escape', async () => {
  mockApi()
  render(<App />)
  await screen.findByText('152,703')
  const launcher = screen.getByRole('button', { name: 'Open plan explainer' })
  expect(launcher).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  fireEvent.click(launcher)
  const dialog = screen.getByRole('dialog', { name: 'Gridpilot AI Assistant' })
  expect(dialog).toHaveAttribute('aria-modal', 'false')
  expect(launcher).toHaveAttribute('aria-controls', dialog.id)
  expect(launcher).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByRole('textbox')).toHaveFocus()
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(launcher).toHaveFocus()
  fireEvent.click(launcher)
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close plan explainer' }))
  expect(launcher).toHaveFocus()
  fireEvent.click(launcher)
  fireEvent.click(launcher)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('keeps pending requests and drafts across closing, marks unread answers, and does not steal focus', async () => {
  let resolveAnswer!: (response: Response) => void
  let signal!: AbortSignal
  mockApi((init) => { signal = init.signal as AbortSignal; return new Promise((resolve) => { resolveAnswer = resolve }) })
  const input = await start()
  ask()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close plan explainer' }))
  const launcher = screen.getByRole('button', { name: 'Open plan explainer' })
  expect(signal.aborted).toBe(false)
  await act(async () => resolveAnswer(json(answer())))
  expect(launcher).toHaveFocus()
  expect(within(launcher).getByText('Unread answer')).toBeInTheDocument()
  fireEvent.click(launcher)
  expect(screen.getByText('Verified')).toBeVisible()
  expect(screen.getByRole('textbox')).toBe(input)
  expect(within(launcher).queryByText('Unread answer')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: questions[0] })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Suggestions' }))
  expect(screen.getByRole('button', { name: questions[0] })).toBeVisible()
  fireEvent.change(input, { target: { value: 'Unsent question' } })
  fireEvent.click(launcher)
  fireEvent.click(launcher)
  expect(input).toHaveValue('Unsent question')
  expect(screen.getByText('Verified')).toBeVisible()
})

it('stays available with the same conversation on every tab', async () => {
  mockApi()
  await start()
  ask()
  await screen.findByText('Verified')
  for (const name of ['Trade-off', 'Backtest results', "Today's plan"]) {
    fireEvent.click(screen.getByRole('tab', { name }))
    expect(screen.getByRole('dialog')).toBeVisible()
    expect(screen.getByText('Verified')).toBeVisible()
    expect(screen.getByText('Day 2024-07-02 · w = 0.50')).toBeVisible()
  }
})

it('disables the composer when no planning date is available', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...days, days: [] })))
  render(<App />)
  await screen.findByText('No planning days available.')
  fireEvent.click(screen.getByRole('button', { name: 'Open plan explainer' }))
  expect(screen.getByRole('textbox')).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
})

it('locks body scrolling only while the phone sheet is open and restores existing styles', async () => {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    matches: query === '(max-width: 639px)', addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })))
  mockApi()
  document.body.style.overflow = 'auto'
  await start()
  expect(document.body.style.overflow).toBe('hidden')
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close plan explainer' }))
  expect(document.body.style.overflow).toBe('auto')
  document.body.style.overflow = ''
})

it('uses the new selected weight while an older solved plan is still displayed', async () => {
  const fetchMock = mockApi()
  await start()
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.7' } })
  ask()
  await screen.findByText('Verified')
  expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/explain', expect.objectContaining({
    body: JSON.stringify({ date: '2024-07-02', w: 0.7, question: questions[0] }),
  }))
})

it('hides voice controls when browser speech is unavailable', async () => {
  mockApi()
  await start()
  expect(screen.queryByRole('button', { name: /microphone/i })).not.toBeInTheDocument()
  ask()
  await screen.findByText('Verified')
  expect(screen.queryByRole('button', { name: /Listen/ })).not.toBeInTheDocument()
})

it('fills speech input without sending and reads answers with an English voice', async () => {
  const recognition: {
    lang: string; onresult: ((event: { results: { 0: { transcript: string } }[] }) => void) | null
    onend: (() => void) | null; onerror: (() => void) | null
    start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>; abort: ReturnType<typeof vi.fn>
  } = { lang: '', onresult: null, onend: null, onerror: null, start: vi.fn(), stop: vi.fn(), abort: vi.fn() }
  class Recognition { constructor() { return recognition } }
  vi.stubGlobal('webkitSpeechRecognition', Recognition)
  const speak = vi.fn()
  const cancel = vi.fn()
  const voice = { lang: 'en-US', name: 'English' }
  vi.stubGlobal('speechSynthesis', { getVoices: () => [voice], speak, cancel, addEventListener: vi.fn(), removeEventListener: vi.fn() })
  vi.stubGlobal('SpeechSynthesisUtterance', class { text: string; lang = ''; voice = null; constructor(text: string) { this.text = text } })
  const fetchMock = mockApi(async () => json(answer({ language: 'en', answer: 'Generator supplies 269.8 kW.' })))
  await start()
  fireEvent.click(screen.getByRole('button', { name: 'Start microphone' }))
  expect(recognition.lang).toBe('en-US')
  expect(recognition.start).toHaveBeenCalledOnce()
  expect(screen.getByText(/Browser speech services may send audio/)).toBeVisible()
  act(() => recognition.onresult?.({ results: [[{ transcript: 'Why is the generator on?' }]] }))
  expect(screen.getByRole('textbox')).toHaveValue('Why is the generator on?')
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/explain'))).toHaveLength(0)
  act(() => recognition.onend?.())
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await screen.findByText('Verified')
  fireEvent.click(screen.getByRole('button', { name: 'Listen' }))
  expect(speak).toHaveBeenCalledWith(expect.objectContaining({ lang: 'en-US', voice, text: 'Generator supplies 269.8 kW.' }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close plan explainer' }))
  expect(cancel).toHaveBeenCalled()
})

function stubRecognition() {
  const recognition: {
    lang: string; onstart: (() => void) | null; onresult: ((event: { results: { 0: { transcript: string } }[] }) => void) | null
    onend: (() => void) | null; onerror: ((event: { error?: string }) => void) | null
    start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>; abort: ReturnType<typeof vi.fn>
  } = { lang: '', onstart: null, onresult: null, onend: null, onerror: null, start: vi.fn(), stop: vi.fn(), abort: vi.fn() }
  class Recognition { constructor() { return recognition } }
  vi.stubGlobal('webkitSpeechRecognition', Recognition)
  return recognition
}

it.each([
  ['not-allowed', /Microphone access is blocked/],
  ['network', /Could not reach the browser.s speech service/],
  ['audio-capture', /No microphone found/],
  ['something-else', /Voice input did not work/],
])('shows a visible message when speech recognition fails with %s', async (code, message) => {
  const recognition = stubRecognition()
  mockApi()
  await start()
  fireEvent.click(screen.getByRole('button', { name: 'Start microphone' }))
  act(() => recognition.onstart?.())
  expect(screen.getByText('Listening… speak now.')).toBeVisible()
  act(() => { recognition.onerror?.({ error: code }); recognition.onend?.() })
  expect(screen.getByText(message)).toBeVisible()
  expect(screen.queryByText('Listening… speak now.')).not.toBeInTheDocument()
  // The button recovers so the person can try again.
  expect(screen.getByRole('button', { name: 'Start microphone' })).toBeEnabled()
})

it('says so when the browser heard nothing, and clears the message after a good result', async () => {
  const recognition = stubRecognition()
  mockApi()
  await start()
  fireEvent.click(screen.getByRole('button', { name: 'Start microphone' }))
  act(() => recognition.onend?.())
  expect(screen.getByText(/No speech heard/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Start microphone' }))
  expect(screen.queryByText(/No speech heard/)).not.toBeInTheDocument()
  act(() => { recognition.onresult?.({ results: [[{ transcript: 'Why the generator?' }]] }); recognition.onend?.() })
  expect(screen.getByRole('textbox')).toHaveValue('Why the generator?')
  expect(screen.queryByText(/No speech heard/)).not.toBeInTheDocument()
})

it('hides listening without a matching voice and updates when voices load', async () => {
  let voices: { lang: string; name: string }[] = [{ lang: 'fr-FR', name: 'French' }]
  let changed: (() => void) | undefined
  vi.stubGlobal('speechSynthesis', { getVoices: () => voices, speak: vi.fn(), cancel: vi.fn(),
    addEventListener: (_: string, callback: () => void) => { changed = callback }, removeEventListener: vi.fn() })
  vi.stubGlobal('SpeechSynthesisUtterance', class {})
  mockApi(async () => json(answer({ language: 'en' })))
  await start()
  ask()
  await screen.findByText('Verified')
  expect(screen.queryByRole('button', { name: 'Listen' })).not.toBeInTheDocument()
  voices = [{ lang: 'en-US', name: 'English' }]
  act(() => changed?.())
  expect(screen.getByRole('button', { name: 'Listen' })).toBeVisible()
})

// ---------------------------------------------------------------------------- server voice input (recorder)

function stubRecorder(level: number) {
  const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }]
  const getUserMedia = vi.fn(async () => ({ getTracks: () => tracks }))
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true })
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:recording'), configurable: true, writable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true, writable: true })
  class Recorder {
    state = 'inactive'
    mimeType = 'audio/webm;codecs=opus'
    ondataavailable: ((event: { data: Blob }) => void) | null = null
    onstop: (() => void) | null = null
    start() { this.state = 'recording' }
    pause() { this.state = 'paused' }
    resume() { this.state = 'recording' }
    stop() {
      this.state = 'inactive'
      this.ondataavailable?.({ data: new Blob(['audio'], { type: this.mimeType }) })
      this.onstop?.()
    }
  }
  vi.stubGlobal('MediaRecorder', Recorder)
  class Context {
    createAnalyser() { return { fftSize: 0, smoothingTimeConstant: 0, getByteTimeDomainData: (data: Uint8Array) => data.fill(128 + Math.round(level * 128)) } }
    createMediaStreamSource() { return { connect() {} } }
    close() { return Promise.resolve() }
  }
  vi.stubGlobal('AudioContext', Context)
  // jsdom's animation-frame timestamps use another clock than performance.now(); real browsers keep them in sync.
  vi.stubGlobal('requestAnimationFrame', (callback: (time: number) => void) => window.setTimeout(() => callback(performance.now()), 16))
  vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))
  return { tracks, getUserMedia }
}
// The recorder measures real time and level samples, so the tests really record for a moment.
async function speakFor(ms = 900) { await act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)) }) }
async function record(ms = 900) {
  fireEvent.click(await screen.findByRole('button', { name: 'Start recording' }))
  await screen.findByRole('button', { name: 'Stop and save recording' })
  await speakFor(ms)
  fireEvent.click(screen.getByRole('button', { name: 'Stop and save recording' }))
}
const transcribed = (text: string, extra: Record<string, unknown> = {}) =>
  json({ text, language: 'en', duration_s: 2.4, model: 'whisper-test', no_speech: false, ...extra })

it('shows the voice button as a microphone icon, not text', async () => {
  stubRecorder(0.2)
  voice = { available: true, transcribe: async () => transcribed('x') }
  mockApi()
  await start()
  const mic = await screen.findByRole('button', { name: 'Start recording' })
  expect(mic.textContent).toBe('')
  expect(mic.querySelector('svg')).not.toBeNull()
})

it('records, transcribes on the server and fills the input without sending it', async () => {
  const { tracks } = stubRecorder(0.2)
  const bodies: RequestInit[] = []
  voice = { available: true, transcribe: async (init) => { bodies.push(init); return transcribed('Why is the generator on?') } }
  const fetchMock = mockApi()
  await start()
  await record()
  expect(screen.getByText(/Voice is sent to Groq to turn it into text and is not stored\./)).toBeVisible()
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Ask about the plan' })).toHaveValue('Why is the generator on?'))
  expect(bodies).toHaveLength(1)
  expect((bodies[0].body as Blob).type).toBe('audio/webm;codecs=opus')
  expect((bodies[0].headers as Record<string, string>)['Content-Type']).toBe('audio/webm;codecs=opus')
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/transcribe?language=en'))).toBe(true)
  expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/explain'))).toHaveLength(0) // never auto-sent
  tracks.forEach((track) => expect(track.stop).toHaveBeenCalled()) // the browser's recording light goes off
  expect(screen.getByRole('textbox', { name: 'Ask about the plan' })).toHaveFocus()
  expect(screen.queryByText(/Turning your voice into text/)).not.toBeInTheDocument()
  expect(await screen.findByRole('button', { name: 'Start recording' })).toBeEnabled() // ready for the next one
})

it('does not upload a recording that was too short or silent', async () => {
  stubRecorder(0)
  const transcribe = vi.fn(async () => transcribed('should not happen'))
  voice = { available: true, transcribe }
  mockApi()
  await start()
  await record(0) // stopped at once: too short
  expect(await screen.findByText('Too short. Hold on a little longer and speak.')).toBeVisible()
  await record(900) // long enough but nothing but silence
  expect(await screen.findByText('No speech heard. Check your microphone and try again.')).toBeVisible()
  expect(transcribe).not.toHaveBeenCalled()
  expect(screen.getByRole('textbox', { name: 'Ask about the plan' })).toHaveValue('')
})

it('says so when the server found no speech', async () => {
  stubRecorder(0.2)
  const transcribe = vi.fn(async () => transcribed('', { no_speech: true }))
  voice = { available: true, transcribe }
  mockApi()
  await start()
  await record()
  expect(await screen.findByText('No speech heard. Check your microphone and try again.')).toBeVisible()
  expect(transcribe).toHaveBeenCalledOnce() // loud enough to upload; the server then found no speech
  expect(screen.getByRole('textbox', { name: 'Ask about the plan' })).toHaveValue('')
})

it.each([
  [429, { code: 'rate_limited', message: 'slow down', retry_after_s: 7 }, /Try again in 7 s/],
  [503, { code: 'transcription_unavailable', message: 'no key', retry_after_s: null }, /not set up on this server/],
  [502, { code: 'transcription_failed', message: 'upstream', retry_after_s: null }, /Could not turn the recording into text/],
])('shows a readable message for server error %s and lets you try again', async (status, detail, message) => {
  stubRecorder(0.2)
  voice = { available: true, transcribe: async () => json({ detail }, status) }
  mockApi()
  await start()
  await record()
  expect(await screen.findByText(message)).toBeVisible()
  expect(await screen.findByRole('button', { name: 'Start recording' })).toBeEnabled()
})

it('explains a denied or missing microphone', async () => {
  const { getUserMedia } = stubRecorder(0.2)
  voice = { available: true, transcribe: async () => transcribed('x') }
  mockApi()
  await start()
  getUserMedia.mockRejectedValueOnce(Object.assign(new DOMException('no', 'NotAllowedError')))
  fireEvent.click(await screen.findByRole('button', { name: 'Start recording' }))
  expect((await screen.findAllByText('Microphone access denied')).length).toBeGreaterThan(0)
  getUserMedia.mockRejectedValueOnce(new DOMException('none', 'NotFoundError'))
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect((await screen.findAllByText('No microphone found')).length).toBeGreaterThan(0)
})

it('releases the microphone and uploads nothing when the chat is closed while recording', async () => {
  const { tracks } = stubRecorder(0.2)
  const transcribe = vi.fn(async () => transcribed('x'))
  voice = { available: true, transcribe }
  mockApi()
  await start()
  fireEvent.click(await screen.findByRole('button', { name: 'Start recording' }))
  await screen.findByRole('button', { name: 'Stop and save recording' })
  await speakFor(300)
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close plan explainer' }))
  await waitFor(() => tracks.forEach((track) => expect(track.stop).toHaveBeenCalled()))
  expect(transcribe).not.toHaveBeenCalled()
})

it('uses the browser speech recognition when the server cannot transcribe', async () => {
  stubRecorder(0.2)
  const recognition = { lang: '', onresult: null, onend: null, onerror: null, onstart: null, start: vi.fn(), stop: vi.fn(), abort: vi.fn() }
  class Recognition { constructor() { return recognition } }
  vi.stubGlobal('webkitSpeechRecognition', Recognition)
  voice = { available: false, transcribe: async () => transcribed('x') }
  mockApi()
  await start()
  const browserMic = await screen.findByRole('button', { name: 'Start microphone' })
  expect(browserMic.textContent).toBe('') // an icon, not the word Mic
  fireEvent.click(browserMic)
  expect(recognition.start).toHaveBeenCalledOnce()
  expect(screen.getByText(/Browser speech services may send audio/)).toBeVisible()
})
