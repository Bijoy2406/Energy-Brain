import { useCallback, useMemo, useSyncExternalStore } from 'react'

export function useReducedMotion() {
  const media = useMemo(() => window.matchMedia?.('(prefers-reduced-motion: reduce)'), [])
  const subscribe = useCallback((notify: () => void) => {
    media?.addEventListener('change', notify)
    return () => media?.removeEventListener('change', notify)
  }, [media])
  return useSyncExternalStore(subscribe, () => media?.matches ?? false, () => false)
}
