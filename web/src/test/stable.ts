import { waitFor } from '@testing-library/react'

/**
 * Returns an element only after the same node has been found, and stayed attached to the document, on several
 * consecutive polls. Charts (Recharts) can re-render a moment after they first appear, especially when the machine
 * is busy; acting on a node found too early means clicking or focusing something that was already replaced.
 */
export async function findStable<T extends Element>(find: () => T, polls = 4, interval = 40): Promise<T> {
  let last: T | null = null
  let streak = 0
  await waitFor(
    () => {
      const element = find()
      streak = element === last && element.isConnected ? streak + 1 : 0
      last = element
      if (streak < polls) throw new Error('element is not stable yet')
    },
    { interval, timeout: 8000 },
  )
  return last as unknown as T
}
