import { lazy, Suspense } from 'react'
import { useReducedMotion } from '../hooks/useReducedMotion'

const CountUp = lazy(() => import('../fx/CountUp'))

export default function AnimatedValue({ value, format }: {
  value: number
  format: (value: number) => string
}) {
  const reducedMotion = useReducedMotion()
  if (reducedMotion || typeof IntersectionObserver === 'undefined') return format(value)
  return <Suspense fallback={format(value)}>
    <CountUp to={value} formatter={format} preserveFinalValue duration={0.85} />
  </Suspense>
}
