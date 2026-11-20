import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { ApiResult } from '../api/client'

// Panels are keyed by day and weight: unmount cancels work and discards stale results.
export function useWhatIf<Body, Data>(request: (body: Body, signal: AbortSignal) => Promise<ApiResult<Data>>, cooldown?: RefObject<number>) {
  const active = useRef<AbortController | null>(null)
  const lastBody = useRef<Body | null>(null)
  const [result, setResult] = useState<ApiResult<Data> | null>(null)
  const [loading, setLoading] = useState(false)
  const localCooldown = useRef(0)
  const blockedUntil = cooldown ?? localCooldown
  const [retryAfter, setRetryAfter] = useState(() => Math.max(0, Math.ceil((blockedUntil.current - Date.now()) / 1000)))
  const coolingDown = retryAfter > 0
  useEffect(() => () => active.current?.abort(), [])
  useEffect(() => {
    if (!coolingDown) return
    const timer = setInterval(() => setRetryAfter(Math.max(0, Math.ceil((blockedUntil.current - Date.now()) / 1000))), 250)
    return () => clearInterval(timer)
  }, [coolingDown, blockedUntil])

  function clear() {
    active.current?.abort()
    active.current = null
    lastBody.current = null
    setLoading(false)
    setResult(null)
  }
  async function run(body: Body) {
    if (Date.now() < blockedUntil.current) return
    active.current?.abort()
    const controller = new AbortController()
    active.current = controller
    lastBody.current = body
    setLoading(true)
    setResult(null)
    const response = await request(body, controller.signal)
    if (controller.signal.aborted || active.current !== controller) return
    setLoading(false)
    if (!response.ok && response.error.kind === 'aborted') return
    if (!response.ok && response.error.kind === 'api' && response.error.code === 'rate_limited') {
      const seconds = Math.max(0, Math.ceil(response.error.retry_after_s ?? 0))
      blockedUntil.current = Date.now() + seconds * 1000
      setRetryAfter(seconds)
    }
    setResult(response)
  }
  return { data: result?.ok ? result.data : null, error: result && !result.ok && result.error.kind !== 'aborted' ? result.error : null,
    loading, retryAfter, run, clear, retry: () => { if (lastBody.current !== null) void run(lastBody.current) } }
}
