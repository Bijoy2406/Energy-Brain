import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { backtestDaysFixture, backtestFixture } from './test/backtestFixture'
import { days, planFixture } from './test/fixtures'

const json = (body: unknown) => new Response(JSON.stringify(body))
function deferredResponse() {
  let resolve!: (value: Response) => void
  const promise = new Promise<Response>((done) => { resolve = done })
  return { promise, resolve }
}

// Keep the real charts mounted; only supply the layout jsdom cannot measure.
class LayoutObserver implements ResizeObserver {
  static content: LayoutObserver[] = []
  callback: ResizeObserverCallback
  target?: Element
  disconnect = vi.fn()
  constructor(callback: ResizeObserverCallback) { this.callback = callback }
  observe(target: Element) {
    this.target = target
    if (target.classList.contains('backtest-view')) LayoutObserver.content.push(this)
    this.resize(640, 300)
  }
  resize(width: number, height: number) {
    this.callback([{ target: this.target, contentRect: { width, height } } as ResizeObserverEntry], this)
  }
  unobserve() {}
}

function mockApi(summary = Promise.resolve(json(backtestFixture())), daily = Promise.resolve(json(backtestDaysFixture(0.5)))) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/backtest/days')) return (await daily).clone()
    if (url.endsWith('/backtest')) return summary
    if (url.endsWith('/days')) return json(days)
    if (url.endsWith('/plan')) return json(planFixture())
    throw new Error(`Unexpected URL ${url}`)
  }))
}
async function tick(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}
function heading() { return screen.getByRole('heading', { name: 'Limits and methodology' }) }
function scroll() { return vi.mocked(HTMLElement.prototype.scrollIntoView) }
function observer() { return LayoutObserver.content.at(-1)! }

beforeEach(() => {
  vi.useFakeTimers()
  LayoutObserver.content = []
  vi.stubGlobal('ResizeObserver', LayoutObserver)
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, writable: true, value: vi.fn() })
  window.history.replaceState(null, '', '/#limits')
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '/')
})

it.each(['summary', 'daily'])('waits for both fetches and two frames when %s resolves first, then scrolls once before focusing', async (first) => {
  const summary = deferredResponse()
  const daily = deferredResponse()
  mockApi(summary.promise, daily.promise)
  render(<StrictMode><App /></StrictMode>)
  await tick()
  expect(scroll()).not.toHaveBeenCalled()
  await act(async () => {
    if (first === 'summary') summary.resolve(json(backtestFixture()))
    else daily.resolve(json(backtestDaysFixture(0.5)))
  })
  await tick(100)
  expect(scroll()).not.toHaveBeenCalled()
  expect(document.activeElement?.textContent).not.toBe('Limits and methodology')
  await act(async () => {
    if (first === 'summary') daily.resolve(json(backtestDaysFixture(0.5)))
    else summary.resolve(json(backtestFixture()))
  })
  const focus = vi.spyOn(heading(), 'focus')
  expect(scroll()).not.toHaveBeenCalled()
  expect(focus).not.toHaveBeenCalled()
  await tick(16)
  expect(scroll()).not.toHaveBeenCalled()
  await tick(16)
  expect(scroll()).toHaveBeenCalledExactlyOnceWith({ behavior: 'smooth', block: 'start' })
  await tick(300)
  expect(scroll()).toHaveBeenCalledTimes(1)
  expect(focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true })
  expect(scroll().mock.invocationCallOrder[0]).toBeLessThan(focus.mock.invocationCallOrder[0])
  expect(heading()).toHaveFocus()
})

it('corrects late content growth, finishes after 300 ms of stable size, and ignores later layout and weight changes', async () => {
  mockApi()
  render(<App />)
  await tick()
  await tick(32)
  const focus = vi.spyOn(heading(), 'focus')
  expect(scroll()).toHaveBeenCalledTimes(1)
  await tick(200)
  act(() => observer().resize(640, 7500))
  await tick(16)
  expect(scroll()).toHaveBeenCalledTimes(2)
  expect(focus).not.toHaveBeenCalled()
  await tick(299)
  // An observer notification without a size change must not prolong settling.
  act(() => observer().resize(640, 7500))
  await tick(1)
  expect(focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true })
  expect(scroll().mock.invocationCallOrder[1]).toBeLessThan(focus.mock.invocationCallOrder[0])
  expect(observer().disconnect).toHaveBeenCalled()
  act(() => observer().resize(640, 8000))
  fireEvent.click(screen.getByRole('button', { name: 'Cheapest w = 1' }))
  await tick(2500)
  expect(scroll()).toHaveBeenCalledTimes(2)
  expect(focus).toHaveBeenCalledTimes(1)
})

