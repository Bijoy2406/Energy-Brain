import { fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { useDashboardMotion } from './useDashboardMotion'

function Dashboard() {
  const root = useRef<HTMLDivElement>(null)
  useDashboardMotion(root, 'plan')
  return <div ref={root}><div className="summary-cards"><div tabIndex={0}>Selectable total</div></div></div>
}

function pointerMove(card: HTMLElement, pointerType: string) {
  const event = new MouseEvent('pointermove', { bubbles: true, clientX: 80, clientY: 20 })
  Object.defineProperty(event, 'pointerType', { value: pointerType })
  fireEvent(card, event)
}

function media(reduce: boolean, coarse: boolean) {
  vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
    matches: query.includes('reduced-motion') ? reduce : query.includes('coarse') ? coarse : true,
    addEventListener() {}, removeEventListener() {},
  } as unknown as MediaQueryList))
}

describe('dashboard card tilt', () => {
  it('tilts mouse cards, preserves keyboard focus and resets on leaving', () => {
    media(false, false)
    render(<Dashboard />)
    const card = screen.getByText('Selectable total')
    vi.spyOn(card, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 100, height: 100 } as DOMRect)
    pointerMove(card, 'mouse')
    expect(card.style.getPropertyValue('--tilt-x')).toBe('1.5deg')
    expect(parseFloat(card.style.getPropertyValue('--tilt-y'))).toBeCloseTo(1.5)
    card.focus()
    expect(card).toHaveFocus()
    fireEvent.pointerOut(card)
    expect(card.style.getPropertyValue('--tilt-x')).toBe('')
  })

  it.each([[true, false, 'mouse'], [false, true, 'mouse'], [false, false, 'touch']])(
    'does not tilt for reduced=%s coarse=%s pointer=%s', (reduce, coarse, pointer) => {
      media(reduce as boolean, coarse as boolean)
      render(<Dashboard />)
      const card = screen.getByText('Selectable total')
      pointerMove(card, pointer as string)
      expect(card.style.getPropertyValue('--tilt-x')).toBe('')
    },
  )
})
