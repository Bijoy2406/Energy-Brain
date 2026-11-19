import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { days, planFixture } from './test/fixtures'
import { backtestDaysFixture } from './test/backtestFixture'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockApi(plan = planFixture(), dayList = days) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes('/backtest/days')) return json(backtestDaysFixture())
    if (url.endsWith('/days')) return json(dayList)
    if (url.endsWith('/plan')) return json(plan)
    throw new Error(`Unexpected URL: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('schedule dashboard', () => {
  it('gives the schedule its own workspace and renders the floating chat outside the tab layout', async () => {
    mockApi()
    const { container } = render(<App />)
    await screen.findByText('152,703')
    const workspace = container.querySelector('.workspace')!
    expect(workspace.children).toHaveLength(1)
    expect(within(workspace as HTMLElement).getByRole('region', { name: 'Schedule dashboard' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Open plan explainer' }))
    expect(screen.getByRole('dialog').closest('[role="tabpanel"]')).toBeNull()
    expect(screen.getByRole('dialog').parentElement).toBe(document.body)
  })

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
    const chart = await screen.findByRole('application', { name: 'Energy in kW' })
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
    const originalHandler = fetchMock.getMockImplementation()!
    fetchMock.mockImplementation((url: string) => url.endsWith('/plan')
      ? new Promise<Response>((resolve) => { resolveNext = resolve })
      : originalHandler(url))
    await user.selectOptions(screen.getByRole('combobox'), '2024-07-01')
    expect(screen.getByText('152,703')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Plan results' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('region', { name: 'Energy supply and demand chart' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Loading plan')
    expect(screen.getByText(/Showing previous plan: 2024-07-02/)).toBeInTheDocument()
    const next = planFixture({ date: '2024-07-01' })
    next.plan.totals.cost_bdt = 321.123
    await waitFor(() => expect(resolveNext).toBeDefined())
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


const honestNote = 'Cost and CO2 trade off only a little on this site because the grid CO2 factor is flat. The plan changes mostly between Cleanest and about w = 0.26; from there up it is the same cheapest plan.'

describe('cost and carbon control', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  async function start() {
    render(<App />)
    await act(async () => {})
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    return screen.getByRole('slider', { name: 'Cost vs carbon' })
  }

  async function tick(ms = 250) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
  }

  it('shows accessible endpoints, current meaning and the honest note', async () => {
    mockApi()
    const slider = await start()
    expect(slider).toHaveValue('0.5')
    expect(slider).toHaveAttribute('aria-valuetext', 'w = 0.50 - Balanced')
    expect(screen.getByText('w = 0.50 - Balanced')).toBeInTheDocument()
    expect(slider).toHaveAttribute('min', '0')
    expect(slider).toHaveAttribute('max', '1')
    expect(slider).toHaveAttribute('step', '0.05')
    expect(screen.getByText('Cleanest')).toBeInTheDocument()
    expect(screen.getByText('Cheapest')).toBeInTheDocument()
    expect(screen.getByText(honestNote)).toBeInTheDocument()
    expect(screen.queryByText(/Solved at/)).not.toBeInTheDocument()
    fireEvent.change(slider, { target: { value: '0.3' } })
    expect(slider).toHaveAttribute('aria-valuetext', 'w = 0.30 - weighted toward lower CO2')
    expect(screen.getByText('w = 0.30 - weighted toward lower CO2')).toBeInTheDocument()
    fireEvent.change(slider, { target: { value: '0.7' } })
    expect(slider).toHaveAttribute('aria-valuetext', 'w = 0.70 - weighted toward lower cost')
    for (const value of ['0.4', '0.5', '0.6']) {
      fireEvent.change(slider, { target: { value } })
      expect(slider).toHaveAttribute('aria-valuetext', `w = ${Number(value).toFixed(2)} - Balanced`)
    }
    fireEvent.change(slider, { target: { value: '0' } })
    expect(slider).toHaveAttribute('aria-valuetext', 'w = 0.00 - Cleanest')
    fireEvent.change(slider, { target: { value: '1' } })
    expect(slider).toHaveAttribute('aria-valuetext', 'w = 1.00 - Cheapest')
  })

  it('debounces quick changes to the last weight and keeps it when the day changes', async () => {
    const fetchMock = mockApi()
    const slider = await start()
    fireEvent.change(slider, { target: { value: '0.1' } })
    await tick(100)
    fireEvent.change(slider, { target: { value: '0.2' } })
    await tick(100)
    fireEvent.change(slider, { target: { value: '0.3' } })
    await tick(249)
    expect(fetchMock.mock.calls.filter(([url]) => !url.includes('/backtest/'))).toHaveLength(2)
    expect(screen.getByText('152,703')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Plan results' })).toHaveClass('is-stale')
    await tick(1)
    expect(fetchMock.mock.calls.filter(([url]) => !url.includes('/backtest/'))).toHaveLength(3)
    expect(fetchMock).toHaveBeenLastCalledWith('http://localhost:8000/plan', expect.objectContaining({
      body: '{"date":"2024-07-02","w":0.3}',
    }))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '2024-07-01' } })
    await tick()
    expect(slider).toHaveValue('0.3')
    expect(fetchMock).toHaveBeenLastCalledWith('http://localhost:8000/plan', expect.objectContaining({
      body: '{"date":"2024-07-01","w":0.3}',
    }))
  })

  it('aborts the in-flight request and ignores an old response even if fetch resolves after abort', async () => {
    let resolveOld!: (response: Response) => void
    let oldSignal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/days')) return json(days)
      if (JSON.parse(String(init?.body)).w === 0.5) {
        oldSignal = init?.signal as AbortSignal
        return new Promise<Response>((resolve) => { resolveOld = resolve })
      }
      const next = planFixture({ weight: 0.3, weight_requested: 0.3 })
      next.plan.totals.cost_bdt = 321.123
      return json(next)
    }))
    const slider = await start()
    fireEvent.change(slider, { target: { value: '0.3' } })
    await tick()
    expect(oldSignal.aborted).toBe(true)
    expect(screen.getByText('321')).toBeInTheDocument()
    await act(async () => resolveOld(json(planFixture({ safe: false }))))
    expect(screen.getByText('321')).toBeInTheDocument()
    expect(screen.queryByText('152,703')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('stays busy when returning to a previously solved weight during another request', async () => {
    const fetchMock = mockApi()
    const slider = await start()
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}))
    fireEvent.change(slider, { target: { value: '0.3' } })
    await tick()
    fireEvent.change(slider, { target: { value: '0.5' } })
    expect(screen.getByRole('region', { name: 'Plan results' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('152,703')).toBeInTheDocument()
  })

  it('shows the solved API weight and cache tag when the backend rounds the request', async () => {
    mockApi(planFixture({ weight: 0.31, weight_requested: 0.305, cached: true }))
    await start()
    expect(screen.getByText('Solved at w = 0.31')).toBeInTheDocument()
    expect(screen.getByText('cached')).toBeInTheDocument()
  })

  it('retains the previous plan on error and lets the slider recover', async () => {
    const fetchMock = mockApi()
    const slider = await start()
    fetchMock.mockImplementation(async () => json({ detail: { code: 'unavailable', message: 'Try again' } }, 503))
    fireEvent.change(slider, { target: { value: '0.2' } })
    await tick()
    expect(screen.getByRole('alert')).toHaveTextContent('Try again')
    expect(screen.getByText('152,703')).toBeInTheDocument()
    expect(slider).toBeEnabled()
    const next = planFixture({ weight: 0.3, weight_requested: 0.3 })
    next.plan.totals.cost_bdt = 321.123
    fetchMock.mockImplementation(async () => json(next))
    fireEvent.change(slider, { target: { value: '0.3' } })
    await tick()
    await tick(350)
    expect(screen.getByText('321')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps exact API formatting immediately available while totals are enhanced', async () => {
    const initial = planFixture()
    Object.assign(initial.plan.totals, { cost_bdt: 100.125, co2_kg: 20.875, generator_hours: 2.2 })
    const fetchMock = mockApi(initial)
    const slider = await start()
    const next = planFixture()
    Object.assign(next.plan.totals, { cost_bdt: 200.625, co2_kg: 10.125, generator_hours: 1.1 })
    fetchMock.mockImplementation(async () => json(next))
    fireEvent.change(slider, { target: { value: '0.3' } })
    await tick()
    const cards = screen.getByText('Cost · BDT').closest('dl')!
    expect(within(cards).getByText('201')).toBeInTheDocument()
    expect(within(cards).getByText('10')).toBeInTheDocument()
    expect(within(cards).getByText('1.1')).toBeInTheDocument()
  })

  it('shows new totals immediately with reduced motion', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    const fetchMock = mockApi()
    const slider = await start()
    const next = planFixture()
    Object.assign(next.plan.totals, { cost_bdt: 321.123, co2_kg: 12.75, generator_hours: 2.25 })
    fetchMock.mockImplementation(async () => json(next))
    fireEvent.change(slider, { target: { value: '0.3' } })
    await tick()
    expect(screen.getByText('321')).toBeInTheDocument()
    expect(screen.getByText('13')).toBeInTheDocument()
    expect(screen.getByText('2.3')).toBeInTheDocument()
  })

  it('finishes totals and chart transitions when reduced motion is enabled during an update', async () => {
    const listeners = new Set<() => void>()
    const media = {
      matches: false,
      addEventListener: (_event: string, listener: () => void) => { listeners.add(listener) },
      removeEventListener: (_event: string, listener: () => void) => { listeners.delete(listener) },
    }
    vi.stubGlobal('matchMedia', vi.fn(() => media))
    const initial = planFixture()
    initial.plan.schedule.solar_used_kw.fill(1)
    initial.plan.schedule.battery_soc_kwh.fill(0.2)
    const fetchMock = mockApi(initial)
    const slider = await start()
    await tick(350)
    const next = planFixture()
    Object.assign(next.plan.totals, { cost_bdt: 321.123, co2_kg: 12.75, generator_hours: 2.25 })
    next.plan.schedule.solar_used_kw.fill(3)
    next.plan.schedule.grid_import_kw.fill(4)
    next.plan.schedule.battery_soc_kwh.fill(0.8)
    fetchMock.mockImplementation(async () => json(next))
    fireEvent.change(slider, { target: { value: '0.3' } })
    await tick()
    await tick(100)
    const energy = screen.getByRole('region', { name: 'Energy supply and demand chart' })
    const battery = screen.getByRole('region', { name: 'Battery state of charge chart' })
    const paths = () => [
      energy.querySelector('.recharts-area-area')?.getAttribute('d'),
      battery.querySelector('.recharts-line-curve')?.getAttribute('d'),
    ]
    const intermediate = paths()
    // The exact total stays accessible throughout the decorative animation.
    expect(screen.getByText('321')).toBeInTheDocument()
    act(() => {
      media.matches = true
      listeners.forEach((listener) => listener())
    })
    expect(screen.getByText('321')).toBeInTheDocument()
    expect(screen.getByText('13')).toBeInTheDocument()
    expect(screen.getByText('2.3')).toBeInTheDocument()
    const finished = paths()
    expect(finished.every(Boolean)).toBe(true)
    expect(finished[0]).not.toEqual(intermediate[0])
    expect(finished[1]).not.toEqual(intermediate[1])
    await tick(350)
    expect(paths()).toEqual(finished)
  })

  it('never displays cancellation as a network or response error', async () => {
    const fetchMock = mockApi()
    const slider = await start()
    fetchMock.mockRejectedValue(new DOMException('Cancelled', 'AbortError'))
    fireEvent.change(slider, { target: { value: '0' } })
    await tick()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('152,703')).toBeInTheDocument()
  })
})
