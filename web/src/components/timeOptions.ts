// All controls use the same 96-slot site-time grid, including midnight.
export const slotTimes = Array.from({ length: 96 }, (_, index) => `${String(Math.floor(index / 4)).padStart(2, '0')}:${String((index % 4) * 15).padStart(2, '0')}`)
