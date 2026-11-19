import type { components, operations } from './generated'

type Schemas = components['schemas']
export type PlanResponse = Schemas['PlanResponse']
export type DayInfo = Schemas['DayInfo']
type ValidationDetail = Schemas['ValidationError']

export type ApiError =
  | { kind: 'aborted' }
  | { kind: 'validation'; status: number; message: string; details: ValidationDetail[] }
  | ({ kind: 'api'; status: number } & Schemas['ErrorDetail'])
  | { kind: 'network'; message: string }
  | { kind: 'http' | 'response'; status: number; message: string }

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isValidationDetail(value: unknown): value is ValidationDetail {
  return isRecord(value) && Array.isArray(value.loc)
    && value.loc.every((part) => typeof part === 'string' || typeof part === 'number')
    && typeof value.msg === 'string' && typeof value.type === 'string'
}

function mapError(status: number, body: unknown): ApiError {
  const detail = isRecord(body) ? body.detail : undefined
  if (status === 422 && Array.isArray(detail) && detail.every(isValidationDetail)) {
    return {
      kind: 'validation', status, details: detail,
      message: detail.map((item) => `${item.loc.join('.')}: ${item.msg}`).join('; '),
    }
  }
  if (isRecord(detail) && typeof detail.code === 'string' && typeof detail.message === 'string') {
    return {
      kind: 'api', status, code: detail.code, message: detail.message,
      retry_after_s: typeof detail.retry_after_s === 'number' ? detail.retry_after_s : null,
    }
  }
  return { kind: 'http', status, message: `API returned HTTP ${status}.` }
}

export function createApiClient(baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000') {
  const base = baseUrl.replace(/\/+$/, '')

  async function request<T>(path: string, method: 'GET' | 'POST', body?: unknown, signal?: AbortSignal): Promise<ApiResult<T>> {
    const aborted = (error: unknown) => signal?.aborted || (isRecord(error) && error.name === 'AbortError')
    try {
      const response = await fetch(`${base}${path}`, {
        method, signal,
        ...(body === undefined ? {} : {
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        }),
      })
      let data: unknown
      try {
        data = await response.json()
      } catch (error) {
        if (aborted(error)) return { ok: false, error: { kind: 'aborted' } }
        return { ok: false, error: response.ok
          ? { kind: 'response', status: response.status, message: 'API returned an invalid JSON response.' }
          : mapError(response.status, null) }
      }
      if (!response.ok) return { ok: false, error: mapError(response.status, data) }
      // Response types come from the committed OpenAPI contract; no totals are changed here.
      return { ok: true, data: data as T }
    } catch (error) {
      if (aborted(error)) return { ok: false, error: { kind: 'aborted' } }
      return { ok: false, error: {
        kind: 'network',
        message: 'API unreachable. Start the backend with "uvicorn energy_brain.api.app:app --reload --port 8000". Check VITE_API_BASE_URL and the connection.',
      } }
    }
  }

  return {
    days: (signal?: AbortSignal) => request<Schemas['DaysResponse']>('/days', 'GET', undefined, signal),
    plan: (body: Schemas['PlanRequest'], signal?: AbortSignal) => request<PlanResponse>('/plan', 'POST', body, signal),
    backtest: (query?: operations['backtest_backtest_get']['parameters']['query'], signal?: AbortSignal) =>
      request<Schemas['BacktestSummary']>(query?.include_sha === undefined
        ? '/backtest' : `/backtest?include_sha=${query.include_sha}`, 'GET', undefined, signal),
    backtestDays: (query?: operations['backtest_days_backtest_days_get']['parameters']['query'], signal?: AbortSignal) =>
      request<Schemas['BacktestDaysResponse']>(query?.weight == null
        ? '/backtest/days' : `/backtest/days?weight=${query.weight}`, 'GET', undefined, signal),
    explain: (body: Schemas['ExplainRequest'], signal?: AbortSignal) => request<Schemas['ExplainResponse']>('/explain', 'POST', body, signal),
    health: (signal?: AbortSignal) => request<Schemas['HealthResponse']>('/health', 'GET', undefined, signal),
  }
}

export const api = createApiClient()
