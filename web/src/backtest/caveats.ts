const groupRules = [
  { title: 'The data is synthetic or simulated', pattern: /synthetic load|simulated PV|synthetic outages/i },
  { title: 'The forecast and test setup', pattern: /out-of-sample|observed weather as forecast input|near-perfect/i },
  { title: 'How the comparison was built', pattern: /baselines are built on actual|^EV:|ADJUSTED|settlement reuses/i },
  { title: 'What these numbers cannot show', pattern: /HVAC|grid CO2 factor is flat/i },
]

export function groupCaveats(caveats: string[]) {
  const groups = [...groupRules, { title: 'Other limits' }].map(({ title }) => ({ title, items: [] as string[] }))
  for (const caveat of caveats) {
    const match = groupRules.findIndex(({ pattern }) => pattern.test(caveat))
    groups[match === -1 ? groupRules.length : match].items.push(caveat)
  }
  return groups.filter(({ items }) => items.length > 0)
}
