# Add a dark theme with a header toggle, using CSS variables only

- **Used with:** Codex CLI (gpt-6.1-sol, medium effort); stopped part-way and finished by hand
- **Outcome:** Merged in PR #68 (Codex produced the variable refactor and toggle; the rest was completed and checked manually).
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Add a dark mode with a theme toggle to the Gridpilot AI web UI (web/ only). Do NOT commit, push or touch git. There are uncommitted splash changes in web/index.html and web/src/splash.ts: keep them working.

Theme behaviour:
- Two themes: light (current) and dark. Default = the OS setting (prefers-color-scheme). Once the user picks one with the toggle, remember it in localStorage key 'eb-theme' ('light' | 'dark') and follow the OS again only if nothing is stored. Wrap all storage access in try/catch.
- No flash of the wrong theme: in web/index.html's existing inline <head> script (the one that sets window.__splashStart and handles 'eb-splash-seen'), also set document.documentElement.dataset.theme before first paint. Set <meta name="theme-color"> and CSS `color-scheme` to match.
- Implement with CSS custom properties: put every colour used in web/src/App.css, web/src/skeleton.css and component inline styles into tokens on :root (light) and redefine them under :root[data-theme="dark"]. Replace hard-coded hex/rgba values in App.css (about 110 of them) with tokens. Keep the light theme looking exactly as it does now.

Dark palette (Navy + solar, made for dark):
- page bg #07111F, surface/cards #0E1B2E, raised/subtle surface #13233A, borders rgba(159,179,207,0.14) / strong 0.24
- text primary #E6EDF7, secondary #A9B8CC, muted #7E90A8
- accent blue #5B8DEF (buttons/active tab/links; must reach 4.5:1 for text on dark), amber #F5A623, teal #2DBFA5, orange #E2622B, danger #F87171, success #34D399
- header/footer stay navy (#0B2545 or slightly darker #081A33) so the brand reads the same; the aurora header keeps working.
- warning/unsafe banners, the honesty strip, status badges, the chat popup (header, bubbles, chips, composer), skeleton shimmer, splash (already dark; fine), tooltips, select/inputs/sliders, tables, caveats panel and focus rings all get dark versions with WCAG AA contrast.
- Charts (web/src/chart/*.tsx, Recharts): axes, grid lines, tick labels, tooltip background/text, legend text, reference lines and outage/peak shading must read well on dark. Read colours from CSS variables (e.g. getComputedStyle or `var(--...)` in SVG props) so they switch live when the theme toggles. Keep the four series hues recognisable and colour-blind distinguishable in both themes (solar amber, battery teal, grid blue, generator orange); the dark variants may be slightly lighter. BacktestChart and TradeoffChart too.

Toggle:
- In the header's top bar next to the existing pills (header-meta in App.tsx), a compact round/pill button that matches the header style: sun icon in dark mode, moon icon in light mode (inline SVG, no emoji), smooth icon rotate/fade (off under prefers-reduced-motion). Accessible: <button aria-label="Switch to dark theme" / "Switch to light theme" aria-pressed>, visible focus ring, 44px touch target on phones. Must fit on phone widths without wrapping the header badly.
- Theme switching animates colours briefly (background/color transition ~200ms), disabled under reduced motion, and never animates on first load.

Tests: add tests for the toggle (default from matchMedia, stored preference wins, clicking flips data-theme and stores it, aria-label changes). Keep all existing tests green without weakening them.
Check desktop, tablet and phone widths in both themes for unreadable text or leftover light backgrounds (grep for leftover hard-coded colours afterwards and justify any that remain).
Finish by running in web/: npm run lint, npx tsc -b, npx vitest run, npm run build. Report changes and results, including any contrast checks you did.
