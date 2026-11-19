import { useEffect, type RefObject } from 'react'
import { animate } from 'motion/mini'
import { useReducedMotion } from './useReducedMotion'

const cards = '.summary-cards > div, .comparison-card, .forecast-metrics > div'

export function useDashboardMotion(root: RefObject<HTMLDivElement | null>, activeTab: string) {
  const reduced = useReducedMotion()
  useEffect(() => {
    const node = root.current
    if (!node || reduced) return
    const seen = new WeakSet<Element>()
    const animations = new Set<ReturnType<typeof animate>>()
    const reveal = () => {
      if (typeof HTMLElement.prototype.animate !== 'function') return
      const panel = node.querySelector('[role="tabpanel"]:not([hidden])')
      if (!panel) return
      const fresh = [panel, ...panel.querySelectorAll(`.panel, ${cards}`)].filter(el => !seen.has(el))
      fresh.forEach((element, index) => {
        seen.add(element)
        // Start visible. translate is independent of the hover tilt transform.
        const controls = animate(element as HTMLElement, { opacity: [0.86, 1], translate: ['0px 7px', '0px 0px'] }, {
          duration: 0.38, delay: Math.min(index * 0.045, 0.24), ease: 'easeOut',
        })
        animations.add(controls)
        void controls.finished.then(() => { animations.delete(controls) })
      })
    }
    reveal()
    const observer = new MutationObserver(reveal)
    observer.observe(node, { childList: true, subtree: true })

    let current: HTMLElement | null = null
    const reset = () => {
      current?.style.removeProperty('--tilt-x')
      current?.style.removeProperty('--tilt-y')
      current?.style.removeProperty('--glare-x')
      current?.style.removeProperty('--glare-y')
      current = null
    }
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !window.matchMedia('(hover: hover) and (pointer: fine)').matches
        || window.matchMedia('(any-pointer: coarse)').matches) { reset(); return }
      const card = event.target instanceof Element ? event.target.closest<HTMLElement>(cards) : null
      if (card !== current) reset()
      if (!card) return
      current = card
      const rect = card.getBoundingClientRect()
      const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)))
      const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / Math.max(1, rect.height)))
      card.style.setProperty('--tilt-x', `${(0.5 - y) * 5}deg`)
      card.style.setProperty('--tilt-y', `${(x - 0.5) * 5}deg`)
      card.style.setProperty('--glare-x', `${x * 100}%`)
      card.style.setProperty('--glare-y', `${y * 100}%`)
    }
    const leave = (event: PointerEvent) => {
      if (current && (!(event.relatedTarget instanceof Node) || !current.contains(event.relatedTarget))) reset()
    }
    node.addEventListener('pointermove', move)
    node.addEventListener('pointerout', leave)
    return () => {
      observer.disconnect()
      animations.forEach(animation => animation.stop())
      node.querySelectorAll<HTMLElement>('[role="tabpanel"], .panel, ' + cards).forEach(element => {
        element.style.removeProperty('translate')
        element.style.removeProperty('opacity')
      })
      node.removeEventListener('pointermove', move)
      node.removeEventListener('pointerout', leave)
      reset()
    }
  }, [root, activeTab, reduced])
}
