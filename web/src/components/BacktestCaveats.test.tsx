import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import BacktestCaveats from './BacktestCaveats'
import { caveats } from '../test/backtestFixture'

it('expands and collapses all groups, including after individual toggles', async () => {
  render(<BacktestCaveats caveats={caveats} />)
  const panel = screen.getByRole('region', { name: 'Limits and methodology' })
  const groups = [...panel.querySelectorAll('details')]
  const summaries = [...panel.querySelectorAll('summary')]
  expect(summaries.map((summary) => summary.textContent)).toEqual([
    'The data is synthetic or simulated3 limits', 'The forecast and test setup3 limits',
    'How the comparison was built4 limits', 'What these numbers cannot show2 limits',
  ])
  groups.forEach((group) => expect(group.open).toBe(false))
  await userEvent.click(screen.getByRole('button', { name: 'Expand all' }))
  groups.forEach((group) => expect(group.open).toBe(true))
  for (const caveat of caveats) expect(within(panel).getByText(caveat)).toBeVisible()
  await userEvent.click(summaries[0])
  await waitFor(() => expect(screen.getByRole('button', { name: 'Expand all' })).toBeInTheDocument())
  await userEvent.click(screen.getByRole('button', { name: 'Expand all' }))
  groups.forEach((group) => expect(group.open).toBe(true))
  await userEvent.click(screen.getByRole('button', { name: 'Collapse all' }))
  groups.forEach((group) => expect(group.open).toBe(false))
})

it('lets keyboard users open and close every group', async () => {
  render(<BacktestCaveats caveats={caveats} />)
  const summaries = screen.getByRole('region', { name: 'Limits and methodology' }).querySelectorAll('summary')
  for (const summary of summaries) {
    summary.focus()
    await userEvent.keyboard('{Enter}')
    expect(summary.closest('details')).toHaveAttribute('open')
    await userEvent.keyboard(' ')
    expect(summary.closest('details')).not.toHaveAttribute('open')
  }
})

it('computes the count and displays unfamiliar API limits', async () => {
  const additional = 'A future API limit with no known keywords.'
  render(<BacktestCaveats caveats={[...caveats, additional]} />)
  expect(screen.getByText('13 limits in 5 groups')).toBeInTheDocument()
  const summary = screen.getByText('Other limits').closest('summary')!
  expect(summary).toHaveTextContent('1 limit')
  await userEvent.click(summary)
  expect(screen.getByText(additional)).toBeVisible()
})
