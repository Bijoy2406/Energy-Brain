import { lazy, Suspense, type ComponentType } from 'react'
import type { AuroraProps } from '../fx/Aurora'
import { useReducedMotion } from '../hooks/useReducedMotion'

// A failed chunk/network request is decorative: retain the static gradient.
const Aurora = lazy<ComponentType<AuroraProps>>(() => import('../fx/Aurora').catch(() => ({ default: () => null })))
const palette = ['#2F6FDB', '#14A38B', '#F5A623']

export default function HeaderAtmosphere() {
  const reduced = useReducedMotion()
  const supported = typeof window.WebGL2RenderingContext !== 'undefined'
    && typeof IntersectionObserver !== 'undefined' && typeof ResizeObserver !== 'undefined'
  return <div className="header-atmosphere" aria-hidden="true">
    {!reduced && supported && <Suspense fallback={null}>
      <Aurora colorStops={palette} speed={0.25} amplitude={0.6} height={0.75}
        layers={2} opacity={0.10} intro={false} mouseInteraction={false}
        dpr={Math.min(window.devicePixelRatio || 1, 1.5)} />
    </Suspense>}
  </div>
}
