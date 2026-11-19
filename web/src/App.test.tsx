import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import { days, planFixture } from './test/fixtures'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockApi(plan = planFixture(), dayList = days) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith('/days')) return json(dayList)
    if (url.endsWith('/plan')) return json(plan)
    throw new Error(`Unexpected URL: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('dashboard scaffold', () => {
  it('loads labelled days, selects the demo day and requests its plan at w=0.5', async () => {
    const fetchMock = mockApi()
    render(<App />)
    const picker = await screen.findByRole('combobox', { name: 'Planning day' })
    expect(within(picker).getByRole('option', { name: '2024-07-01 — Ordinary workday' })).toBeInTheDocument()
    expect(within(picker).getByRole('option', { name: '2024-07-02 — Demo day: monsoon' })).toBeInTheDocument()
    expect(picker).toHaveValue('2024-07-02')
    expect(await screen.findByText('152702.707123')).toBeInTheDocument()
    expect(screen.getByText('9092.852456')).toBeInTheDocument()
    expect(screen.getByText('optimal')).toBeInTheDocument()
    expect(screen.getByText('true')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/plan', expect.objectContaining({
      method: 'POST', body: '{"date":"2024-07-02","w":0.5}',
    }))
    expect(screen.getByText('Synthetic load, simulated PV, synthetic outages. Historical replay.')).toBeInTheDocument()
  })

  it('shows an unsafe plan prominently even without backend banners', async () => {
    mockApi(planFixture({ safe: false }))
    render(<App />)
    const warning = await screen.findByRole('alert')
    expect(warning).toHaveTextContent('NOT SAFE TO EXECUTE')
    expect(warning).toHaveTextContent('Diagnostic only')
    expect(screen.getByText('false')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /execute|run plan/i })).not.toBeInTheDocument()
  })

  it('shows all backend banners and forecast/signal fallback notes', async () => {
    mockApi(planFixture({ banners: ['Planner warning'],
      forecast_fallback: { used: true, note: 'FALLBACK forecast: sample data', fields: null },
      signal_fallback: { used: true, note: 'FALLBACK signal: analog day', fields: null },
    }))
    render(<App />)
    const warning = await screen.findByRole('alert')
    expect(warning).toHaveTextContent('Planner warning')
    expect(warning).toHaveTextContent('FALLBACK forecast: sample data')
    expect(warning).toHaveTextContent('FALLBACK signal: analog day')
  })

  it('uses the first day if the demo day is absent', async () => {
    mockApi(planFixture(), { ...days, days: [days.days[0]] })
    render(<App />)
    await screen.findByText('152702.707123')
    expect(screen.getByRole('combobox')).toHaveValue('2024-07-01')
  })

  it('handles an empty day list without requesting a plan', async () => {
    const fetchMock = mockApi(planFixture(), { ...days, days: [] })
    render(<App />)
    expect(await screen.findByText('No planning days available.')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('shows loading then backend startup instructions on network failure', async () => {
    let rejectFetch!: (reason: Error) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise((_, reject) => { rejectFetch = reject })))
    render(<App />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading days')
    await act(async () => rejectFetch(new TypeError('Failed to fetch')))
    expect(await screen.findByRole('alert')).toHaveTextContent('uvicorn energy_brain.api.app:app --reload --port 8000')
  })

  it('shows plan errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/days') ? json(days)
      : json({ detail: { code: 'forecast_unavailable', message: 'Forecast unavailable', retry_after_s: null } }, 503)))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Forecast unavailable')
  })

  it('clears the previous plan while loading a newly selected day', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi()
    render(<App />)
    await screen.findByText('152702.707123')
    fetchMock.mockImplementation(() => new Promise(() => {}))
    await user.selectOptions(screen.getByRole('combobox'), '2024-07-01')
    expect(screen.queryByText('152702.707123')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Loading plan')
  })

  it('ignores an old response after the user switches days', async () => {
    const user = userEvent.setup()
    let resolveOld!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/days')) return json(days)
      if (JSON.parse(String(init?.body)).date === '2024-07-02') {
        return new Promise<Response>((resolve) => { resolveOld = resolve })
      }
      const next = planFixture()
      next.plan.totals.cost_bdt = 321.123
      return json(next)
    }))
    render(<App />)
    await waitFor(() => expect(resolveOld).toBeDefined())
    await user.selectOptions(screen.getByRole('combobox'), '2024-07-01')
    await screen.findByText('321.123')
    await act(async () => resolveOld(json(planFixture({ safe: false }))))
    expect(screen.queryByText('152702.707123')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
