import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)

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
