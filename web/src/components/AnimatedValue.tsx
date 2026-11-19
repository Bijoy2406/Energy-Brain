import { useEffect, useRef, useState } from 'react'
import { useReducedMotion } from '../hooks/useReducedMotion'

export default function AnimatedValue({ value, format }: {
  value: number
  format: (value: number) => string
}) {
  const reducedMotion = useReducedMotion()
  const [display, setDisplay] = useState(value)
  const current = useRef(value)

  useEffect(() => {
    if (reducedMotion) {
      current.current = value
      setDisplay(value)
      return
    }
    const from = current.current
    if (from === value) return
    let start: number | undefined
    let frame: number
    const animate = (now: number) => {
      start ??= now
      const progress = Math.min(1, (now - start) / 300)
      const eased = 1 - (1 - progress) ** 3
      // Display interpolation only; the endpoint is the unmodified API total.
      current.current = progress === 1 ? value : from + (value - from) * eased
      setDisplay(current.current)
      if (progress < 1) frame = requestAnimationFrame(animate)
    }
    frame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frame)
  }, [value, reducedMotion])

  return format(reducedMotion ? value : display)
}
