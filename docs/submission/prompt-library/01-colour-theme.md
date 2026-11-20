# Recolour the UI to a navy and solar energy palette

- **Used with:** Codex CLI (gpt-6.1-sol, medium effort)
- **Outcome:** Merged in PR #67. Replaced the crimson/brown theme; chart series kept colour-blind safe.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Recolour the Energy Brain web UI (web/ only) from the crimson/brown theme to the "Navy + solar" energy palette. Do NOT commit, push or touch git. Change colours only: no layout, copy, font or feature changes.

Palette:
- navy #0B2545 (header, footer, headings/primary text, replaces obsidian/mahogany/crimson as the brand)
- blue #2F6FDB (main accent: --accent, active tab, links, buttons, focus; also the Grid series)
- amber #F5A623 (Solar series, peak-tariff shade --peak-shade, small highlights)
- teal #14A38B (Battery series, SoC line)
- burnt orange #C2410C (Diesel generator series, outage markers --backtest-outage, --backtest-other a lighter/related tone)
- canvas white, page background a very light grey-blue (e.g. #F4F7FB), subtle surfaces #F8FAFC/#EEF3F9, text primary navy, secondary #3B4A63, muted #64748B, lines rgba(11,37,69,0.09/0.16). Shadows use rgba(11,37,69,...) instead of rgba(34,9,1,...).

Files: web/src/App.css (variables at top, plus every hard-coded old colour/rgba further down; keep variable names where possible, rename the --c-* core ones sensibly), web/src/App.tsx (footer swatches and any inline colours: use navy/blue/amber/teal/orange), web/src/chart/ScheduleCharts.tsx (series: solar #F5A623, battery #14A38B, grid #2F6FDB, generator #C2410C, SoC line teal; update the colour-blind comment), web/index.html (theme-color #0B2545), BacktestChart/TradeoffChart only if they hard-code colours. The logos in web/public stay as they are, but if the header logo is unreadable on navy keep it inside a white rounded chip.

Rules: header/footer text must keep WCAG AA contrast on navy; the four chart series must stay colour-blind distinguishable (do not change those four hexes); no leftover crimson/brown (#220901 #621708 #941b0c #bc3908 #f6aa1c and warm-brown rgba) anywhere in web/src or web/index.html; update any tests that assert old colours. Finish by running in web/: npm run lint (if present), npx tsc -b, npx vitest run. Report what you changed and the results.
