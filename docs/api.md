# Energy Brain API (issue #21)

REST API for the React dashboard (#27–#33). The machine-readable contract is
[`docs/openapi.json`](openapi.json), so you can build the frontend without running the server. Interactive docs are
at `/docs` while the server runs. The code is in [`src/energy_brain/api/`](../src/energy_brain/api/).

> The optimiser decides every number in a plan. The LLM Explainer only explains, using numbers it got from tool
> outputs. Never show a plan with `safe: false` as runnable, and never show a backtest number without its caveats.

## Run

```bash
pip install -r requirements.txt                          # once
uvicorn energy_brain.api.app:app --reload --port 8000    # from the repo root
# open http://localhost:8000/docs
```

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `EB_CORS_ORIGINS` | – | extra allowed browser origins, comma-separated. `http://localhost:5173` and `http://127.0.0.1:5173` (Vite dev server) are always allowed |
| `EB_EXPLAIN_CACHE` | `fallback` | stored demo answers: `fallback` = only when the live LLM call fails; `prefer` = serve them first (demo day); `off` |
| `EB_WARMUP` | `1` | `0` = do not plan the demo days in the background at startup |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | | the Explainer's Groq settings (`.env`); read at call time and never returned or logged |

Without `data/processed` and `models/` (fresh clone, CI) the API still starts and works. The Forecast agent then
uses its fallback tiers (the committed one-week `data/sample`), the Signal agent generates the outage calendar in
memory, and every plan says so in `banners` and `forecast_fallback`. Real numbers need the 2022–2024 data and the
trained models (`docs/data-sources.md`).

## Optional built website

Set `EB_STATIC_DIR` to a directory containing `index.html` (for example `web/dist`) before creating the app
to serve the website at `/`, after all API routes. Unset or invalid directories leave API-only behavior unchanged.
Unknown non-API paths return `index.html` only for GET requests accepting `text/html`; API namespaces stay
reserved, and the OpenAPI schema is unchanged. See [deployment](deploy.md) for the single-container setup.

## Endpoints

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| GET | `/days` | – | `DaysResponse`: demo days + tomorrow |
| POST | `/plan` | `{"date": "YYYY-MM-DD", "w": 0..1}` | `PlanResponse` |
| POST | `/plan` | `{"date", "w", "overrides": [{"time", "battery_kw", "generator_kw"}]}` | `PlanResponse` with override impact |
| POST | `/simulate/cloud` | `{"date", "w", "time", "drop_pct", "duration_minutes": 120}` | before/after plans for a simulated cloud event |
| GET | `/pareto` | `?date=YYYY-MM-DD` | `ParetoResponse`: sampled cost/CO2 frontier |
| GET | `/backtest` | `?include_sha=false` | `BacktestSummary` |
| GET | `/backtest/days` | `?weight=<0.5\|1\|0>` (default: headline weight) | `BacktestDaysResponse` |
| POST | `/explain` | `{"date", "w", "question"}` | `ExplainResponse` |
| GET | `/health` | – | `HealthResponse` |
| GET | `/docs`, `/openapi.json` | – | FastAPI's interactive docs and schema |

All times are site time (Asia/Dhaka). Per-slot lists have `n_slots()` items (96 × 15 min). Units are in the field
names (`_kw`, `_kwh`, `_bdt`, `_kg`, `_l`, `_pct`). The agent shapes (`PlanResult`, `Forecast`, `SignalTable`,
`AssetConstraints`, `ExplainerAnswer`) are documented in [`docs/interfaces.md`](interfaces.md).

### `GET /days`

The days come from [`config/demo_days.yaml`](../config/demo_days.yaml), which the team can edit and the API
validates when it loads. The API adds **tomorrow**, which always uses a fallback forecast.

```json
{
  "first_day": "2022-01-01",
  "last_day": "2026-11-19",
  "days": [
    {"date": "2024-07-02", "weekday": "Tue", "workday": true, "weekly_off": false, "holiday": false,
     "outage": true, "outage_slots": 8, "outage_blocks": [{"start": "18:00", "end": "20:00"}],
     "label": "Demo day: evening outage 18:00-20:00, monsoon", "demo": true, "dynamic": false,
     "forecast_tier": "model", "forecast_tier_source": "planned", "signal_fallback": false, "in_backtest": true},
    {"date": "2026-11-19", "weekday": "Thu", "workday": true, "...": "...", "dynamic": true,
     "forecast_tier": "fallback", "signal_fallback": true, "in_backtest": false}
  ]
}
```

`forecast_tier` is `model` (trained models, tier 0) or `fallback`. Before a day has been planned it is
*predicted*: `model` only if `data/processed` and `models/` exist and the date is between 2022-01-08 and
2024-12-31. After planning it is the plan's actual tier (`forecast_tier_source: "planned"`).

### `POST /plan`

```bash
curl -s localhost:8000/plan -H 'Content-Type: application/json' -d '{"date": "2024-07-02", "w": 0.5}'
```

```json
{
  "date": "2024-07-02",
  "weight": 0.5,
  "weight_requested": 0.5,
  "safe": true,
  "risk_ok": true,
  "banners": [],
  "forecast_fallback": {"used": false, "note": null, "fields": null},
  "signal_fallback": {"used": false, "note": null, "fields": null},
  "forecast_source": "model",
  "plan":     {"start": "2024-07-02T00:00:00+06:00", "weight": 0.5, "schedule": {"...": "96 per slot"},
               "totals": {"cost_bdt": 152702.707, "co2_kg": 9092.852, "...": "..."}, "risk": {"...": "..."},
               "status": "optimal", "...": "..."},
  "forecast": {"start": "...", "solar": {"p10_kw": [], "p50_kw": [], "p90_kw": []}, "load": {"...": "..."},
               "fallback_used": false, "note": null},
  "signals":  {"tariff_bdt_per_kwh": [], "grid_co2_kg_per_kwh": [], "outage": [], "...": "..."},
  "assets":   {"battery": {"...": "..."}, "generator": {"...": "..."}, "...": "..."},
  "cached": false,
  "computed_at": "2026-11-18T16:20:01.123456+06:00"
}
```

- **Dates** run from 2022-01-01 to **tomorrow** (site time). Dates after 2024-12-31 get the analog-day fallback
  and banners.
- **Weight:** `w` in [0, 1], where 1 = cheapest and 0 = cleanest. It is **rounded to 0.01** before solving
  (`weight` is the value used, `weight_requested` the value sent). A slider can send any value, but debounce it
  anyway.
- **Chart data:**
  - the schedule is in `plan.schedule`;
  - the P10/P50/P90 bands are in `forecast.solar` / `forecast.load` (load = base load without HVAC and EV);
  - tariff and outage flags are in `signals`;
  - battery limits are in `assets.battery`;
  - per-slot cost and CO2 are in `plan.totals.slot_cost_bdt` / `slot_co2_kg`.
- **Always 200 for a plan, even an unsafe one.** `safe: false` means the planner found no schedule that meets
  every hard constraint. `banners[0]` then starts with `WARNING: this plan is NOT SAFE TO EXECUTE`, the schedule is
  diagnostic only, and it must not look runnable. `risk_ok: false` means the plan fails the Risk agent's P10 solar
  check. The reasons are in `plan.notes` and `plan.risk.reasons`.
- **Fallbacks:** `forecast_fallback` / `signal_fallback` carry the stable one-line `FALLBACK` note and its
  parsed fields (`docs/interfaces.md`). The banners contain no numbers and should be shown above the chart.
- **Errors:** 422 for an invalid body (see *Errors*). 503 `forecast_unavailable` when not even `data/sample` is
  present.
- **Speed:** the first plan after startup loads the data and models (about 1 s), a new (date, w) takes about
  0.1–0.3 s, and a cached one a few milliseconds (`cached: true`). The demo days at w = 0.5, 0 and 1 are planned
  in the background at startup.

### Operator overrides: `POST /plan`

```json
{"date": "2024-07-02", "w": 0.5,
 "overrides": [{"time": "13:00", "battery_kw": 100, "generator_kw": 300}]}
```

Each override fixes one slot on the 15-minute grid in site time. Supply at least one power:
`battery_kw` is signed (positive charges, negative discharges, zero idles); `generator_kw` is zero
(off) or between the site's minimum and rated power. Battery power must stay within the site's
charge/discharge ratings. Times must be unique, with at most 96 overrides. Invalid overrides return
422 `invalid_overrides` in the `ErrorBody` format, with a readable message.

The planner re-solves the day with those powers fixed. Grid import, solar dispatch and battery SoC
remain free and must obey balance, dynamics and every other hard constraint. Risk checks the result
and can ask for more reserve; every re-plan keeps the overrides. HVAC still uses its occupied power
minimum: there is no thermal model or comfort shifting.

The response appends `override`: `applied` echoes the decisions, `baseline` holds the normal plan's
full totals at the same date and rounded weight, and `delta` gives signed changes in `cost_bdt`,
`co2_kg`, `generator_hours` and `diesel_litres` (override minus baseline). `feasible` means the planner
met all hard constraints, including the overrides. Check `risk_ok` as well: a Risk-failing override
has `safe: false`, even when it is feasible at P50. An infeasible request returns 200 with
`override.feasible: false`, `safe: false` and diagnostic banners. Those banners name the fixed slots
and any battery reachability or unserved-demand diagnosis. A diagnostic schedule must not be run;
it may fail to meet the requested overrides when even the elastic diagnosis has no solution.

Without overrides (including null or an empty list), existing fields retain their normal meaning
and order, and `override` is null. Override plans use a separate bounded cache and never replace a
normal cached plan. The Explainer continues to read and explain only the normal plan; `/explain`
does not accept overrides.

### Simulated cloud event: `POST /simulate/cloud`

```json
{"date": "2024-07-02", "w": 0.5, "time": "13:00", "drop_pct": 60, "duration_minutes": 120}
```

This is a **simulated cloud event on a historical replay day**, applied to the solar forecast.
It is not a new observation or live weather feed. `time` must be HH:MM on the 15-minute site grid;
`drop_pct` is 1–100, and `duration_minutes` is 15–720 (default 120). Duration rounds up to whole
slots, then clips at midnight. The event echo retains the requested duration; `affected_slots`
reports the actual zero-based range as inclusive `start` and exclusive `end`.

The normal plan at the same date and rounded weight is `before`. The simulation reduces solar
P10, P50 and P90 equally in affected slots. All earlier battery charge/discharge, generator, EV
and HVAC decisions are frozen to the before-plan. SoC follows the original battery dynamics,
carrying the executed prefix into the re-plan. Planner and Risk then re-solve with these fixes
retained on every reserve retry. There is no HVAC thermal model and no pre-cooling claim.

The response contains `before` and `after` in the `PlanResponse` shape, `event`, `affected_slots`,
`delta` (the same four totals, after minus before), `replan_seconds`, `safe`, `risk_ok` and `banners`.
`replan_seconds` measures Planner/Risk orchestration only, excluding baseline work and solver-lock
waits. A cached response retains the original computation time; `after.cached` identifies reuse.
The demo target is below 10 seconds for 2024-07-02, w=0.5, 13:00, 60%, 120 minutes.
An infeasible event still returns 200 with an unsafe diagnostic after-plan and explanatory banners.
A Risk failure also makes the simulated after-plan's `safe` false. The original forecast and normal
plan cache are unchanged.

Both what-if paths use a separate shared LRU of 64 results and single-flight protection for matching
requests. Each path has its own limit of **6 new computations per minute per client IP and 30 overall**;
cache hits and concurrent followers are free. A limit returns 429 `rate_limited`, `retry_after_s` and
`Retry-After`. Both paths share the existing agent/HiGHS lock. They never hold the cache lock while
waiting for a solve. Invalid cloud bodies return 422 `invalid_cloud_event` using `ErrorBody`.

### `GET /pareto`

Compare planned cost and CO2 across the fixed weights `0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3,
0.4, 0.5, 0.75, 1`. These use the same plans as the slider and `/plan`. Dates have the same validation
and range as `/plan`: 2022-01-01 through tomorrow in Asia/Dhaka. No available forecast returns the same
503 `forecast_unavailable` body as `/plan`.

```bash
curl -s 'localhost:8000/pareto?date=2024-07-02'
```

Illustrative response, shortened to one point (the actual response always has eleven):

```json
{
  "date": "2024-07-02",
  "weights_meaning": "1 = cheapest, 0 = cleanest",
  "basis": "planned totals at the P50 forecast, before settlement against actuals",
  "points": [
    {"weight": 0.0, "cost_bdt": 152702.707, "co2_kg": 9092.852,
     "generator_hours": 2.0, "diesel_litres": 100.0, "status": "optimal",
     "safe": true, "risk_ok": true, "replans": 0, "efficient": true,
     "group": 0, "same_as_weight": null, "plan_cached": false}
  ],
  "frontier": [
    {"weight": 0.0, "cost_bdt": 152702.707, "co2_kg": 9092.852,
     "generator_hours": 2.0, "diesel_litres": 100.0, "status": "optimal",
     "safe": true, "risk_ok": true, "replans": 0, "efficient": true,
     "group": 0, "same_as_weight": null, "plan_cached": false}
  ],
  "forecast_fallback": {"used": false, "note": null, "fields": null},
  "signal_fallback": {"used": false, "note": null, "fields": null},
  "banners": [], "forecast_source": "model", "cached": false,
  "computed_at": "2026-11-19T16:20:01.123456+06:00", "solve_seconds": 1.2,
  "distinct_points": 1
}
```

All totals come unchanged from `PlanResult.totals`; they describe the P50 forecast plan before settlement
against actuals. On this site the grid CO2 factor is flat, so the frontier usually collapses to very few
distinct points. Eleven weights do not imply eleven different operating choices. This is the frontier
of the sampled weights, not an exhaustive enumeration of feasible schedules.

- `points` is in weight order. `safe` uses `planner.is_safe`; `risk_ok` and `replans` retain the Risk verdict
  and re-plan count. Unsafe or infeasible plans stay visible with `efficient: false`.
- A safe point is efficient when no safe point has both cost and CO2 less than or equal to it and at least
  one strictly smaller. Comparisons treat values as equal within relative tolerance `1e-6` (no absolute
  tolerance). Risk failure alone does not exclude an otherwise safe point.
- Groups are assigned in weight order, starting at 0, by comparing cost and CO2 with each group's first
  member using that tolerance. `same_as_weight` is that first member's weight, or null for the first member.
  `distinct_points` counts groups containing safe plans.
- `frontier` contains **full point objects**, one per efficient group, sorted by ascending cost. The
  representative is the lowest-weight efficient safe member; an unsafe member cannot represent a group.
  If no plan is safe, the frontier is empty and a banner says so.
- Fallback metadata and warning text use the same source as `/plan`; banners include warnings from every
  sampled plan, with duplicates removed. Show these warnings alongside the comparison.
- A separate LRU holds 64 finished days with single-flight protection. `cached` means the whole body came
  from that cache (including a concurrent computation). `computed_at` is the original completion time;
  `plan_cached` records plan-cache reuse during that original computation, and stays unchanged on a Pareto
  cache hit. `solve_seconds` measures this request's wall time, including waits and cache lookup.
- Uncached computations are limited to **6 per minute per client IP and 30 per minute overall**, using a
  separate instance of the existing sliding-window limiter. Cache hits and concurrent followers are free.
  A limit returns 429 `rate_limited`, `retry_after_s`, and `Retry-After`, like `/explain`.
- Startup warm-up computes Pareto results after the existing warm-up plans in the same background thread.
  `EB_WARMUP=0` disables both. Warm-up bypasses client rate limits; failures are logged and never stop the app.
  No new settings are required. `/health` retains its existing schema and plan-cache stats; it does not
  expose Pareto-cache stats. Warm-up `planned` and `total` still count the initial plans.

### `GET /backtest` and `GET /backtest/days`

These are compact views of [`reports/backtest.json`](../reports/backtest.json) (out-of-sample 2024, 366 days); the
2 MB file is never sent. **Show `caveats` with every number** (synthetic load, simulated PV, synthetic outages,
observed weather as forecast input, ...).

- `/backtest` returns `report`, `schema_version`, `caveats`, `headline` (`weight`, `baseline_names`, `lines`),
  `summary` (`days`, `by_weight`, `forecast`, exactly as in the report), `config` (provenance; the sha256 maps
  only with `?include_sha=true`) and `run`.
- `/backtest/days?weight=0.5` returns one row per day:

```json
{"date": "2024-01-01", "weekday": "Mon", "workday": true, "outage_slots": 6, "included": true,
 "excluded_reason": null, "forecast_fallback_used": false,
 "methods": {"optimiser":            {"adjusted_cost_bdt": 142418.5728, "adjusted_co2_kg": 8537.8295,
                                      "cost_bdt": 142418.5728, "co2_kg": 8537.8295},
             "baseline_solar_first": {"...": "..."}, "baseline_time_of_use": {"...": "..."}}}
```

The headline figures use the `adjusted_*` values. A weight that is not in the backtest (only 0.5, 1 and 0 were
run) gives 422 `weight_not_in_backtest`. If the report is missing, both endpoints return 404
`backtest_unavailable` (run `python -m energy_brain.evaluator`).

### `POST /explain`

```bash
curl -s localhost:8000/explain -H 'Content-Type: application/json' \
  -d '{"date": "2024-07-02", "w": 0.5, "question": "Why did you run the generator at 7 PM?"}'
```

The question is about the **same cached plan** that `/plan` returns for (date, w). The response is an
`ExplainerAnswer` (`docs/interfaces.md`, Explainer) plus `date`, `weight`, `weight_requested`, `cached`,
`cached_recorded_at`, `cached_model` and `live_error`:

```json
{
  "question": "What will today cost, and how much CO2?",
  "language": "en",
  "answer": "The plan’s total cost is 152 703 BDT and it will emit 9 093 kg CO₂. The plan is safe to run.",
  "verified": true, "refusal_reason": null, "error": null, "banners": [],
  "tool_calls": [{"name": "get_plan_summary", "arguments": {}, "result": {"...": "..."}}],
  "provenance": [{"text": "152 703 BDT", "kind": "number", "tool": "get_plan_summary",
                  "field": "totals.cost_bdt", "call": 0}],
  "unverified_numbers": [], "rounds": 2, "model": "openai/gpt-oss-120b",
  "prompt_version": "explainer_system v3 (2026-11-18)",
  "date": "2024-07-02", "weight": 0.5, "weight_requested": 0.5,
  "cached": true, "cached_recorded_at": "2026-11-18T16:32:20+06:00", "cached_model": "openai/gpt-oss-120b",
  "live_error": null
}
```

- **Outcomes** (all 200): a verified answer (`verified: true`, numbers in `provenance`), a refusal
  (`refusal_reason`: `missing_data`, `off_topic`, `change_request`, `unverified`, `language`), or an LLM problem
  (`error`: `no_key`, `bad_config`, `network`, `timeout`, `rate_limit`, `blocked_model`, `bad_tool_call`,
  `max_rounds`, `api_error`). In every case `answer` is a clear message to show.
- **Stored demo answers** (`cached: true`) cover the five verified demo questions for 2024-07-02 at w = 0.5. They
  were recorded live in [`reports/explainer_demo_answers.json`](../reports/explainer_demo_answers.json) by
  `python -m energy_brain.api record-demo-answers`. A stored answer is served only when **all** of these hold:
  1. same date and weight, and the question matches exactly after trimming and lower-casing;
  2. every tool call it was built from, re-run on the current plan, returns the identical result;
  3. its text passes the Explainer's traceability check against those results.

  Its banners are recomputed from the current plan. With `EB_EXPLAIN_CACHE=fallback` (default) a stored answer
  is used only when the live call fails, and `live_error` says why the live call failed. With `prefer` it is
  served first. Otherwise the live Explainer answers. The API never makes up an answer.
- **Limits:**
  - questions are 1–500 characters (422 otherwise);
  - 5 questions per minute per client IP and 30 per minute in total (429 `rate_limited` with `retry_after_s` and
    a `Retry-After` header; this protects the free Groq quota);
  - at most 2 LLM calls at once (503 `busy` after 30 s).
- **Speed:** a live answer takes about 2–10 s (Groq), a stored one milliseconds.

### `GET /health`

```json
{"status": "ok", "version": "0.1.0", "processed_data": true, "models": true, "sample_data": true,
 "backtest": true, "demo_answers": 5, "llm_key_configured": true, "llm_model": "openai/gpt-oss-120b",
 "explain_cache": "fallback",
 "cache": {"size": 24, "max_size": 256, "hits": 0, "misses": 24, "inflight": 0},
 "warmup": {"enabled": true, "state": "done", "planned": 24, "total": 24, "errors": 0}}
```

`llm_key_configured` is only true/false. The key itself never appears in any response or log.

## Errors

| Status | Body | When |
| --- | --- | --- |
| 422 | FastAPI's standard `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | normal body or query validation (bad date, date out of range, `w` outside [0, 1], empty or too-long question, unknown field); what-if validation uses ErrorBody |
| 404 / 422 / 429 / 503 / 500 | `{"detail": {"code": "...", "message": "...", "retry_after_s": null}}` | codes: `invalid_overrides`, `invalid_cloud_event`, `backtest_unavailable`, `weight_not_in_backtest`, `rate_limited`, `busy`, `forecast_unavailable`, `internal_error` |

Error bodies never contain a stack trace or a secret. Details of an internal error go to the server log only.

## Caching, concurrency and safety

- **Plan cache:** an LRU of 256 plans keyed by (date, w rounded to 0.01). It stores the whole planning context
  (plan, forecast, signals, assets), so `/plan` and `/explain` describe the same plan. Concurrent requests for
  the same key solve it once.
- **Threading:**
  - Endpoints are synchronous; FastAPI runs them in its thread pool, so a solve or an LLM call never blocks the
    event loop.
  - One lock serialises the agents and the solver, because the agents cache data lazily and HiGHS runs one
    solve at a time.
  - LLM calls run outside that lock.
- **CORS:** `GET` and `POST` from the allowed origins only, without credentials.
- **Rate-limit key:** the client IP as the server sees it. Behind a reverse proxy, start uvicorn with
  `--proxy-headers --forwarded-allow-ips=<proxy>`.

## Keeping the contract current

```bash
python -m energy_brain.api export-openapi            # rewrite docs/openapi.json after changing a model or route
python -m energy_brain.api export-openapi --check    # exit 1 if it is stale
```

`tests/test_api.py::test_exported_openapi_file_is_current` fails when `docs/openapi.json` is stale. To add an
endpoint (#22 cloud event, #23 Pareto, #25 override, #26 PDF report), add a module in `api/routers/`, list it in
`api/routers/__init__.py`, put the logic in the service, then re-export the schema and document it here.
