// Display only: Intl's default rounding is nearest, with ties away from zero.
const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

export const formatInteger = (value: number) => integer.format(value)
export const formatBdt = formatInteger
export const formatKg = formatInteger
export const formatKwh = formatInteger
export const formatLitres = formatInteger
export const formatHours = (value: number) => decimal.format(value)
export const formatKw = (value: number) => decimal.format(value)
export const formatPercent = (value: number) => decimal.format(value)
