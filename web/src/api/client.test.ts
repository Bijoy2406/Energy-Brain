import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApiClient } from './client'

function respond(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const api = createApiClient('http://api.test/')

afterEach(() => vi.unstubAllEnvs())

describe('API client', () => {
  it.each([true, false])('defaults to the correct origin with PROD=%s', async (production) => {
    vi.stubEnv('PROD', production)
    vi.stubEnv('VITE_API_BASE_URL', '')
    const fetchMock = respond({})
    await createApiClient().days()
    expect(fetchMock).toHaveBeenCalledWith(production ? '/days' : 'http://localhost:8000/days', expect.anything())
  })

  it.each([true, false])('respects a configured API URL with PROD=%s', async (production) => {
    vi.stubEnv('PROD', production)
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example/')
    const fetchMock = respond({})
    await createApiClient().health()
    expect(fetchMock).toHaveBeenCalledWith('https://api.example/health', expect.anything())
  })

  it('gives useful hosted API connection advice', async () => {
    vi.stubEnv('PROD', true)
    vi.stubEnv('VITE_API_BASE_URL', '')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const result = await createApiClient().days()
    expect(result).toMatchObject({ ok: false, error: { kind: 'network' } })
    if (!result.ok && result.error.kind === 'network') {
      expect(result.error.message).toContain('Space')
      expect(result.error.message).toContain('/health')
      expect(result.error.message).toContain('VITE_API_BASE_URL')
    }
  })

  it('returns successful data unchanged and normalises the base URL', async () => {
    const body = { first_day: '2022-01-01', last_day: '2024-07-02', days: [] }
    const fetchMock = respond(body)
    expect(await api.days()).toEqual({ ok: true, data: body })
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/days', expect.objectContaining({ method: 'GET' }))
  })

  it('gets the Pareto frontier by date with an optional cancellation signal', async () => {
    const body = { date: '2024-07-02', points: [], frontier: [], cached: false }
    const fetchMock = respond(body)
    const { signal } = new AbortController()
    expect(await api.pareto('2024-07-02', signal)).toEqual({ ok: true, data: body })
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/pareto?date=2024-07-02',
      expect.objectContaining({ method: 'GET', signal }))
  })

  it('maps FastAPI validation errors with field locations', async () => {
    const details = [{ loc: ['body', 'w'], msg: 'Must be at most 1', type: 'less_than_equal' }]
    respond({ detail: details }, 422)
    expect(await api.plan({ date: '2024-07-02', w: 2 })).toEqual({
      ok: false, error: { kind: 'validation', status: 422, message: 'body.w: Must be at most 1', details },
    })
  })

  it.each([404, 422, 503, 500])('maps structured errors at HTTP %s', async (status) => {
    respond({ detail: { code: 'unavailable', message: 'Report unavailable', retry_after_s: null } }, status)
    expect(await api.backtest()).toEqual({ ok: false, error: {
      kind: 'api', status, code: 'unavailable', message: 'Report unavailable', retry_after_s: null,
    } })
  })

  it('preserves the explain rate limit and retry interval', async () => {
    respond({ detail: { code: 'rate_limited', message: 'Wait before asking again', retry_after_s: 12.5 } }, 429)
    expect(await api.explain({ date: '2024-07-02', w: 0.5, question: 'Why?' })).toEqual({
      ok: false, error: { kind: 'api', status: 429, code: 'rate_limited', message: 'Wait before asking again', retry_after_s: 12.5 },
    })
  })

  it('maps network failures to a typed error with startup instructions', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const result = await api.days()
    expect(result).toMatchObject({ ok: false, error: { kind: 'network' } })
    if (!result.ok && result.error.kind === 'network') expect(result.error.message).toContain('uvicorn energy_brain.api.app:app --reload --port 8000')
  })

  it('passes a signal to fetch and returns cancellation separately from network failure', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')))
    }))
    vi.stubGlobal('fetch', fetchMock)
    const pending = api.plan({ date: '2024-07-02', w: 0.3 }, controller.signal)
    controller.abort()
    expect(await pending).toEqual({ ok: false, error: { kind: 'aborted' } })
  })

  it('recognises AbortError without a supplied signal', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Cancelled', 'AbortError')))
    expect(await api.days()).toEqual({ ok: false, error: { kind: 'aborted' } })
  })

  it('recognises cancellation while reading the response body', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200,
      json: async () => {
        controller.abort('Superseded')
        throw new DOMException('Cancelled', 'AbortError')
      },
    }))
    expect(await api.plan({ date: '2024-07-02', w: 0 }, controller.signal))
      .toEqual({ ok: false, error: { kind: 'aborted' } })
  })

  it('forwards optional signals on all endpoints', async () => {
    const fetchMock = respond({})
    const { signal } = new AbortController()
    await api.days(signal)
    await api.backtest({ include_sha: true }, signal)
    await api.backtestDays({ weight: 0 }, signal)
    await api.explain({ date: '2024-07-02', w: 0, question: 'Why?' }, signal)
    await api.health(signal)
    expect(fetchMock.mock.calls.every(([, init]) => init.signal === signal)).toBe(true)
  })

  it('returns a typed error for non-JSON responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad gateway', { status: 502 })))
    expect(await api.health()).toEqual({ ok: false, error: {
      kind: 'http', status: 502, message: 'API returned HTTP 502.',
    } })
  })

  it('returns a typed error for invalid JSON on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not JSON')))
    expect(await api.health()).toMatchObject({ ok: false, error: { kind: 'response', status: 200 } })
  })

  it('sends each endpoint with its contract method, query and body', async () => {
    const fetchMock = respond({})
    await api.plan({ date: '2024-07-02', w: 0.5 })
    await api.backtest({ include_sha: true })
    await api.backtestDays({ weight: 0 })
    await api.backtestDays()
    await api.explain({ date: '2024-07-02', w: 0.5, question: 'Why?' })
    await api.health()
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init.method, init.body])).toEqual([
      ['http://api.test/plan', 'POST', '{"date":"2024-07-02","w":0.5}'],
      ['http://api.test/backtest?include_sha=true', 'GET', undefined],
      ['http://api.test/backtest/days?weight=0', 'GET', undefined],
      ['http://api.test/backtest/days', 'GET', undefined],
      ['http://api.test/explain', 'POST', '{"date":"2024-07-02","w":0.5,"question":"Why?"}'],
      ['http://api.test/health', 'GET', undefined],
    ])
  })
})
