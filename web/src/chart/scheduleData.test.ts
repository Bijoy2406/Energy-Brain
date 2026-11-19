import { describe, expect, it } from 'vitest'
import { planFixture } from '../test/fixtures'
import { buildScheduleRows, peakSlots, outageBlocks, formatBdt, formatKg, formatHours, formatKwh, formatKw } from './scheduleData'

function shortPlan() {
  const response = planFixture()
  for (const key of Object.keys(response.plan.schedule) as (keyof typeof response.plan.schedule)[]) {
    response.plan.schedule[key] = response.plan.schedule[key].slice(0, 4)
  }
  Object.assign(response.plan.schedule, {
    solar_used_kw: [1, 2, 3, 4], battery_discharge_kw: [2, 2, 2, 2],
    grid_import_kw: [3, 4, 5, 6], generator_kw: [4, 4, 4, 4],
    base_load_kw: [4, 6, 8, 10], hvac_kw: [1, 1, 1, 1],
    ev_charge_kw: [2, 2, 2, 2], battery_charge_kw: [3, 3, 3, 3],
    battery_soc_kwh: [10, 11, 12, 13],
  })
  response.signals.tariff_bdt_per_kwh = [5, 10, 10, 5]
  response.signals.outage = [false, true, true, false]
  return response
}

describe('schedule rows', () => {
  it('uses all schedule slots and labels the day in Asia/Dhaka', () => {
    const rows = buildScheduleRows(planFixture())
    expect(rows).toHaveLength(96)
    expect(rows.slice(0, 3).map((row) => row.time)).toEqual(['00:00', '00:15', '00:30'])
    expect(rows[95].time).toBe('23:45')
  })

  it('maps a four-slot schedule, including charging demand and end-of-slot SoC', () => {
    const rows = buildScheduleRows(shortPlan())
    expect(rows).toHaveLength(4)
    expect(rows.map((row) => row.time)).toEqual(['00:00', '00:15', '00:30', '00:45'])
    expect(rows[1]).toEqual({ time: '00:15', solar: 2, batteryDischarge: 2, grid: 4,
      generator: 4, demand: 12, soc: 11, tariff: 10, outage: true })
    for (const row of rows) {
      expect(Math.abs(row.demand - (row.solar + row.batteryDischarge + row.grid + row.generator))).toBeLessThan(1e-6)
    }
  })

  it('uses the actual start instant rather than the browser timezone or midnight', () => {
    const response = shortPlan()
    response.plan.start = '2024-07-01T18:30:00Z'
    expect(buildScheduleRows(response).map((row) => row.time)).toEqual(['00:30', '00:45', '01:00', '01:15'])
  })
})

describe('tariff and outage windows', () => {
  it('selects every maximum-tariff slot, including separated peaks', () => {
    const rows = buildScheduleRows(shortPlan())
    rows[3].tariff = 10
    expect(peakSlots(rows).map((row) => row.time)).toEqual(['00:15', '00:30', '00:45'])
    rows[2].tariff = 5
    expect(peakSlots(rows).map((row) => row.time)).toEqual(['00:15', '00:45'])
  })

  it('has no peak window for flat or empty tariffs', () => {
    expect(peakSlots(buildScheduleRows(planFixture()))).toEqual([])
    expect(peakSlots([])).toEqual([])
  })

  it.each([
    [[false, true, true, false], [['00:15', '00:45']]],
    [[true, false, true, false], [['00:00', '00:15'], ['00:30', '00:45']]],
    [[false, false, true, true], [['00:30', '01:00']]],
    [[false, false, false, false], []],
  ])('groups %j into exclusive-end blocks %j', (flags, expected) => {
    const rows = buildScheduleRows(shortPlan()).map((row, index) => ({ ...row, outage: flags[index] }))
    expect(outageBlocks(rows)).toEqual(expected)
  })

  it('ends a final-slot outage at 24:00 and handles no rows', () => {
    const rows = buildScheduleRows(planFixture())
    rows[95].outage = true
    expect(outageBlocks(rows)).toEqual([['23:45', '24:00']])
    expect(outageBlocks([])).toEqual([])
  })
})

it('formats reported values in en-US without changing their units', () => {
  expect(formatBdt(152702.707)).toBe('152,703')
  expect(formatKg(9092.85)).toBe('9,093')
  expect(formatHours(2)).toBe('2.0')
  expect(formatKwh(1234.6)).toBe('1,235')
  expect(formatKw(1234.56)).toBe('1,234.6')
})
