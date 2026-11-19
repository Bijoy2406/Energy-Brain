import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { days, planFixture } from './test/fixtures'
import { backtestDaysFixture, backtestFixture, baselineNames, caveats } from './test/backtestFixture'
import { focusAndPress } from './test/stable'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
function mockApi(report: () => Promise<Response> = async () => json(backtestFixture()), initialPlan = planFixture(), daily: (weight: number) => Promise<Response> = async (weight) => json(backtestDaysFixture(weight))) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes('/backtest/days')) return daily(Number(new URL(url).searchParams.get('weight')))
    if (url.endsWith('/backtest')) return report()
    if (url.endsWith('/days')) return json({ ...days, days: [...days.days,
      { ...days.days[0], date: '2025-07-02', label: 'Outside the replay', in_backtest: false }] })
    if (url.endsWith('/plan')) {
      const request = JSON.parse(String(init?.body))
      return json({ ...initialPlan, date: request.date, weight: initialPlan.weight === 0.31 ? 0.31 : request.w })
    }
    throw new Error(`Unexpected URL ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, writable: true, value: vi.fn() })
})
afterEach(() => { window.history.replaceState(null, '', '/'); vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function openBacktest() {
  await userEvent.click(screen.getByRole('tab', { name: 'Backtest results' }))
  return screen.findByRole('heading', { name: baselineNames.baseline_solar_first })
}

it('keeps date, slider, plan and chat draft mounted while tabs switch, with hash and keyboard navigation', async () => {
  const fetchMock = mockApi()
  const user = userEvent.setup()
  render(<App />)
  await screen.findByText('152,703')
  await user.selectOptions(screen.getByRole('combobox'), '2024-07-01')
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.3' } })
  await user.type(screen.getByRole('textbox'), 'My draft question')
  const planTab = screen.getByRole('tab', { name: "Today's plan" })
  planTab.focus()
  await user.keyboard('{End}')
  expect(screen.getByRole('tab', { name: 'Backtest results' })).toHaveFocus()
  expect(window.location.hash).toBe('#backtest')
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'backtest-tab')
  await screen.findByRole('heading', { name: baselineNames.baseline_solar_first })
  await user.keyboard('{Home}')
  expect(planTab).toHaveAttribute('aria-selected', 'true')
  expect(window.location.hash).toBe('')
  expect(screen.getByRole('slider')).toHaveValue('0.3')
  expect(screen.getByRole('combobox')).toHaveValue('2024-07-01')
  expect(screen.getByRole('textbox')).toHaveValue('My draft question')
  expect(screen.getByText('152,703')).toBeInTheDocument()
  await openBacktest()
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/backtest'))).toHaveLength(1)
})

it('opens from the hash, renders signed comparisons, every caveat, forecast and provenance', async () => {
  window.history.replaceState(null, '', '/#backtest')
  mockApi()
  render(<App />)
  const heading = await screen.findByRole('heading', { name: baselineNames.baseline_solar_first })
  const card = heading.closest('article')!
  expect(card).toHaveTextContent('20.0 % lower cost (40 BDT less)')
  expect(card).toHaveTextContent('0.3 % higher CO2 (8 kg more)')
  expect(card).toHaveTextContent('56.0 fewer generator hours')
  expect(card).toHaveTextContent('12 more diesel litres')
  expect(card).toHaveTextContent('Costlier than the baseline on 3 of 366 days')
  expect(card).toHaveTextContent('Median per-day cost saving')
  expect(card).toHaveTextContent('Load-shedding days (138): 25.0 % lower cost')
  expect(card).toHaveTextContent('Other days (228): 2.0 % higher cost')
  for (const caveat of caveats) expect(screen.getAllByText(caveat).length).toBeGreaterThan(0)
  expect(screen.getByText('Grid factor 0.62 kg/kWh (test source)')).toBeInTheDocument()
  expect(screen.getByText('Diesel factor 2.68 kg/litre (test source)')).toBeInTheDocument()
  expect(screen.getByText('Synthetic office load')).toBeInTheDocument()
  for (const source of ['Simulated PV', 'Observed weather', 'Synthetic outage calendar']) expect(screen.getByText(source)).toBeInTheDocument()
  expect(screen.getByText('abcdef1')).toBeInTheDocument()
  expect(screen.getByText('How this was measured').closest('details')).toHaveAttribute('open')
  expect(screen.getByText(/All plans optimal/)).toHaveTextContent('safe / Risk ok')
  expect(screen.getByRole('region', { name: 'Forecast quality' })).toHaveTextContent('12.1 kW')
})

it('puts the only caution banner before results and all twelve full caveats after measurement', async () => {
  mockApi()
  render(<App />)
  await openBacktest()
  const panel = screen.getByRole('tabpanel')
  const banner = within(panel).getByText(/Read with caution:/)
  const toggle = within(panel).getByRole('group', { name: 'Backtest weight' })
  expect(toggle.nextElementSibling).toBe(banner)
  const cards = within(panel).getAllByRole('article')
  expect(cards).toHaveLength(2)
  const daysCovered = within(panel).getByRole('region', { name: 'Days covered' })
  const forecast = within(panel).getByRole('region', { name: 'Forecast quality' })
  const chart = within(panel).getByRole('region', { name: 'Daily cost comparison' })
  await within(chart).findByRole('application')
  const limits = within(panel).getByRole('region', { name: 'Limits and methodology' })
  const measurement = within(panel).getByText('How this was measured').closest('details')!
  const ordered = [banner, ...cards, daysCovered, forecast, chart, measurement, limits]
  ordered.slice(1).forEach((element, index) => {
    expect(ordered[index].compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
  expect(limits.parentElement).toBe(chart.parentElement)
  expect(limits).toHaveTextContent('12 limits in 4 groups')
  expect(limits).not.toHaveClass('warning')
  expect(panel.querySelectorAll('.warning')).toHaveLength(1)
  expect(limits.querySelectorAll('li')).toHaveLength(12)
  expect(limits.querySelectorAll('details')).toHaveLength(4)
  for (const group of limits.querySelectorAll('details')) expect(group).not.toHaveAttribute('open')
  for (const caveat of caveats) expect(within(limits).getByText(caveat)).toBeInTheDocument()
  for (const card of [...cards, daysCovered, forecast, chart]) expect(card).toHaveTextContent('synthetic data, 2024 replay')
})

it('rounds summary and forecast values and uses neutral wording for tiny percentages in every split', async () => {
  const report = backtestFixture()
  const values = report.summary.by_weight['0.5'].vs.baseline_solar_first
  Object.assign(values, { cost_saved_pct: 4.7, cost_saved_bdt: 2075147.8029, co2_saved_kg: -8388.1019,
    generator_hours_saved: 54.25, diesel_litres_saved: 20761.1569 })
  values.per_day_cost_saved_pct.median = -0.04
  Object.assign(values.outage_days, { cost_saved_pct: 0.03, co2_saved_pct: -0.04 })
  Object.assign(values.no_outage_days, { cost_saved_pct: -0.04, co2_saved_pct: 0.03 })
  report.summary.forecast.load_p10_p90_coverage_pct = 88.4567
  mockApi(async () => json(report))
  render(<App />)
  await openBacktest()
  const card = screen.getByRole('article', { name: `Energy Brain vs ${baselineNames.baseline_solar_first}` })
  expect(card).toHaveTextContent('4.7 % lower cost (2,075,148 BDT less)')
  expect(card).toHaveTextContent('0.3 % higher CO2 (8,388 kg more)')
  expect(card).toHaveTextContent('54.3 fewer generator hours')
  expect(card).toHaveTextContent('20,761 fewer diesel litres')
  expect(card).toHaveTextContent(`Median per-day cost saving vs ${baselineNames.baseline_solar_first}: about the same cost`)
  for (const label of ['Load-shedding days (138)', 'Other days (228)']) {
    expect(within(card).getByText(new RegExp(label.replace(/[()]/g, '\\$&')))).toHaveTextContent(
      `${label}: about the same cost vs ${baselineNames.baseline_solar_first} / about the same CO2`,
    )
  }
  const forecast = screen.getByRole('region', { name: 'Forecast quality' })
  expect(forecast).toHaveTextContent('12.1 kW')
  expect(forecast).toHaveTextContent('8.1 kW')
  expect(forecast).toHaveTextContent('88.5 %')
})

it('rounds daily costs and CO2 in the same-day table and chart tooltip without changing savings calculations', async () => {
  mockApi(undefined, undefined, async (weight) => {
    const daily = backtestDaysFixture(weight)
    for (const day of daily.days) {
      day.methods.optimiser.adjusted_cost_bdt = 153157.6373
      day.methods.optimiser.adjusted_co2_kg = 9111.5024
      day.methods.baseline_solar_first.adjusted_cost_bdt = 169145.81
    }
    return json(daily)
  })
  render(<App />)
  const sameDay = await screen.findByRole('region', { name: 'Baseline vs Energy Brain, same day' })
  await within(sameDay).findByRole('table')
  expect(sameDay).toHaveTextContent('Energy Brain saves 15,988 BDT (9.5 % lower cost)')
  expect(within(sameDay).getByText('153,158')).toBeInTheDocument()
  expect(within(sameDay).getByText('9,112')).toBeInTheDocument()
  expect(within(sameDay).getByRole('button', { name: 'See limits of these numbers' })).toBeInTheDocument()
  for (const caveat of caveats) expect(within(sameDay).queryByText(caveat)).not.toBeInTheDocument()
  await openBacktest()
  await focusAndPress(
    () => screen.getByRole('application', { name: /Daily cost saved in BDT/ }),
    '{ArrowRight}',
    () => expect(within(screen.getByRole('region', { name: 'Daily cost comparison' })).getByText('2024-07-02 · Tue')).toBeInTheDocument(),
  )
  const region = screen.getByRole('region', { name: 'Daily cost comparison' })
  expect(region).toHaveTextContent('169,146 BDT')
  expect(region).toHaveTextContent('153,158 BDT')
  expect(region).toHaveTextContent('15,988 BDT less')
  const axisTicks = region.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')
  expect(axisTicks.length).toBeGreaterThan(0)
  for (const tick of axisTicks) expect(tick.textContent).toMatch(/^-?\d{1,3}(,\d{3})*$/)
})

it.each([0.03, -0.04])('uses about the same cost in the same-day card for %s percent savings', async (saved) => {
  mockApi(undefined, undefined, async (weight) => {
    const daily = backtestDaysFixture(weight)
    for (const day of daily.days) day.methods.optimiser.adjusted_cost_bdt = 100 - saved
    return json(daily)
  })
  render(<App />)
  const card = await screen.findByRole('region', { name: 'Baseline vs Energy Brain, same day' })
  expect(await within(card).findByRole('table')).toHaveTextContent('(about the same cost)')
  expect(card).not.toHaveTextContent('0.0 %')
})

it('updates summary numbers and requests daily values at each selected weight', async () => {
  const fetchMock = mockApi()
  render(<App />)
  await openBacktest()
  for (const [label, weight, result] of [['Cheapest w = 1', 1, '30.0 % lower cost (60 BDT less)'],
    ['Cleanest w = 0', 0, '10.0 % higher cost (20 BDT more)'],
    ['Balanced w = 0.5 (headline)', 0.5, '20.0 % lower cost (40 BDT less)']] as const) {
    await userEvent.click(screen.getByRole('button', { name: label }))
    expect(screen.getByText(result)).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`http://localhost:8000/backtest/days?weight=${weight}`, expect.anything()))
  }
})