it.each(['wheel', 'keydown', 'touchmove'])('cancels pending corrections and focus immediately on %s', async (type) => {
  mockApi()
  render(<App />)
  await tick()
  await tick(32)
  const focus = vi.spyOn(heading(), 'focus')
  act(() => observer().resize(640, 7500))
  fireEvent(window, new Event(type))
  await tick(2500)
  act(() => observer().resize(640, 8000))
  await tick(32)
  expect(scroll()).toHaveBeenCalledTimes(1)
  expect(focus).not.toHaveBeenCalled()
  expect(observer().disconnect).toHaveBeenCalled()
})

it('stops correcting after two seconds even if content keeps growing', async () => {
  mockApi()
  render(<App />)
  await tick()
  await tick(32)
  const focus = vi.spyOn(heading(), 'focus')
  for (let i = 1; i <= 10; i++) {
    act(() => observer().resize(640, 1000 * i))
    await tick(200)
  }
  expect(focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true })
  const count = scroll().mock.calls.length
  act(() => observer().resize(640, 15000))
  await tick(1000)
  expect(scroll()).toHaveBeenCalledTimes(count)
})

it('uses instant scrolling with reduced motion', async () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
  mockApi()
  render(<App />)
  await tick()
  await tick(350)
  expect(scroll()).toHaveBeenCalledExactlyOnceWith({ behavior: 'instant', block: 'start' })
  expect(heading()).toHaveFocus()
})

it('cancels before the first scroll on user input and allows a new explicit request', async () => {
  mockApi()
  render(<App />)
  await tick()
  const focus = vi.spyOn(heading(), 'focus')
  fireEvent.keyDown(window, { key: 'PageDown' })
  await tick(2500)
  expect(scroll()).not.toHaveBeenCalled()
  expect(focus).not.toHaveBeenCalled()
  fireEvent.click(within(screen.getByText(/Read with caution:/)).getByRole('button', { name: 'See limits' }))
  await tick(350)
  expect(scroll()).toHaveBeenCalledTimes(1)
  expect(focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true })
})

it('cleans up a pending correction and focus when switching tabs during settling', async () => {
  mockApi()
  render(<App />)
  await tick()
  await tick(32)
  const focus = vi.spyOn(heading(), 'focus')
  act(() => observer().resize(640, 7500))
  fireEvent.click(screen.getByRole('tab', { name: "Today's plan" }))
  await tick(2500)
  expect(scroll()).toHaveBeenCalledTimes(1)
  expect(focus).not.toHaveBeenCalled()
  expect(observer().disconnect).toHaveBeenCalled()
})

it('scrolls exactly once for each new request, including after leaving and revisiting the tab', async () => {
  mockApi()
  render(<App />)
  await tick()
  await tick(350)
  const focus = vi.spyOn(heading(), 'focus')
  const button = within(screen.getByText(/Read with caution:/)).getByRole('button', { name: 'See limits' })
  fireEvent.click(button)
  await tick(350)
  expect(scroll()).toHaveBeenCalledTimes(2)
  expect(focus).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('tab', { name: "Today's plan" }))
  fireEvent.click(screen.getByRole('tab', { name: 'Backtest results' }))
  await tick(350)
  expect(scroll()).toHaveBeenCalledTimes(2)
  fireEvent.click(button)
  await tick(350)
  expect(scroll()).toHaveBeenCalledTimes(3)
  expect(focus).toHaveBeenCalledTimes(2)
})

it.each(['banner', 'first card', 'second card', 'same-day'])('uses the settling navigation from the %s entry point while daily values are pending', async (entry) => {
  window.history.replaceState(null, '', entry === 'same-day' ? '/' : '/#backtest')
  const daily = deferredResponse()
  mockApi(undefined, daily.promise)
  render(<App />)
  await tick()
  await tick(300)
  const button = entry === 'same-day'
    ? screen.getByRole('button', { name: 'See limits of these numbers' })
    : within(entry === 'banner' ? screen.getByText(/Read with caution:/) : screen.getAllByRole('article')[entry === 'first card' ? 0 : 1])
      .getByRole('button', { name: 'See limits' })
  fireEvent.click(button)
  await tick(100)
  expect(window.location.hash).toBe('#limits')
  expect(scroll()).not.toHaveBeenCalled()
  const focus = vi.spyOn(heading(), 'focus')
  expect(focus).not.toHaveBeenCalled()
  await act(async () => daily.resolve(json(backtestDaysFixture(0.5))))
  await tick(32)
  expect(scroll()).toHaveBeenCalledTimes(1)
  act(() => observer().resize(375, 7000))
  await tick(350)
  expect(scroll()).toHaveBeenCalledTimes(2)
  expect(focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true })
})
