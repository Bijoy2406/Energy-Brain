# Frontend for issue #33: cloud-event panel, override panel, guided demo mode, mobile layout

- **Used with:** Codex CLI (gpt-6.1-sol, high effort)
- **Outcome:** Merged in PR #71.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Implement GitHub issue #33 in the Gridpilot AI web UI (web/ only): cloud-event view, operator override control, a guided demo flow, and a mobile check. Do NOT commit, push or touch git. No backend changes (the endpoints already exist; if you find a real backend bug, report it instead of fixing it).

Read first: web/src/App.tsx (tabs, PlanPanel, PlanSummary, state), web/src/api/client.ts and web/src/api/generated.ts (types for POST /simulate/cloud = CloudRequest/CloudResponse, POST /plan with `overrides` = SlotOverride and the `override` block in PlanResponse), web/src/components/TradeoffView.tsx and web/src/chart/* (Recharts chart style, colour variables, tooltip style), web/src/hooks/usePareto.ts (hook pattern with abort, rate-limit retry), web/src/components/ExplainerPanel.tsx, web/src/App.css (design tokens: light and dark themes via CSS variables; use ONLY variables, no hard-coded colours), web/src/skeleton.css, Gridpilot AI — PRD.md section 10 (3-minute demo script). Follow existing code style, accessibility patterns, test patterns (Vitest + RTL, retry-safe helpers in web/src/test/), and keep every existing test green without weakening it.

## 1. API client
Add `api.simulateCloud(body, signal)` and make `api.plan` accept `overrides` (types come from generated.ts). Map errors like the other calls (422 `invalid_overrides` message, rate_limited with retry_after_s).

## 2. Cloud event view (demo step 4)
A "Cloud event" panel on the Today's plan tab, below the schedule dashboard (collapsible, closed by default is fine; the demo flow below can open it):
- Controls: start time (select on the 15-minute grid, default 13:00), solar drop % (slider 10-100, default 60), duration (select 1 / 2 / 3 / 4 hours, default 2). Button "Trigger cloud event". Uses the currently selected day and weight.
- Result: a before/after comparison. Overlay chart of grid import + generator + battery SoC before vs after for the whole day (reuse the chart style; the affected window shaded; frozen prefix visually distinct from the re-planned part), and a small table of cost, CO2, generator hours, diesel litres with before, after and change (+/- with sign and the project's number formatting), plus `replan_seconds` shown as "Re-planned in X.X s".
- Always show the banner that this is a simulated what-if on a historical replay day, not live weather. If the after-plan is not safe or Risk is not ok, show the existing NOT SAFE treatment (same as PlanSummary), never hide it.
- States: loading (skeleton + status text), error (alert + retry honoring retry_after_s), cancelled on unmount or when a new request starts (AbortController).
- Stale handling: changing day or weight clears the result.

## 3. Override control
A "Operator overrides" panel on the plan tab:
- Add up to 8 override rows: time (15-minute select), battery kW (signed number input with the label "+ charge / - discharge", bounds from the plan response assets), generator kW (0 = off, otherwise within min..rated from assets). Remove row button. Client-side validation mirroring the server rules, but always show server errors too.
- Button "Re-solve with overrides" sends POST /plan with `overrides`. Show the result with the existing PlanSummary/charts for the overridden plan (clearly labelled "Plan with your overrides") and an impact box: baseline vs override totals and the delta (cost, CO2, generator hours, diesel), `feasible` flag. If infeasible or not safe, show the NOT SAFE alert and the server's banners; label cost deltas of an unsafe plan as diagnostic only and do not present them as savings.
- Button "Back to the optimiser's plan" clears overrides.
- The Explainer keeps working on the default plan (do not point it at the override plan); say so in one muted line near the panel.

## 3b. Pareto in the demo
The Pareto frontier chart already exists on the Trade-off tab: do not rebuild it. Make sure the demo flow can jump to it.

## 4. Guided demo flow (PRD section 10 script)
Add a "Demo mode" toggle in the header area (next to the existing pills, matching their style) or as a button in the honesty strip. When on, a compact, dismissible stepper bar (sticky under the header on desktop; bottom sheet-like card above the chat launcher on phones, never covering the launcher) walks through:
1. Problem (one sentence about the Dhaka site; points at the honesty strip),
2. Side by side (scrolls to the baseline vs Gridpilot AI comparison table),
3. Slider (focuses the weight slider; suggest moving it),
4. Cloud event (opens the Cloud event panel with the demo values 13:00 / 60 % / 2 h and a "Trigger" hint),
5. Ask why (opens the chat widget with the first suggested question pre-filled but NOT auto-sent),
6. Proof and business (opens the Backtest tab and scrolls to the limits/caveats).
Next / Back / Close buttons, keyboard accessible, aria-live announcement of the step title, Esc closes, remembers nothing (no storage). It must select the demo day (2024-07-02) and w = 0.5 on step 1 if they exist in /days. Respect prefers-reduced-motion (no smooth scrolling then).

## 5. Mobile and responsive
Everything above must work at 360, 390, 768 and 1280 px width in both themes: no horizontal page scroll, tap targets at least 44 px, the demo stepper and chat launcher never overlap each other, tables scroll inside their own container. Verify by reading the CSS and, if a browser is available to you, by checking the widths; otherwise say clearly that visual checks were not possible.

## Honesty rules (unchanged project rules)
- Always label simulated/override results as such; no HVAC pre-cooling or comfort claims; never show an unsafe plan as safe; keep the synthetic-data strip and caveats visible; numbers come from the API only (no client-side invented totals; differences shown are API deltas or simple subtraction of two API totals, formatted with the existing formatters).

## Tests
Add Vitest tests: cloud event (success, unsafe result, error with retry, abort on day change), overrides (validation, success with delta, infeasible shown as NOT SAFE, clear), demo mode (steps, keyboard, Esc, pre-filled question not sent, reduced motion). Mock fetch with the helpers used in App.test.tsx.

Finish by running in web/: `npm run lint`, `npx tsc -b`, `npx vitest run`, `npm run build`. Report what you changed, which parts you could not check visually, and the results.
