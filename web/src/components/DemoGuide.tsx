import { useEffect, useRef } from 'react'
import { useReducedMotion } from '../hooks/useReducedMotion'

const steps = [
  { title: 'Problem', text: 'One Dhaka factory faces peak tariffs and load-shedding, with rooftop solar, a battery and diesel backup.', target: '.honesty-strip' },
  { title: 'Side by side', text: 'Compare the baseline with Gridpilot AI on the same historical day.', target: '.same-day-card' },
  { title: 'Slider', text: 'Try moving Cost vs carbon and watch the schedule and API totals change.', target: '.weight-control input' },
  { title: 'Cloud event', text: '13:00 · 60 % solar drop · 2 hours. Press Trigger cloud event to simulate and check Risk.', target: '#cloud-event-panel' },
  { title: 'Ask why', text: 'The first suggested question is ready in chat. Review it, then press Send when you choose.', target: '.chat-panel textarea' },
  { title: 'Proof and business', text: 'Review the backtest and its limits. Discuss SaaS per site via installers and the next pilot; pricing and measured savings are not established here.', target: '.backtest-caveats h2' },
]

export default function DemoGuide({ step, onStep, onClose, onPareto }: { step: number; onStep: (step: number) => void; onClose: () => void; onPareto: () => void }) {
  const guide = useRef<HTMLElement>(null)
  const reducedMotion = useReducedMotion()
  useEffect(() => {
    const element = guide.current
    if (!element) return
    const root = document.documentElement
    const previous = root.style.getPropertyValue('--demo-guide-height')
    const measure = () => root.style.setProperty('--demo-guide-height', `${element.getBoundingClientRect().height}px`)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => {
      observer.disconnect()
      if (previous) root.style.setProperty('--demo-guide-height', previous)
      else root.style.removeProperty('--demo-guide-height')
    }
  }, [])
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); onClose() } }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [onClose])
  useEffect(() => {
    let done = false
    const point = () => {
      const target = document.querySelector<HTMLElement>(steps[step].target)
      if (!target || target.closest('[hidden]') || done) return
      done = true
      target.scrollIntoView?.({ behavior: reducedMotion ? 'instant' : 'smooth', block: 'center' })
      if (step === 2 || step === 4) target.focus({ preventScroll: true })
    }
    point()
    // The same-day comparison and caveats may arrive after their API response.
    const observer = new MutationObserver(point)
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] })
    return () => observer.disconnect()
  }, [step, reducedMotion])
  return <section ref={guide} className={`demo-guide${step === 4 ? ' demo-chat-step' : ''}`} role="region" aria-label="Guided demo">
    <div><p className="demo-title" aria-live="polite" aria-atomic="true">{step + 1} / {steps.length} · {steps[step].title}</p><p className="demo-description">{steps[step].text}</p></div>
    <div className="demo-actions">
      {step === 2 && <button type="button" onClick={onPareto}>See Pareto frontier</button>}
      <button type="button" disabled={step === 0} onClick={() => onStep(step - 1)}>Back</button>
      <button type="button" disabled={step === steps.length - 1} onClick={() => onStep(step + 1)}>Next</button>
      <button type="button" onClick={onClose}>Close</button>
    </div>
  </section>
}
