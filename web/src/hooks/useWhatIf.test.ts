import { act, renderHook } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { useWhatIf } from './useWhatIf'
import type { ApiResult } from '../api/client'

it('aborts a superseded request and ignores its late response', async () => {
  const pending: { signal: AbortSignal; resolve: (value: ApiResult<number>) => void }[] = []
  const request = vi.fn((_body: number, signal: AbortSignal) => new Promise<ApiResult<number>>(resolve => pending.push({ signal, resolve })))
  const { result, unmount } = renderHook(() => useWhatIf(request))
  let first!: Promise<void>
  let second!: Promise<void>
  act(() => { first = result.current.run(1) })
  act(() => { second = result.current.run(2) })
  expect(pending[0].signal.aborted).toBe(true)
  expect(result.current.loading).toBe(true)
  await act(async () => { pending[0].resolve({ ok: true, data: 1 }); await first })
  expect(result.current.data).toBeNull()
  expect(result.current.loading).toBe(true)
  await act(async () => { pending[1].resolve({ ok: true, data: 2 }); await second })
  expect(result.current.data).toBe(2)
  unmount()
  expect(pending[1].signal.aborted).toBe(true)
})
