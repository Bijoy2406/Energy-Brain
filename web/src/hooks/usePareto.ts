import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { ApiResult } from '../api/client'
import type { ParetoResponse } from '../tradeoff/tradeoffData'

// Owned by the mounted tab for this App session; revisiting a day reuses its result.
export function usePareto(date: string, active: boolean) {
  const cache = useRef(new Map<string, ApiResult<ParetoResponse>>())
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<{ date: string; attempt: number; result: ApiResult<ParetoResponse> } | null>(null)
  const [previous, setPrevious] = useState<ParetoResponse | null>(null)
  const [retryAfter, setRetryAfter] = useState(0)
  const force = useRef(false)
  const coolingDown = retryAfter > 0
  useEffect(() => {
    if (!coolingDown) return
    const timer = setInterval(() => setRetryAfter(seconds => Math.max(0, seconds - 1)), 1000)
    return () => clearInterval(timer)
  }, [coolingDown])
  useEffect(() => {
    if (!active || !date) return
    const controller = new AbortController()
    const cached = force.current ? undefined : cache.current.get(date)
    force.current = false
    const request = cached ? Promise.resolve(cached) : api.pareto(date, controller.signal)
    void request.then(result => {
      if (controller.signal.aborted || (!result.ok && result.error.kind === 'aborted')) return
      cache.current.set(date, result)
      if (result.ok) {
        setPrevious(result.data)
      }
      if (!cached && !result.ok && result.error.kind === 'api' && result.error.status === 429) {
        setRetryAfter(Math.max(0, Math.ceil(result.error.retry_after_s ?? 0)))
      }
      setState({ date, attempt, result })
    })
    return () => controller.abort()
  }, [date, active, attempt])
  const current = state?.date === date && state.attempt === attempt ? state.result : null
  const error = current && !current.ok && current.error.kind !== 'aborted' ? current.error : null
  return { data: current?.ok ? current.data : previous, loading: !!date && !current, error, retryAfter,
    retry: () => { force.current = true; setAttempt(value => value + 1) } }
}
