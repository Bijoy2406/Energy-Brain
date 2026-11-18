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

## Endpoints

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| GET | `/days` | – | `DaysResponse`: demo days + tomorrow |
| POST | `/plan` | `{"date": "YYYY-MM-DD", "w": 0..1}` | `PlanResponse` |
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
| 422 | FastAPI's standard `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | invalid body or query (bad date, date out of range, `w` outside [0, 1], empty or too-long question, unknown field) |
| 404 / 422 / 429 / 503 / 500 | `{"detail": {"code": "...", "message": "...", "retry_after_s": null}}` | codes: `backtest_unavailable`, `weight_not_in_backtest`, `rate_limited`, `busy`, `forecast_unavailable`, `internal_error` |

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
