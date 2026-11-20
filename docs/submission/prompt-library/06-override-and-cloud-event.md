# Backend for issues #22 (simulated cloud event re-plan) and #25 (operator slot override)

- **Used with:** Codex CLI (gpt-6.1-sol, high effort)
- **Outcome:** Merged in PR #69. Re-plan of the demo day takes about 0.1 s.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Implement GitHub issues #22 (simulated cloud event, live re-plan) and #25 (operator override of a slot with re-solve) in the Energy Brain backend. Python only (src/energy_brain, tests, docs/openapi.json, web/src/api/generated.ts). Do NOT commit, push or touch git. No frontend UI work (that is #33, later).

Read first: src/energy_brain/planner/model.py (formulation docstring), planner/agent.py (MilpPlanner.plan, _infeasible elastic path), planner/checks.py (find_violations), orchestrator.py (run_plan + Risk re-plan loop), api/service.py (PlanService: plan cache, single-flight, HiGHS lock, RateLimiter), api/models.py, api/routers/plan.py, tests/test_planner.py, tests/test_orchestrator.py, tests/test_api.py. Follow the existing style: docstrings that state limits honestly, pydantic v2 models with Field descriptions, ruff clean.

## Shared mechanism: slot fixes in the planner (build this first)

Both issues need the MILP to accept fixed values for some slots.

- Add a small immutable type (e.g. `SlotFixes` in planner/ or schemas.py) holding, per slot index, optional fixed values for the controllable decisions: battery_charge_kw, battery_discharge_kw, generator_kw (and for the frozen-prefix case also ev_charge_kw and hvac_kw). Grid import, solar use/curtailment and SoC stay free and follow from the balance and SoC dynamics.
- `build_model(..., fixes=None)` applies them as equality constraints (or variable fixing). For generator, a fixed 0 means off; a fixed value > 0 must be within [min_kw, rated_kw] and forces gen_on = 1. A fixed charge > 0 forces mode = 1, a fixed discharge > 0 forces mode = 0.
- Thread `fixes` through `MilpPlanner.plan(...)`, the `PlannerAgent` protocol in interfaces.py, `_fallback`/`_infeasible` (the elastic diagnosis must keep the fixes so it explains why the fixed day is infeasible), and `orchestrator.run_plan(..., fixes=None)` so the Risk re-plan loop keeps the fixes on every re-plan.
- `find_violations` must still check every hard constraint. If fixes make the day infeasible, the existing infeasible path applies: return an unsafe plan with clear notes, never raise. Add a note naming the fixed slots that caused it when the elastic diagnosis points at them.
- No behaviour change when `fixes` is None: all existing tests must pass unchanged, and the default /plan results must be byte-identical.

## #25 Operator override (PRD FR-14)

- `PlanRequest` gets optional `overrides: list[SlotOverride]` (max 96). `SlotOverride`: `time` "HH:MM" on the 15-minute grid (site time; validate alignment) and at least one of:
  - `battery_kw`: signed kW, positive = charge, negative = discharge, 0 = idle; within -max_discharge_kw..+max_charge_kw.
  - `generator_kw`: 0 (off) or within [min_kw, rated_kw].
  Reject duplicates or out-of-range values with 422 and a clear message (use the existing ErrorBody style).
- With overrides, `/plan` solves through the orchestrator with those fixes, does NOT read or write the normal plan cache (keep the default-plan cache clean; a small separate LRU keyed by (date, w, overrides) is fine), and the response adds an `override` block:
  - echo of the applied overrides
  - `baseline` totals: the normal plan for the same (date, w) from the cache or solved
  - `delta`: cost_bdt, co2_kg, generator_hours, diesel_litres (override minus baseline)
  - `feasible`: bool. If false, explain in banners which override cannot be met and why (e.g. "battery cannot discharge 400 kW at 19:00: SoC floor reached").
- The response without `overrides` stays exactly as it is today (field order and content); `override` is null then.
- The Explainer keeps explaining the default plan only. Make sure override plans never enter the cache the Explainer reads.

## #22 Simulated cloud event (PRD demo step 4)

- New `POST /simulate/cloud` with body {date, w, time "HH:MM", drop_pct (1-100), duration_minutes (15-720, default 120, clipped at end of day)}.
- Before: the normal plan for (date, w) (cache or solve).
- Event: solar forecast in the affected slots is reduced by drop_pct for P10/P50/P90 alike. This is a what-if on the forecast, not new data: label it "simulated cloud event" everywhere.
- After: re-plan from the event slot onward. Slots before the event are frozen to the before-plan's decisions through `fixes` (they already happened), so SoC carries over exactly. Run it through the orchestrator so the Risk agent checks and, if needed, re-plans with more reserve, like today.
- Response: before and after `PlanResult` (or the same `PlanResponse` shape for each), event echo, affected slot range, `delta` totals (after minus before), `replan_seconds` (wall time of the re-plan only), `safe`/`risk_ok` of the after plan, banners. If the event makes the day infeasible (e.g. a drop during an outage), return 200 with the unsafe after-plan flagged honestly, as /plan does.
- Done-when from the issue: re-plan under 10 s and all hard constraints kept. Add a test that asserts the frozen prefix equals the before-plan, the after-plan passes find_violations, and that `replan_seconds` stays under 10 s on the demo day (2024-07-02, w = 0.5, event 13:00, 60 %, 120 min). Skip the timing assert only if CI marks it slow; do not loosen it silently.
- Rate-limit both new paths (override solves and /simulate/cloud) with the existing RateLimiter pattern (like pareto) and keep the HiGHS lock / single-flight rules in service.py: never hold the cache lock while solving.

## Honesty and limits (keep the project's rules)

- The cloud event is a simulation on a historical replay day. Say so in banners and docstrings.
- HVAC has no thermal model: do not claim pre-cooling or comfort shifting anywhere.
- Never present an infeasible or Risk-failing plan as safe.

## Docs and types

- Regenerate docs/openapi.json with `python -m energy_brain.api export-openapi`, then in web/ run `npm run gen:api` so web/src/api/generated.ts matches (types only; no UI changes). Make sure `npx tsc -b` and `npx vitest run` in web/ still pass.
- Update docs (README API section or docs/ where the endpoints are listed) with the two features and their limits, in the same plain style.

## Tests (pytest)

- Planner: a fixed battery slot and a fixed generator slot are honoured exactly; generator fix below min_kw is rejected; an impossible fix gives an unsafe plan with a note, not an exception; fixes=None gives the same result as before.
- Orchestrator: fixes survive Risk re-plans.
- API: /plan with overrides (impact block, 422 cases, cache untouched, response without overrides unchanged); /simulate/cloud (shape, frozen prefix, constraints, timing, rate limit, infeasible case).

Finish by running from the repo root: `ruff check .`, `pytest -q`, and in web/: `npx tsc -b`, `npx vitest run`. Report what you changed, the test results, and the measured re-plan time for the demo-day cloud event.
