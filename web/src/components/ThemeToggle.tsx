import { useEffect, useRef, useState } from 'react'

type Theme = 'light' | 'dark'

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem('eb-theme')
    return value === 'light' || value === 'dark' ? value : null
  } catch { return null }
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#07111F' : '#F4F7FB')
}

export default function ThemeToggle() {
  const preference = useRef(storedTheme())
  const [theme, setTheme] = useState<Theme>(() => preference.current ?? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => { applyTheme(theme) }, [theme])

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const sync = () => {
      if (!preference.current) setTheme(media.matches ? 'dark' : 'light')
    }
    media.addEventListener('change', sync)
    return () => {
      media.removeEventListener('change', sync)
      clearTimeout(timer.current)
      document.documentElement.classList.remove('theme-changing')
    }
  }, [])

  function toggle() {
    const next = theme === 'dark' ? 'light' : 'dark'
    preference.current = next
    try { localStorage.setItem('eb-theme', next) } catch { /* Keep the choice for this page when storage is blocked. */ }
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      document.documentElement.classList.add('theme-changing')
      clearTimeout(timer.current)
      timer.current = setTimeout(() => document.documentElement.classList.remove('theme-changing'), 250)
    }
    applyTheme(next)
    setTheme(next)
  }

  return <button type="button" className="theme-toggle" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} aria-pressed={theme === 'dark'} onClick={toggle}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <g className="theme-sun"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" /></g>
      <path className="theme-moon" d="M20.8 13A9 9 0 0 1 11 3.2 9 9 0 1 0 20.8 13Z" />
    </svg>
  </button>
}
