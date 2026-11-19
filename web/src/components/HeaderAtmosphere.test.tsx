import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import HeaderAtmosphere from './HeaderAtmosphere'
import Aurora from '../fx/Aurora'

describe('header graphics fallbacks', () => {
  it('keeps the static fallback without touching canvas when WebGL is unavailable', () => {
    vi.stubGlobal('WebGL2RenderingContext', undefined)
    const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    const { container } = render(<HeaderAtmosphere />)
    expect(container.querySelector('.header-atmosphere')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelector('canvas')).toBeNull()
    expect(context).not.toHaveBeenCalled()
  })

  it('does not mount WebGL for reduced motion even on capable devices', () => {
    vi.stubGlobal('WebGL2RenderingContext', class {})
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true, addEventListener() {}, removeEventListener() {} } as unknown as MediaQueryList)
    const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    const { container } = render(<HeaderAtmosphere />)
    expect(container.querySelector('canvas')).toBeNull()
    expect(context).not.toHaveBeenCalled()
  })

  it.each(['null', 'throw'])('silently retains fallback when context creation returns %s', failure => {
    vi.stubGlobal('WebGL2RenderingContext', class {})
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => {
      if (failure === 'throw') throw new Error('WebGL disabled')
      return null
    })
    const error = vi.spyOn(console, 'error')
    expect(() => render(<Aurora />)).not.toThrow()
    expect(error).not.toHaveBeenCalled()
  })
})

describe('Aurora rendering lifecycle', () => {
  it('caps DPR and stops and resumes frames for visibility and intersection', () => {
    vi.stubGlobal('WebGL2RenderingContext', class {})
    vi.stubGlobal('devicePixelRatio', 3)
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(100)
    let intersection: IntersectionObserverCallback | undefined
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: IntersectionObserverCallback) { intersection = callback }
      observe() {}
      disconnect() {}
    })
    // Exercise scheduling without a GPU; shader and buffer calls are inert.
    const gl = new Proxy({}, { get: (_target, name) => {
      if (name === 'LINK_STATUS') return 1
      if (name === 'getProgramParameter') return (_program: unknown, parameter: number) => parameter === 1 ? true : 0
      if (name === 'getShaderParameter') return () => true
      if (String(name).startsWith('create')) return () => ({})
      return () => undefined
    } })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(kind => (kind === 'webgl2' ? gl : null) as never)
    const frames = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(42)
    const cancel = vi.spyOn(window, 'cancelAnimationFrame')
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    const { container, unmount } = render(<Aurora dpr={3} mouseInteraction={false} />)
    expect(container.querySelector('canvas')?.width).toBe(150)
    expect(frames).toHaveBeenCalled()
    frames.mockClear()
    hidden.mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(cancel).toHaveBeenCalledWith(42)
    expect(frames).not.toHaveBeenCalled()
    hidden.mockReturnValue(false)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(frames).toHaveBeenCalledTimes(1)
    frames.mockClear()
    intersection?.([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver)
    expect(frames).not.toHaveBeenCalled()
    intersection?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)
    expect(frames).toHaveBeenCalledTimes(1)
    unmount()
    expect(cancel).toHaveBeenCalledWith(42)
  })
})
