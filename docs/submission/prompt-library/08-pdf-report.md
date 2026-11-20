# Issue #26: monthly savings and CO2 report as a PDF, with honest labelling

- **Used with:** Codex CLI (gpt-6.1-sol, high effort)
- **Outcome:** Merged in PR #71. Numbers were recomputed independently and match the backtest report.
- **Written by:** Bijoy (orchestrator role: the lead wrote each prompt, checked the result himself, and approved every commit)
- **Proprietary:** No (suggested; repository is public)

The prompt exactly as given to the coding agent:

---

Implement GitHub issue #26 in the Gridpilot AI repo: a downloadable monthly savings and CO2 report as a PDF for the owner / CFO (PRD FR-13). Backend first, plus one small download control in the web UI. Do NOT commit, push or touch git.

Read first: reports/backtest.json (structure: `report`, `caveats`, `headline`, `summary.by_weight`, `config`, `days[].methods[]` with `method` = optimiser / baseline_solar_first / baseline_time_of_use, `adjusted_cost_bdt`, `adjusted_co2_kg`, `generator_hours`, `diesel_litres`, `unserved_kwh`, `included`, `outage_slots`), src/energy_brain/api/backtest_view.py, api/routers/backtest.py (error style `api_error`, 404 `backtest_unavailable`), api/service.py (`service.backtest()`), api/app.py (router registration, reserved routes in api/static.py), api/models.py, config/ (tariff, emission and diesel values: use the project's existing constants, do not retype them), docs/api.md, tests/test_api.py, web/src/components/BacktestView.tsx and web/src/api/client.ts.

## Endpoint
`GET /report?month=YYYY-MM&weight=0.5` returns `application/pdf` with `Content-Disposition: attachment; filename="energy-brain-report-2024-07.pdf"`.
- `month`: a month inside the backtest range (2024-01 .. 2024-12 from `config.first_day/last_day`). Missing month = the whole backtest period ("2024 full year"). Invalid or out-of-range month: 422 with code `month_not_in_backtest` and the available range in the message. Malformed value: 422 via validation.
- `weight`: default the headline weight; must be one of the weights in the backtest (reuse `WeightNotInBacktest` handling and error code `weight_not_in_backtest`).
- No backtest file: 404 `backtest_unavailable`, same as the other backtest routes.
- Rate-limit it lightly with the existing RateLimiter pattern, and cache the rendered bytes per (month, weight) in a small LRU, because the report is deterministic.
- The report must be byte-for-byte reproducible for the same inputs: do not embed a "generated at" wall-clock time or random ids; use the backtest's own run metadata or no timestamp.

## PDF content (A4, 2-3 pages max)
Use a small pure-Python library: add `fpdf2` (or `reportlab` if you find fpdf2 unsuitable) to `pyproject.toml` dependencies and requirements.txt if that file lists runtime dependencies. Use only built-in fonts, so keep all text ASCII/Latin-1 safe: write "BDT" (not the taka sign), "CO2", "-" for dashes, no emoji.
Page 1:
- Title "Gridpilot AI: savings and CO2 report", period (month or full year), optimiser weight and what it means (1 = cheapest, 0 = cleanest), and a clearly visible box "Historical replay on synthetic data" stating: synthetic load, simulated PV, synthetic outages, 2024 out-of-sample days, not a live deployment and not a guarantee of future savings.
- Headline table for the period, for each baseline (solar-first / charge-on-arrival rule and simple time-of-use rule, use the names from `headline.baseline_names`): optimiser cost, baseline cost, savings in BDT and in percent; optimiser CO2, baseline CO2, change in kg and percent. Signed numbers: a CO2 increase must show as an increase (at w = 0.5 the optimiser's CO2 is slightly HIGHER than the solar-first baseline in the full-year headline; the report must say so plainly, never hide or round it away).
- Also: days included (and excluded with reason if any), days with an outage, generator hours and diesel litres for the optimiser vs each baseline, unserved energy (must be 0, show it).
Page 2:
- A simple bar chart of savings per day or per week drawn with PDF rectangles (no matplotlib dependency), with axis labels and a zero line, plus the best and worst day of the period.
- "How to read these numbers": cost and CO2 are ADJUSTED (an end-of-day battery shortfall is charged), baselines are rule-based and use actual solar and load (perfect information), HVAC runs at its occupied minimum in every method (no pre-cooling modelled), grid CO2 factor is flat (no hourly grid carbon data), emission factors and tariff: take the values from the project config and print them with their sources exactly as the config/docs cite them (BERC MT-3 tariff bands, grid factor kg/kWh, diesel litres price and kg CO2 per litre).
Page 3 (if needed): all caveats from `caveats` in the report, verbatim, and the data/model provenance fields that exist in `config` (names and hashes shortened to the first 12 chars).
Every number in the PDF must be computed from reports/backtest.json day rows with simple sums (or read from `summary.by_weight` when the period is the full year), and a test must assert the full-year PDF figures equal the headline figures in the report, to the displayed precision.

## Web
In the Backtest tab (BacktestView) add a small "Download report (PDF)" control: month select (Full year + 2024-01..2024-12) and a button; it is a plain link/button that downloads via the API URL (add `api.reportUrl(month, weight)` or a download helper in client.ts that respects the base URL; for errors show the API error message in an alert). Accessible labels, works in light and dark themes (CSS variables only), no layout break at 360 px. Keep the change small: another job may be editing App.tsx, so touch only BacktestView.tsx, client.ts, a new small component, CSS for it, and its tests.

## Docs and types
Regenerate docs/openapi.json (`.venv-linux/bin/python -m energy_brain.api export-openapi`) and web/src/api/generated.ts (`cd web && npm run gen:api`). Document the endpoint and its limits in docs/api.md in the same plain style. Add the reserved route name `report` to the reserved roots in api/static.py (and `simulate`, `pareto` if they are missing there).

## Tests
- API: PDF response headers and `%PDF` magic, month filtering, whole-year numbers equal the report headline, invalid month and weight 422, missing backtest 404, rate limit, deterministic bytes for repeated calls.
- A text-extraction check of the PDF content (for example with `pypdf` as a dev dependency, or by testing the report-data function that feeds the renderer): the baseline names, the synthetic-data box, the signed CO2 change and the caveats are present.
- Web: the download control (month options, error display).

Finish by running from the repo root: `.venv-linux/bin/python -m ruff check .`, `.venv-linux/bin/python -m pytest -q`, and in web/: `npx tsc -b`, `npx vitest run`, `npm run lint`. Use the project venv at .venv-linux. Report what you changed, the results, and show the extracted text of the generated July 2024 report page 1 so I can check the numbers.
