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

describe('schedule dashboard', () => {
  it('loads labelled days, selects the demo day and requests its plan at w=0.5', async () => {
    const fetchMock = mockApi()
    render(<App />)
    const picker = await screen.findByRole('combobox', { name: 'Planning day' })
    expect(within(picker).getByRole('option', { name: '2024-07-01 — Ordinary workday' })).toBeInTheDocument()
    expect(within(picker).getByRole('option', { name: '2024-07-02 — Demo day: monsoon' })).toBeInTheDocument()
    expect(picker).toHaveValue('2024-07-02')
    expect(await screen.findByText('152,703')).toBeInTheDocument()
    expect(screen.getByText('9,093')).toBeInTheDocument()
    expect(screen.getByText('optimal')).toBeInTheDocument()
    expect(screen.getByText('Safe')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/plan', expect.objectContaining({
      method: 'POST', body: '{"date":"2024-07-02","w":0.5}',
    }))
    expect(screen.getAllByText('Synthetic load, simulated PV, synthetic outages. Historical replay.').length).toBeGreaterThan(0)
  })

  it('keeps full planning day labels in the options and selected title', async () => {
    const user = userEvent.setup()
    const label = 'Monsoon planning day with synthetic load, simulated PV, and synthetic outages'
    mockApi(planFixture(), { ...days, days: days.days.map((day) => (
      day.demo ? { ...day, label } : day
    )) })
    render(<App />)
    const picker = await screen.findByRole('combobox', { name: 'Planning day' })
    const fullLabel = `2024-07-02 — ${label}`
    expect(within(picker).getByRole('option', { name: fullLabel })).toBeInTheDocument()
    expect(picker).toHaveAttribute('title', fullLabel)
    await user.selectOptions(picker, '2024-07-01')
    expect(picker).toHaveAttribute('title', '2024-07-01 — Ordinary workday')
  })

  it('renders both real charts, limits, legends and focusable API totals', async () => {
    const plan = planFixture({ cached: true })
    plan.plan.totals.generator_hours = 2
    mockApi(plan)
    const { container } = render(<App />)
    const cost = await screen.findByText('152,703')
    expect(cost.closest('[tabindex="0"]')).toHaveTextContent('BDT')
    expect(screen.getByText('9,093').closest('[tabindex="0"]')).toHaveTextContent('kg')
    expect(screen.getByText('2.0').closest('[tabindex="0"]')).toHaveTextContent('h')
    expect(screen.getByText('cached')).toBeInTheDocument()
    expect(screen.getByText('Forecast: trained model (model)')).toBeInTheDocument()
    const energy = screen.getByRole('region', { name: 'Energy supply and demand chart' })
    const battery = screen.getByRole('region', { name: 'Battery state of charge chart' })
    await waitFor(() => expect(energy.querySelectorAll('.recharts-area')).toHaveLength(4))
    expect(energy.querySelector('.recharts-line')).toBeInTheDocument()
    expect(battery.querySelector('.recharts-line')).toBeInTheDocument()
    expect(within(battery).getByText('Min')).toBeInTheDocument()
    expect(within(battery).getByText('Max')).toBeInTheDocument()
    expect(screen.getByText('Grid outage')).toBeInTheDocument()
    expect(screen.getByText('Peak tariff')).toBeInTheDocument()
    expect(container.querySelectorAll('svg.recharts-surface')).toHaveLength(2)
    for (const chart of [energy, battery]) {
      const ticks = chart.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')
      expect(ticks.length).toBeGreaterThan(0)
      for (const tick of ticks) expect(tick.textContent).toMatch(/^-?[\d,.]+$/)
    }
  })

  it('shows every source and charging-inclusive demand with units through keyboard tooltips', async () => {
    const user = userEvent.setup()
    const plan = planFixture()
    plan.plan.schedule.solar_used_kw.fill(1)
    plan.plan.schedule.battery_discharge_kw.fill(2)
    plan.plan.schedule.grid_import_kw.fill(3)
    plan.plan.schedule.generator_kw.fill(4)
    plan.plan.schedule.base_load_kw.fill(7)
    plan.plan.schedule.battery_charge_kw.fill(3)
    plan.plan.schedule.battery_soc_kwh.fill(25)
    mockApi(plan)
    render(<App />)
    await screen.findByText('152,703')
    const chart = screen.getByRole('application', { name: 'Energy in kW' })
    act(() => chart.focus())
    await user.keyboard('{ArrowRight}')
    const energy = screen.getByRole('region', { name: 'Energy supply and demand chart' })
    for (const value of ['1.0 kW', '2.0 kW', '3.0 kW', '4.0 kW', '10.0 kW']) {
      expect(await within(energy).findByText(value)).toBeInTheDocument()
    }
    const battery = screen.getByRole('region', { name: 'Battery state of charge chart' })
    expect(await within(battery).findByText('25 kWh')).toBeInTheDocument()
  })

  it('shades separate peak windows and full outage slots in both charts', async () => {
    const plan = planFixture()
    plan.signals.outage[0] = true
    plan.signals.outage[95] = true
    plan.signals.tariff_bdt_per_kwh[1] = 10
    plan.signals.tariff_bdt_per_kwh[3] = 10
    mockApi(plan)
    render(<App />)
    await screen.findByText('152,703')
    const energy = screen.getByRole('region', { name: 'Energy supply and demand chart' })
    const battery = screen.getByRole('region', { name: 'Battery state of charge chart' })
    await waitFor(() => expect(energy.querySelectorAll('.recharts-reference-area-rect')).toHaveLength(4))
    expect(battery.querySelectorAll('.recharts-reference-area-rect')).toHaveLength(2)
    for (const area of energy.querySelectorAll('.recharts-reference-area-rect')) {
      expect(Number(area.getAttribute('width'))).toBeGreaterThan(0)
    }
  })

  it('shows an unsafe plan prominently even without backend banners', async () => {
    mockApi(planFixture({ safe: false }))
    render(<App />)
    const warning = await screen.findByRole('alert')
    expect(warning).toHaveTextContent('NOT SAFE TO EXECUTE')
    expect(warning).toHaveTextContent('Diagnostic only')
    expect(screen.getByText('NOT safe')).toBeInTheDocument()
    expect(screen.getByText('DIAGNOSTIC ONLY - NOT SAFE TO EXECUTE')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Energy supply and demand chart' })).toBeInTheDocument()
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
    expect(screen.getByText('Forecast: fallback (model)')).toBeInTheDocument()
  })

  it('uses the first day if the demo day is absent', async () => {
    mockApi(planFixture(), { ...days, days: [days.days[0]] })
    render(<App />)
    await screen.findByText('152,703')
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

  it('keeps the previous chart dimmed until the next day arrives', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi()
    render(<App />)
    await screen.findByText('152,703')
    let resolveNext!: (response: Response) => void
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => { resolveNext = resolve }))
    await user.selectOptions(screen.getByRole('combobox'), '2024-07-01')
    expect(screen.getByText('152,703')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Plan results' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('region', { name: 'Energy supply and demand chart' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Loading plan')
    expect(screen.getByText(/Showing previous plan: 2024-07-02/)).toBeInTheDocument()
    const next = planFixture({ date: '2024-07-01' })
    next.plan.totals.cost_bdt = 321.123
    await act(async () => resolveNext(json(next)))
    expect(await screen.findByText('321')).toBeInTheDocument()
    expect(screen.queryByText('152,703')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Plan results' })).toHaveAttribute('aria-busy', 'false')
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
    await screen.findByText('321')
    await act(async () => resolveOld(json(planFixture({ safe: false }))))
    expect(screen.queryByText('152,703')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
