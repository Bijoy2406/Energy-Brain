import { act, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

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

/**
 * Focuses an element, presses a key, and waits for the expected result; if the result does not show up (the chart
 * was re-created between the focus and the key press, so the key went nowhere), finds the element again and retries.
 * Only for interactions where a lost key press has no effect, so a retry cannot overshoot.
 */
export async function focusAndPress(
  find: () => HTMLElement,
  key: string,
  expectation: () => void,
  attempts = 4,
): Promise<void> {
  for (let attempt = 1; attempt <= attempts; attempt++) {
    const element = await findStable(find)
    act(() => element.focus())
    await userEvent.keyboard(key)
    try {
      await waitFor(expectation, { timeout: 1500 })
      return
    } catch (error) {
      if (attempt === attempts) throw error
    }
  }
}

/**
 * Clicks an element, finding it again on every attempt, until the expected result shows up. A click on a chart dot
 * can be lost when the chart is re-created at that moment (slower machines). Only for clicks that are safe to
 * repeat (for example "select this plan"), so a retry cannot change the outcome.
 */
export async function clickUntil(find: () => Element, expectation: () => void, timeout = 8000): Promise<void> {
  await waitFor(
    () => {
      fireEvent.click(find())
      expectation()
    },
    { timeout, interval: 60 },
  )
}
