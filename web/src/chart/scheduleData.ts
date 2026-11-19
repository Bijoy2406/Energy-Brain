import type { PlanResponse } from '../api/client'

export interface ScheduleRow {
  time: string
  solar: number
  batteryDischarge: number
  grid: number
  generator: number
  demand: number
  soc: number
  tariff: number
  outage: boolean
}

const siteTime = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Dhaka', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

export const formatBdt = (value: number) => integer.format(value)
export const formatKg = (value: number) => integer.format(value)
export const formatHours = (value: number) => decimal.format(value)
export const formatKwh = (value: number) => integer.format(value)
export const formatKw = (value: number) => decimal.format(value)

export function buildScheduleRows(response: PlanResponse): ScheduleRow[] {
  const { schedule, start } = response.plan
  const startMs = new Date(start).getTime()
  return schedule.solar_used_kw.map((solar, index) => ({
    time: siteTime.format(new Date(startMs + index * 15 * 60_000)),
    solar,
    batteryDischarge: schedule.battery_discharge_kw[index],
    grid: schedule.grid_import_kw[index],
    generator: schedule.generator_kw[index],
    demand: schedule.base_load_kw[index] + schedule.hvac_kw[index]
      + schedule.ev_charge_kw[index] + schedule.battery_charge_kw[index],
    soc: schedule.battery_soc_kwh[index],
    tariff: response.signals.tariff_bdt_per_kwh[index],
    outage: response.signals.outage[index],
  }))
}

export function peakSlots(rows: readonly ScheduleRow[]): ScheduleRow[] {
  if (rows.length === 0) return []
  const maximum = Math.max(...rows.map((row) => row.tariff))
  if (rows.every((row) => row.tariff === maximum)) return []
  return rows.filter((row) => row.tariff === maximum)
}

/** Contiguous grid-down runs, with an exclusive end (day end is 24:00). */
export function outageBlocks(rows: readonly ScheduleRow[]): [string, string][] {
  const blocks: [string, string][] = []
  let from: string | undefined
  rows.forEach((row, index) => {
    if (row.outage && from === undefined) from = row.time
    if (from !== undefined && (!row.outage || index === rows.length - 1)) {
      const [hours, minutes] = row.time.split(':').map(Number)
      const endMinutes = hours * 60 + minutes + 15
      const to = row.outage
        ? `${String(Math.floor(endMinutes / 60)).padStart(2, '0')}:${String(endMinutes % 60).padStart(2, '0')}`
        : row.time
      blocks.push([from, to])
      from = undefined
    }
  })
  return blocks
}
