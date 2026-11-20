import { useId, useState } from 'react'
import { api } from '../api/client'
import './ReportDownload.css'

export default function ReportDownload({ firstDay, lastDay, weight }: {
  firstDay: string; lastDay: string; weight: number
}) {
  const id = useId()
  const [month, setMonth] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const months: string[] = []
  const [firstYear, firstMonth] = firstDay.split('-').map(Number)
  const [lastYear, lastMonth] = lastDay.split('-').map(Number)
  for (let index = firstYear * 12 + firstMonth - 1; index <= lastYear * 12 + lastMonth - 1; index++) {
    months.push(`${Math.floor(index / 12)}-${String(index % 12 + 1).padStart(2, '0')}`)
  }

  async function download() {
    setPending(true)
    setError('')
    try {
      const result = await api.downloadReport(month || null, weight)
      if (!result.ok) {
        if (result.error.kind !== 'aborted') setError(result.error.message)
        return
      }
      const url = URL.createObjectURL(result.data)
      try {
        const link = document.createElement('a')
        link.href = url
        link.download = `energy-brain-report-${month || `${firstDay}_to_${lastDay}`}.pdf`
        document.body.appendChild(link)
        link.click()
        link.remove()
      } finally {
        URL.revokeObjectURL(url)
      }
    } catch {
      setError('The report could not be downloaded. Please try again.')
    } finally {
      setPending(false)
    }
  }

  return <div className="report-download">
    <label htmlFor={id}>Report month
      <select id={id} value={month} disabled={pending} onChange={event => { setMonth(event.target.value); setError('') }}>
        <option value="">Full year</option>
        {months.map(value => <option key={value} value={value}>{value}</option>)}
      </select>
    </label>
    <button type="button" disabled={pending} onClick={() => void download()}>
      {pending ? 'Downloading report…' : 'Download report (PDF)'}
    </button>
    {error && <p className="warning report-download-error" role="alert">{error}</p>}
  </div>
}
