import { expect, it } from 'vitest'
import { groupCaveats } from './caveats'
import { caveats } from '../test/backtestFixture'

it('groups the real twelve API caveats once each in the approved order', () => {
  const groups = groupCaveats(caveats)
  expect(groups.map(({ title, items }) => [title, items.length])).toEqual([
    ['The data is synthetic or simulated', 3],
    ['The forecast and test setup', 3],
    ['How the comparison was built', 4],
    ['What these numbers cannot show', 2],
  ])
  expect(groups.map(({ items }) => items)).toEqual([
    [caveats[1], caveats[2], caveats[3]],
    [caveats[0], caveats[4], caveats[5]],
    [caveats[6], caveats[7], caveats[8], caveats[10]],
    [caveats[9], caveats[11]],
  ])
  expect(groups.reduce((total, { items }) => total + items.length, 0)).toBe(caveats.length)
})

it('keeps an unknown thirteenth caveat verbatim in Other limits', () => {
  const additional = 'A future API limit with no known keywords.'
  const input = [...caveats, additional]
  const groups = groupCaveats(input)
  expect(groups.at(-1)).toEqual({ title: 'Other limits', items: [additional] })
  expect(groups.reduce((total, { items }) => total + items.length, 0)).toBe(input.length)
})

it('matches case-insensitively, uses the first matching group, and anchors EV at the start', () => {
  expect(groupCaveats(['SIMULATED pv and HVAC', 'ev: vans', 'New EV: limit'])).toEqual([
    { title: 'The data is synthetic or simulated', items: ['SIMULATED pv and HVAC'] },
    { title: 'How the comparison was built', items: ['ev: vans'] },
    { title: 'Other limits', items: ['New EV: limit'] },
  ])
})
