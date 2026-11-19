import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import CountUp from './CountUp'
import { formatBdt, formatHours } from '../formatting'

// The enhancement must never replace the selectable/accessibility value.
describe('CountUp stable values', () => {
  it('renders exact formatted totals before entering the viewport and after updates', () => {
    const { rerender } = render(<CountUp to={12345.6} formatter={formatBdt} preserveFinalValue />)
    expect(screen.getByText('12,346')).toBeVisible()
    rerender(<CountUp to={9876.5} formatter={formatBdt} preserveFinalValue />)
    expect(screen.getByText('9,877')).toBeVisible()
  })

  it('keeps decimal precision with reduced motion and schedules no animation', () => {
    vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
      matches: query.includes('prefers-reduced-motion'), media: query,
      onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    }))
    render(<CountUp to={2} formatter={formatHours} preserveFinalValue />)
    expect(screen.getByText('2.0')).toBeVisible()
    expect(document.querySelector('[data-counting]')).toBeNull()
  })

  it('keeps the final text while the visual counter runs on entering view', () => {
    let notify: IntersectionObserverCallback | undefined
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: IntersectionObserverCallback) { notify = callback }
      observe() {}
      unobserve() {}
      disconnect() {}
    })
    const { container } = render(<CountUp to={1234} formatter={formatBdt} preserveFinalValue />)
    act(() => notify?.([{ isIntersecting: true, target: container.firstChild } as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(screen.getByText('1,234')).toBeVisible()
    expect(container.querySelector('[data-counting]')).not.toBeNull()
    vi.unstubAllGlobals()
  })
})
