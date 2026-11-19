// The intro splash lives in index.html so it paints before the JavaScript bundle loads. It plays once per browser
// tab: an inline script in index.html skips it when this tab has already seen it (sessionStorage is cleared when the
// tab closes). Once React has rendered, keep it on screen for a short minimum and then zoom it out.
// The tagline finishes fading in about 1.1 s after the splash paints; keep it readable for at least 1.5 s more.
const MIN_VISIBLE_MS = 2700
const EXIT_MS = 700
const SEEN_KEY = 'eb-splash-seen'

export function hideSplash() {
  const splash = document.getElementById('splash')
  if (!splash) return
  if (document.documentElement.classList.contains('splash-skip')) {
    splash.remove()
    return
  }
  try {
    sessionStorage.setItem(SEEN_KEY, '1')
  } catch {
    // Storage can be blocked (private mode, strict settings); the splash then simply plays on every load.
  }
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  const start = (window as Window & { __splashStart?: number }).__splashStart ?? 0
  const wait = Math.max(0, (reduced ? 1500 : MIN_VISIBLE_MS) - (performance.now() - start))
  window.setTimeout(() => {
    splash.classList.add('splash-out')
    document.documentElement.classList.remove('splash-active')
    window.setTimeout(() => splash.remove(), reduced ? 0 : EXIT_MS)
  }, wait)
}
