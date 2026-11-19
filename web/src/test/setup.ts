import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

configure({ asyncUtilTimeout: 4000 })

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

// jsdom has no layout engine. Give ResponsiveContainer a measured viewport
// while keeping the real Recharts rendering and accessibility behavior.
class TestResizeObserver implements ResizeObserver {
  private callback: ResizeObserverCallback
  constructor(callback: ResizeObserverCallback) { this.callback = callback }
  observe(target: Element) {
    this.callback([{ target, contentRect: { width: 640, height: 300 } } as ResizeObserverEntry], this)
  }
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: TestResizeObserver })

// Keep enhancements out of view unless a test explicitly enters the viewport.
class TestIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(globalThis, 'IntersectionObserver', { configurable: true, writable: true, value: TestIntersectionObserver })
Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: (query: string) => ({
  matches: false, media: query, onchange: null,
  addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
}) })
