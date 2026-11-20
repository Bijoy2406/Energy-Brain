import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import ReportDownload from './ReportDownload'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('offers the full year and each backtest month with an accessible download button', () => {
  render(<ReportDownload firstDay="2024-01-01" lastDay="2024-12-31" weight={0.5} />)
  const select = screen.getByRole('combobox', { name: 'Report month' })
  expect(select).toHaveValue('')
  expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual([
    'Full year', ...Array.from({ length: 12 }, (_, i) => `2024-${String(i + 1).padStart(2, '0')}`),
  ])
  expect(screen.getByRole('button', { name: 'Download report (PDF)' })).toBeEnabled()
})

it('downloads the selected month and weight through the API and releases the blob URL', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response('%PDF-test', {
    headers: { 'Content-Type': 'application/pdf' },
  }))
  vi.stubGlobal('fetch', fetchMock)
  const create = vi.fn().mockReturnValue('blob:report')
  const revoke = vi.fn()
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revoke })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.download).toBe('energy-brain-report-2024-07.pdf')
    expect(this.href).toBe('blob:report')
  })
  const { rerender } = render(<ReportDownload firstDay="2024-01-01" lastDay="2024-12-31" weight={0.5} />)
  fireEvent.change(screen.getByRole('combobox', { name: 'Report month' }), { target: { value: '2024-07' } })
  rerender(<ReportDownload firstDay="2024-01-01" lastDay="2024-12-31" weight={0} />)
  await userEvent.click(screen.getByRole('button', { name: 'Download report (PDF)' }))
  await waitFor(() => expect(click).toHaveBeenCalledOnce())
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/report?month=2024-07&weight=0'),
    expect.objectContaining({ method: 'GET' }))
  // Node's and jsdom's Blob are different classes on some setups (CI), so check the shape, not the class.
  expect(create).toHaveBeenCalledOnce()
  expect(create.mock.calls[0][0]).toMatchObject({ type: 'application/pdf', size: expect.any(Number) })
  expect(revoke).toHaveBeenCalledWith('blob:report')
})

it('shows the API error in an alert and allows retry', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    detail: { code: 'backtest_unavailable', message: 'No backtest report on this server.' },
  }), { status: 404 })))
  render(<ReportDownload firstDay="2024-01-01" lastDay="2024-12-31" weight={0.5} />)
  await userEvent.click(screen.getByRole('button', { name: 'Download report (PDF)' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('No backtest report on this server.')
  expect(screen.getByRole('button', { name: 'Download report (PDF)' })).toBeEnabled()
})
