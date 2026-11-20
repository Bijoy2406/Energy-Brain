# Add tasteful motion effects (aurora header, count-up numbers, card tilt, staggered entrance) with reduced-motion and WebGL fallbacks

- **Used with:** Codex CLI (gpt-6.1-sol, medium effort)
- **Outcome:** Merged in PR #67. Uses React Bits components copied from its public registry.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Add tasteful 3D / motion "wow" effects to the Gridpilot AI web UI (web/ only). Do NOT commit, push or touch git. Keep the Navy + solar palette (navy #0B2545, blue #2F6FDB, amber #F5A623, teal #14A38B, orange #C2410C). The React Bits components are already downloaded in web/src/fx/ (Lightning, Aurora, TiltedCard, ShinyText, CountUp .tsx/.css; npm deps motion and ogl are installed). Fix their TS/lint errors without changing behaviour; adapt, don't rewrite.

Effects to add:
1. Header hero: an Aurora (or Lightning, whichever looks better on navy; energy theme) WebGL background behind the header content, colours from the palette, low opacity so header text keeps WCAG AA contrast. Pointer-events none.
2. Brand title "Gridpilot AI" with a subtle ShinyText sheen.
3. KPI numbers in the daily overview / summary cards (cost, CO2, savings, etc. — find them in App.tsx and the dashboard components) animate with CountUp when scrolled into view. Must display the exact same final formatted numbers as now (same decimals, units, thousands separators); existing tests asserting those numbers must still pass.
4. 3D tilt (perspective, TiltedCard style or a lighter custom CSS/JS tilt) on the KPI/summary cards on hover, with a soft light glare. Content must stay selectable and keyboard accessible; no layout shift.
5. Staggered fade/slide-in on tab content and cards (motion), and a gentle animated energy-flow accent (e.g. a moving amber-to-teal line under the header or tab bar).

Hard rules:
- Respect prefers-reduced-motion: no WebGL, no tilt, no count-up (render final values immediately, static header gradient).
- WebGL must be lazy-loaded (React.lazy / dynamic import) and guarded: if WebGL/ogl is unavailable (jsdom tests, old devices) render a static navy gradient fallback with no console errors. Pause the render loop when the tab is hidden or the header is off-screen.
- Mobile: disable tilt on touch/coarse pointers, cap devicePixelRatio at 1.5 for WebGL.
- Do not change copy, data, honesty strip/caveats, layout, or chart colours. Do not hide any content behind animation (content must be visible without JS animation finishing; tests must not need to wait on animations).
- Keep the main bundle growth small: ogl/Aurora in a separate lazy chunk. Report the build size before/after (npm run build).
- Add/adjust tests so vitest stays green (mock matchMedia/IntersectionObserver in web/src/test setup if needed; no flaky timing).
Finish by running in web/: npm run lint, npx tsc -b, npx vitest run, npm run build. Report changes and results.
