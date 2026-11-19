// The intro splash lives in index.html so it paints before the JavaScript bundle loads. Once React has
// rendered, keep it on screen for a short minimum (so the intro animation can finish) and then zoom it out.
const MIN_VISIBLE_MS = 1700
const EXIT_MS = 700

export function hideSplash() {
  const splash = document.getElementById('splash')
  if (!splash) return
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  const wait = reduced ? 0 : Math.max(0, MIN_VISIBLE_MS - performance.now())
  window.setTimeout(() => {
    splash.classList.add('splash-out')
    document.documentElement.classList.remove('splash-active')
    window.setTimeout(() => splash.remove(), reduced ? 0 : EXIT_MS)
  }, wait)
}
