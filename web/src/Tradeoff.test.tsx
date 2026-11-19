import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import { days, planFixture } from './test/fixtures'
import { paretoFixture } from './test/paretoFixture'
import { backtestFixture, backtestDaysFixture } from './test/backtestFixture'
import { findStable } from './test/stable'
afterEach(() => { window.history.replaceState(null, '', '/'); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
function setupApi(hash = '') {
  window.history.replaceState(null, '', `/${hash}`)
  const fetcher = vi.fn(async (url: string) => {
    const data = url.includes('/pareto') ? paretoFixture({ date: new URL(url).searchParams.get('date')! })
      : url.includes('/backtest/days') ? backtestDaysFixture() : url.endsWith('/backtest') ? backtestFixture()
      : url.endsWith('/days') ? days : planFixture()
    return new Response(JSON.stringify(data))
  })
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}
it('adds the lazy trade-off tab and preserves the planning slider', async () => {
  const fetcher = setupApi()
  render(<App />)
  await screen.findByRole('combobox')
  expect(screen.getAllByRole('tab').map(t => t.textContent)).toEqual(["Today's plan", 'Trade-off', 'Backtest results'])
  expect(fetcher.mock.calls.filter(([url]) => url.includes('/pareto'))).toHaveLength(0)
  fireEvent.change(screen.getByRole('slider'), { target: { value: '0.2' } })
  fireEvent.click(screen.getByRole('button', { name: 'See the trade-off curve' }))
  await screen.findByText(/From the cheapest to the cleanest/)
  fireEvent.click(screen.getByRole('tab', { name: "Today's plan" }))
  expect(screen.getByRole('slider')).toHaveValue('0.2')
})

it('moves through all three tabs with arrows and Home/End, including wraparound', async () => {
  setupApi()
  render(<App />)
  await screen.findByRole('combobox', { name: 'Planning day' })
  const tabs = screen.getAllByRole('tab')
  const press = (index: number, key: string, expected: number) => {
    fireEvent.keyDown(tabs[index], { key })
    expect(tabs[expected]).toHaveFocus()
    expect(tabs[expected]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', tabs[expected].id)
  }
  press(0, 'ArrowRight', 1)
  press(1, 'ArrowRight', 2)
  press(2, 'ArrowRight', 0)
  press(0, 'ArrowLeft', 2)
  press(2, 'Home', 0)
  press(0, 'End', 2)
  press(2, 'ArrowLeft', 1)
  await screen.findByText(/From the cheapest/)
})

it.each([['', "Today's plan"], ['#tradeoff', 'Trade-off'], ['#backtest', 'Backtest results'], ['#limits', 'Backtest results']])('maps the initial hash %s and follows external changes', async (hash, name) => {
  setupApi(hash)
  render(<App />)
  expect(screen.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
  await act(async () => {
    window.history.replaceState(null, '', '/#tradeoff')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
  expect(screen.getByRole('tab', { name: 'Trade-off' })).toHaveAttribute('aria-selected', 'true')
  await screen.findByText(/From the cheapest/)
  await act(async () => {
    window.history.replaceState(null, '', '/')
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
  expect(screen.getByRole('tab', { name: "Today's plan" })).toHaveAttribute('aria-selected', 'true')
})

it('fetches changed days only while open, reuses cached days and applies the selected setting to the plan', async () => {
  const fetcher = setupApi()
  render(<App />)
  await screen.findByRole('combobox', { name: 'Planning day' })
  fireEvent.click(screen.getByRole('tab', { name: 'Trade-off' }))
  await screen.findByText(/From the cheapest/)
  const paretoCalls = () => fetcher.mock.calls.filter(([url]) => url.includes('/pareto'))
  expect(paretoCalls()).toHaveLength(1)
  fireEvent.change(screen.getByRole('combobox', { name: 'Trade-off day' }), { target: { value: '2024-07-01' } })
  await waitFor(() => expect(paretoCalls()).toHaveLength(2))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Trade-off results' })).not.toHaveClass('is-stale'))
  fireEvent.click(await findStable(() => screen.getByRole('button', { name: /Inspect w 0.4 - 1/ })))
  fireEvent.click(await screen.findByRole('button', { name: 'Use this setting' }))
  await waitFor(() => expect(screen.getByRole('tab', { name: "Today's plan" })).toHaveAttribute('aria-selected', 'true'))
  await waitFor(() => expect(screen.getByRole('slider')).toHaveValue('0.4'))
  fireEvent.change(screen.getByRole('combobox', { name: 'Planning day' }), { target: { value: '2024-07-02' } })
  expect(paretoCalls()).toHaveLength(2)
  fireEvent.click(screen.getByRole('tab', { name: 'Trade-off' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Trade-off results' })).not.toHaveClass('is-stale'))
  expect(paretoCalls()).toHaveLength(2)
})
