import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ThemeToggle from './ThemeToggle'

let media: MediaQueryList
let listener: () => void
beforeEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
  document.documentElement.classList.remove('theme-changing')
  media = { matches: false, addEventListener: vi.fn((_type, fn) => { listener = fn }), removeEventListener: vi.fn() } as unknown as MediaQueryList
  vi.stubGlobal('matchMedia', vi.fn(() => media))
})
afterEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
  document.documentElement.classList.remove('theme-changing')
  document.querySelector('meta[name="theme-color"]')?.remove()
})
it.each([false, true])('defaults to OS dark = %s without saving a preference', (dark) => {
  Object.assign(media, { matches: dark })
  render(<ThemeToggle />)
  expect(document.documentElement.dataset.theme).toBe(dark ? 'dark' : 'light')
  expect(screen.getByRole('button', { name: `Switch to ${dark ? 'light' : 'dark'} theme` })).toHaveAttribute('aria-pressed', String(dark))
  expect(localStorage.getItem('eb-theme')).toBeNull()
  expect(document.documentElement).not.toHaveClass('theme-changing')
})
it.each(['light', 'dark'])('stored %s wins over OS', (theme) => {
  localStorage.setItem('eb-theme', theme)
  Object.assign(media, { matches: theme !== 'dark' })
  render(<ThemeToggle />)
  expect(document.documentElement.dataset.theme).toBe(theme)
})
it('toggles, stores, updates the accessible label and browser chrome', () => {
  const meta = document.createElement('meta')
  meta.name = 'theme-color'
  document.head.append(meta)
  render(<ThemeToggle />)
  fireEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(document.documentElement.style.colorScheme).toBe('dark')
  expect(meta.content).toBe('#07111F')
  expect(localStorage.getItem('eb-theme')).toBe('dark')
  fireEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }))
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(localStorage.getItem('eb-theme')).toBe('light')
  expect(meta.content).toBe('#F4F7FB')
})
it('follows OS changes only until a user chooses', () => {
  render(<ThemeToggle />)
  act(() => { Object.assign(media, { matches: true }); listener() })
  expect(document.documentElement.dataset.theme).toBe('dark')
  fireEvent.click(screen.getByRole('button'))
  act(() => { Object.assign(media, { matches: true }); listener() })
  expect(document.documentElement.dataset.theme).toBe('light')
})
it('ignores invalid preferences and works if storage throws', () => {
  localStorage.setItem('eb-theme', 'invalid')
  Object.assign(media, { matches: true })
  const { unmount } = render(<ThemeToggle />)
  expect(document.documentElement.dataset.theme).toBe('dark')
  unmount()
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  render(<ThemeToggle />)
  fireEvent.click(screen.getByRole('button'))
  expect(document.documentElement.dataset.theme).toBe('light')
  act(() => listener())
  expect(document.documentElement.dataset.theme).toBe('light')
})
