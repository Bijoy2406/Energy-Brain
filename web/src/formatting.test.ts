import { describe, expect, it } from 'vitest'
import { formatBdt, formatHours, formatInteger, formatKg, formatKw, formatKwh, formatLitres, formatPercent } from './formatting'

describe('shared display formatting', () => {
  it.each([
    [formatBdt, 15988.172699999996, '15,988'],
    [formatBdt, 2075147.8029, '2,075,148'],
    [formatBdt, 153157.6373, '153,158'],
    [formatKg, 8388.1019, '8,388'],
    [formatKg, 9111.5024, '9,112'],
    [formatLitres, 20761.1569, '20,761'],
    [formatKwh, 1234.6, '1,235'],
    [formatInteger, 1234, '1,234'],
  ])('rounds %s(%s) to whole units: %s', (format, value, expected) => {
    expect(format(value)).toBe(expected)
  })

  it.each([
    [formatHours, 54.25, '54.3'],
    [formatHours, -54.25, '-54.3'],
    [formatHours, 56, '56.0'],
    [formatKw, 12.1234, '12.1'],
    [formatPercent, 4.6789, '4.7'],
    [formatPercent, 0.05, '0.1'],
    [formatPercent, -0.05, '-0.1'],
    [formatPercent, 0, '0.0'],
  ])('rounds %s(%s) to one decimal, with ties away from zero: %s', (format, value, expected) => {
    expect(format(value)).toBe(expected)
  })
})