it.each(['unavailable', 'network'])('shows an independent %s error without breaking the plan', async (kind) => {
  mockApi(async () => {
    if (kind === 'network') throw new TypeError('offline')
    return json({ detail: { code: 'backtest_unavailable', message: 'Run python -m energy_brain.evaluator to create the report.' } }, 404)
  })
  render(<App />)
  await userEvent.click(screen.getByRole('tab', { name: 'Backtest results' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(kind === 'network' ? 'API unreachable' : 'Run python -m energy_brain.evaluator to create the report.')
  await userEvent.click(screen.getByRole('tab', { name: "Today's plan" }))
  expect(await screen.findByText('152,703')).toBeInTheDocument()
})

it('shows same-day adjusted values and savings at the solved weight', async () => {
  mockApi()
  render(<App />)
  const card = await screen.findByRole('region', { name: 'Baseline vs Energy Brain, same day' })
  expect(await within(card).findByRole('table')).toHaveTextContent('80')
  expect(within(card).getAllByRole('row')).toHaveLength(4)
  expect(card).toHaveTextContent('Energy Brain saves 20 BDT (20.0 % lower cost)')
  expect(card).toHaveTextContent('synthetic data, 2024 replay')
  expect(card).toHaveTextContent('Adjusted figures price any battery end-of-day shortfall')
  expect(within(card).getByRole('button', { name: 'See limits of these numbers' })).toBeInTheDocument()
  for (const caveat of caveats) expect(within(card).queryByText(caveat)).not.toBeInTheDocument()
})

it('explains a non-backtest date and an unsupported solved weight', async () => {
  mockApi()
  render(<App />)
  await screen.findByText('152,703')
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.3' } })
  expect(await screen.findByText(/Move the slider to 0, 0.5 or 1/)).toBeInTheDocument()
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.5' } })
  await userEvent.selectOptions(screen.getByRole('combobox'), '2025-07-02')
  expect(await screen.findByText(/Date outside the backtest/)).toBeInTheDocument()
})

it('uses solved weight rather than requested slider weight', async () => {
  const fetchMock = mockApi(undefined, planFixture({ weight: 0.31 }))
  render(<App />)
  expect(await screen.findByText(/Move the slider to 0, 0.5 or 1/)).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([url]) => url.includes('/backtest/days'))).toBe(false)
})

it('renders the daily bar chart and switches its named baseline; keyboard tooltip uses adjusted costs', async () => {
  mockApi()
  render(<App />)
  await openBacktest()
  await focusAndPress(
    () => screen.getByRole('application', { name: /Daily cost saved in BDT/ }),
    '{ArrowRight}',
    () => expect(within(screen.getByRole('region', { name: 'Daily cost comparison' })).getByText('2024-07-02 · Tue')).toBeInTheDocument(),
  )
  const region = screen.getByRole('region', { name: 'Daily cost comparison' })
  expect(region).toHaveTextContent('100 BDT')
  expect(region).toHaveTextContent('80 BDT')
  expect(region).toHaveTextContent('20 BDT less')
  expect(region.querySelector('pattern')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Time-of-use' }))
  expect(region).toHaveTextContent(`Daily cost saved vs ${baselineNames.baseline_time_of_use} (computed from the daily values returned by the API)`)
})

it('keeps plan and summary usable when daily comparison loading fails with 422', async () => {
  mockApi(undefined, undefined, async () => json({ detail: { code: 'weight_not_in_backtest', message: 'This weight was not evaluated.' } }, 422))
  render(<App />)
  expect(await screen.findByText(/Same-day comparison unavailable/)).toHaveTextContent('This weight was not evaluated.')
  expect(screen.getByText('152,703')).toBeInTheDocument()
  await openBacktest()
  expect(await screen.findByRole('alert')).toHaveTextContent('This weight was not evaluated.')
  expect(screen.getByText('20.0 % lower cost (40 BDT less)')).toBeInTheDocument()
})

it('shows daily loading independently and ignores a late response for the previous weight', async () => {
  let resolveCheapest!: (response: Response) => void
  mockApi(undefined, undefined, async (weight) => weight === 1
    ? new Promise<Response>((resolve) => { resolveCheapest = resolve }) : json(backtestDaysFixture(weight)))
  render(<App />)
  await openBacktest()
  await userEvent.click(screen.getByRole('button', { name: 'Cheapest w = 1' }))
  expect(screen.getByRole('status')).toHaveTextContent('Loading daily backtest values for w = 1')
  expect(screen.getByText('30.0 % lower cost (60 BDT less)')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Cleanest w = 0' }))
  const region = screen.getByRole('region', { name: 'Daily cost comparison' })
  await within(region).findByRole('application')
  await act(async () => resolveCheapest(json(backtestDaysFixture(1))))
  expect(region).toHaveTextContent('w = 0.')
  expect(region).not.toHaveTextContent('w = 1.')
})

it('renders excluded dates and nonzero health counts from the selected summary', async () => {
  const report = backtestFixture()
  const fixture = { ...report, summary: { ...report.summary,
    days: { ...report.summary.days, included: 365, excluded: 1, excluded_dates: [{ date: '2024-06-01', reason: 'unsafe settlement' }] },
    by_weight: { ...report.summary.by_weight, '0.5': { ...report.summary.by_weight['0.5'],
      plan_health: { days_planned: 366, not_optimal: 2, unsafe: 1, risk_not_ok: 3, replanned: 4, fallback_used: 5 } } },
  } }
  mockApi(async () => json(fixture))
  render(<App />)
  await openBacktest()
  expect(screen.getByRole('region', { name: 'Days covered' })).toHaveTextContent('365 of 366 days included')
  expect(screen.getByText('2024-06-01: unsafe settlement')).toBeInTheDocument()
  expect(screen.getByText(/Plan health:/)).toHaveTextContent('2 not optimal / 1 unsafe / 3 Risk not ok')
})

it('shows summary loading without delaying the mounted plan and follows external hash changes', async () => {
  let resolveReport!: (response: Response) => void
  mockApi(() => new Promise<Response>((resolve) => { resolveReport = resolve }))
  render(<App />)
  await screen.findByText('152,703')
  act(() => {
    window.history.replaceState(null, '', '/#backtest')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
  expect(screen.getByRole('status')).toHaveTextContent('Loading backtest results')
  expect(document.querySelector('#plan-panel')).toHaveAttribute('hidden')
  await act(async () => resolveReport(json(backtestFixture())))
  expect(await screen.findByRole('heading', { name: baselineNames.baseline_solar_first })).toBeInTheDocument()
})

it('does not label the previous day settlement as the selected day while its plan is pending', async () => {
  const fetchMock = mockApi()
  render(<App />)
  const region = await screen.findByRole('region', { name: 'Baseline vs Energy Brain, same day' })
  await within(region).findByRole('table')
  fetchMock.mockImplementation(() => new Promise<Response>(() => {}))
  await userEvent.selectOptions(screen.getByRole('combobox'), '2024-07-01')
  expect(within(region).queryByRole('table')).not.toBeInTheDocument()
  expect(region).toHaveTextContent('Waiting for the solved plan for 2024-07-01')
})


it.each([false, true])('navigates from the same-day card and focuses limits after loading (delayed: %s)', async (delayed) => {
  let resolveReport!: (response: Response) => void
  mockApi(delayed ? () => new Promise<Response>((resolve) => { resolveReport = resolve }) : undefined)
  render(<App />)
  const card = await screen.findByRole('region', { name: 'Baseline vs Energy Brain, same day' })
  await within(card).findByRole('table')
  await userEvent.click(within(card).getByRole('button', { name: 'See limits of these numbers' }))
  expect(screen.getByRole('tab', { name: 'Backtest results' })).toHaveAttribute('aria-selected', 'true')
  expect(window.location.hash).toBe('#limits')
  if (delayed) {
    expect(screen.getByRole('status')).toHaveTextContent('Loading backtest results')
    await act(async () => resolveReport(json(backtestFixture())))
  }
  const heading = await screen.findByRole('heading', { name: 'Limits and methodology' })
  await waitFor(() => expect(heading).toHaveFocus())
  expect(heading).toHaveAttribute('tabindex', '-1')
  expect(heading.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
  expect(heading.closest('section')).toHaveAttribute('id', 'limits')
  expect(heading.closest('section')!.querySelectorAll('details[open]')).toHaveLength(0)
})

it.each([false, true])('opens and focuses limits from the URL hash (reduced motion: %s)', async (reduced) => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: reduced, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
  window.history.replaceState(null, '', '/#limits')
  mockApi()
  render(<App />)
  expect(screen.getByRole('tab', { name: 'Backtest results' })).toHaveAttribute('aria-selected', 'true')
  const heading = await screen.findByRole('heading', { name: 'Limits and methodology' })
  await waitFor(() => expect(heading).toHaveFocus())
  expect(window.matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
  expect(heading.scrollIntoView).toHaveBeenCalledWith({ behavior: reduced ? 'instant' : 'smooth', block: 'start' })
})

it('focuses the limits heading from the banner and both comparison cards on every visit', async () => {
  mockApi()
  render(<App />)
  await openBacktest()
  const heading = screen.getByRole('heading', { name: 'Limits and methodology' })
  const banner = screen.getByText(/Read with caution:/)
  const cards = screen.getAllByRole('article')
  for (const container of [banner, ...cards, banner]) {
    await userEvent.click(within(container).getByRole('button', { name: 'See limits' }))
    await waitFor(() => expect(heading).toHaveFocus())
  }
  await userEvent.click(screen.getByRole('tab', { name: "Today's plan" }))
  act(() => {
    window.history.replaceState(null, '', '/#limits')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
  await waitFor(() => expect(heading).toHaveFocus())
  expect(screen.getByRole('tab', { name: 'Backtest results' })).toHaveAttribute('aria-selected', 'true')
})

it('does not steal focus when a pending limits request is cancelled by switching tabs', async () => {
  let resolveReport!: (response: Response) => void
  mockApi(() => new Promise<Response>((resolve) => { resolveReport = resolve }))
  window.history.replaceState(null, '', '/#limits')
  render(<App />)
  await userEvent.click(screen.getByRole('tab', { name: "Today's plan" }))
  await act(async () => resolveReport(json(backtestFixture())))
  expect(screen.getByRole('tab', { name: "Today's plan" })).toHaveFocus()
  expect(document.querySelector('#backtest-panel')).toHaveAttribute('hidden')
})
