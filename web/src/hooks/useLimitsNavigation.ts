import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

/** One navigation per request, including a bounded window for late chart layout. */
export function useLimitsNavigation(
  request: number,
  ready: boolean,
  contentRef: RefObject<HTMLDivElement | null>,
  headingRef: RefObject<HTMLHeadingElement | null>,
) {
  const handledRequest = useRef(0)

  useEffect(() => {
    if (!request) {
      handledRequest.current = 0
      return
    }
    if (!ready || handledRequest.current === request) return
    const content = contentRef.current
    const heading = headingRef.current
    if (!content || !heading) return

    const behavior = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'
    let stopped = false
    let scrolled = false
    let frame = 0
    let stableTimer: ReturnType<typeof setTimeout>
    let deadlineTimer: ReturnType<typeof setTimeout>
    let observer: ResizeObserver | undefined
    let size: { width: number; height: number } | undefined
    const inputEvents = ['wheel', 'touchmove', 'keydown'] as const

    function stop() {
      stopped = true
      cancelAnimationFrame(frame)
      clearTimeout(stableTimer)
      clearTimeout(deadlineTimer)
      observer?.disconnect()
      inputEvents.forEach((event) => window.removeEventListener(event, cancel, true))
    }
    function cancel() {
      handledRequest.current = request
      stop()
    }
    function finish() {
      if (stopped) return
      // Flush any last resize correction when the two-second deadline wins.
      if (frame && scrolled) scroll()
      handledRequest.current = request
      stop()
      heading!.focus({ preventScroll: true })
    }
    function scroll() {
      if (stopped) return
      frame = 0
      scrolled = true
      heading!.scrollIntoView({ behavior, block: 'start' })
      clearTimeout(stableTimer)
      stableTimer = setTimeout(finish, 300)
    }

    inputEvents.forEach((event) => window.addEventListener(event, cancel, { capture: true, passive: true }))
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver((entries) => {
        if (stopped) return
        const rect = entries.find((entry) => entry.target === content)?.contentRect
        if (!rect || (size?.width === rect.width && size?.height === rect.height)) return
        size = { width: rect.width, height: rect.height }
        if (!scrolled) return
        clearTimeout(stableTimer)
        if (!frame) frame = requestAnimationFrame(scroll)
      })
      observer.observe(content)
    }
    // Let the fetched cards and ResponsiveContainer commit and measure first.
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        scroll()
        deadlineTimer = setTimeout(finish, 2000)
      })
    })
    return stop
  }, [request, ready, contentRef, headingRef])
}
