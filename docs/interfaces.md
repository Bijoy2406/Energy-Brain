# Agent interfaces (contract)

This is the contract every Energy Brain agent codes against (issue #13). The code is the source of truth:
[`schemas.py`](../src/energy_brain/schemas.py), [`interfaces.py`](../src/energy_brain/interfaces.py),
[`timeaxis.py`](../src/energy_brain/timeaxis.py), [`accounting.py`](../src/energy_brain/accounting.py),
[`orchestrator.py`](../src/energy_brain/orchestrator.py). Placeholders for development are in
[`stubs.py`](../src/energy_brain/stubs.py).

> **Tell the team before changing a field.** The data/forecast side (#5–#12, #14, #15) and the
> planner/risk/explainer/evaluator/API side (#16–#21) both depend on these names, units and shapes.

The optimiser decides every number in a plan. The LLM Explainer only reads them.

## 1. Time axis

| Item | Value |
| --- | --- |
| Slot length | `slot_minutes()` from `config/site.yaml` → `planning.slot_minutes` (15) |
| Slots per day | `n_slots()` = `horizon_hours * 60 / slot_minutes` (96). **Never hard-code 96.** |
| Slot length in hours | `slot_hours()` (0.25) |
| Day identifier | `start`: tz-aware `datetime` for slot 0, on a slot boundary, stored in **Asia/Dhaka** |
| Slot timestamps | `slot_times(start)` → `start + i * 15 min`, i = 0 … n_slots − 1 |
| Naive datetimes | rejected (`ValueError`). Aware datetimes in other zones are converted to Asia/Dhaka. |

## 2. Units and series

- Units are in field names: `_kw`, `_kwh`, `_bdt`, `_kg`, `_l`, `_pct`.
- `_kw` series hold the **average power over the slot**. Energy per slot = kW × `slot_hours()`.
- Every per-slot series is a plain JSON list (`list[float]` or `list[bool]`) of exactly `n_slots()` items.
- All numeric values are finite (no NaN/inf). Unknown fields are rejected. Models are frozen.
- Every model round-trips through `model_dump_json()` / `model_validate_json()`.

## 3. Schemas

### `QuantileSeries`

| Field | Type | Rule |
| --- | --- | --- |
| `p10_kw` | list[float] | ≥ 0, n_slots items |
| `p50_kw` | list[float] | ≥ 0, n_slots items |
| `p90_kw` | list[float] | ≥ 0, n_slots items |

Every slot must satisfy `p10 ≤ p50 ≤ p90`.

### `Forecast` (Forecast agent, #14)

| Field | Type | Meaning |
| --- | --- | --- |
| `start` | datetime | day identifier (slot 0) |
| `solar` | QuantileSeries | PV AC output, kW |
| `load` | QuantileSeries | **inflexible base load**, kW: site demand **excluding** HVAC and EV charging (the Planner schedules those) |
| `fallback_used` | bool = false | true if a persistence/backup forecast replaced the model |
| `note` | str or null | why the fallback was used, or other caveats |

### `SignalTable` (Signal agent, #15)

| Field | Type | Unit |
| --- | --- | --- |
| `start` | datetime | |
| `tariff_bdt_per_kwh` | list[float] ≥ 0 | BDT/kWh of grid import |
| `grid_co2_kg_per_kwh` | list[float] ≥ 0 | kg CO2 per kWh imported |
| `diesel_price_bdt_per_l` | list[float] ≥ 0 | BDT per litre |
| `diesel_co2_kg_per_l` | list[float] ≥ 0 | kg CO2 per litre burned |
| `outage` | list[bool] | **true = grid unavailable** in that slot (load-shedding) |

### `AssetConstraints` (Asset agents, #16)

`start`, plus one block per asset. Built from `config/site.yaml` by `energy_brain.assets.AssetAgent`
(one module per asset in `src/energy_brain/assets/`):

- Battery SoC limits: `capacity_kwh × soc_*_pct / 100`. Generator `min_kw = rated_kw × min_load_pct / 100`.
- HVAC `occupied[t]`: slot t overlaps any `load.shifts` window (a partial overlap counts), except on
  `load.weekend_days` and Bangladesh public holidays (`data.synthetic_load.is_bangladesh_holiday`), which are
  unoccupied.
- EV: fleet totals `vehicles × charger_kw` and `vehicles × battery_kwh × (required − arrival SoC) / 100`.
  `plugged_in[t]` is true for slots inside `[arrival_time, departure_time)` (wraps midnight). Arrival and
  departure times must be on slot boundaries, otherwise `ValueError`.
- Comfort band, EV departure energy and SoC limits are copied from config with no override parameter (hard).

| Block | Fields |
| --- | --- |
| `grid: GridConstraints` | `max_import_kw` (> 0). No export in the MVP. |
| `battery: BatteryConstraints` | `capacity_kwh`, `max_charge_kw`, `max_discharge_kw`, `round_trip_efficiency` (0–1], `soc_min_kwh` ≤ `initial_soc_kwh` ≤ `soc_max_kwh` ≤ `capacity_kwh` |
| `hvac: HvacConstraints` | `rated_kw`, `min_kw_when_occupied`, `occupied: list[bool]` per slot, `comfort_min_c`, `comfort_max_c`, `max_precool_hours` |
| `ev: EvConstraints` | `plugged_in: list[bool]` per slot, `charger_kw` (fleet total), `energy_required_kwh` (fleet total), `arrival_slot`, `departure_slot` (slot indices; window may wrap midnight) |
| `generator: GeneratorConstraints` | `rated_kw`, `min_kw` (≤ rated), `fuel_l_per_kwh` |

Battery convention: charge/discharge kW are at the AC side, `η = sqrt(round_trip_efficiency)`,
`soc[t] = soc[t-1] + η·charge[t]·h − discharge[t]·h/η`, `soc[-1] = initial_soc_kwh`.

### `Schedule` (Planner output)

All per-slot lists, kW, ≥ 0: `grid_import_kw`, `solar_used_kw`, `solar_curtailed_kw`, `battery_charge_kw`,
`battery_discharge_kw`, `hvac_kw`, `ev_charge_kw`, `generator_kw`, `base_load_kw`; plus `battery_soc_kwh`
(kWh at the **end** of each slot).

Energy balance, every slot:

```
solar_used + grid_import + battery_discharge + generator = base_load + hvac + ev_charge + battery_charge
```

`schedule.check_energy_balance(tol)` returns the max violation in kW and raises `ValueError` if it is above `tol`.

### `Totals` (from `compute_totals` only)

| Field | Unit |
| --- | --- |
| `cost_bdt`, `co2_kg` | day totals |
| `grid_kwh`, `solar_kwh` (used), `generator_kwh` | kWh |
| `generator_hours` | h with generator running |
| `diesel_litres` | L |
| `slot_cost_bdt`, `slot_co2_kg` | per-slot lists (for charts) |

Formula (`accounting.compute_totals`, the **only** place cost/CO2 is computed):

```
cost = Σ grid_import_kw·h·tariff  + Σ generator_kw·h·fuel_l_per_kwh·diesel_price
co2  = Σ grid_import_kw·h·grid_co2 + Σ generator_kw·h·fuel_l_per_kwh·diesel_co2
```

### `RiskReport` (Risk agent, #18)

| Field | Meaning |
| --- | --- |
| `ok` | false = plan leaves demand unserved under the stress scenario (default **P10 solar / P50 load**), or the plan is unsafe. A P90-load replay is advisory only |
| `reasons` | one stable line per finding (`RISK FAIL:` / `RISK NOTE:`, see Risk agent rules), parse with `energy_brain.risk.parse_reason` |
| `reserve_margin_kwh` | battery reserve the Planner should keep on the next re-plan |
| `replans` | re-plans run before this report (set by the orchestrator) |

### `PlanResult`

| Field | Meaning |
| --- | --- |
| `start` | day identifier |
| `weight` | w in [0, 1]; objective `w·cost + (1 − w)·CO2`; **1 = cheapest, 0 = cleanest** |
| `schedule`, `totals` | as above |
| `risk` | RiskReport (always filled by the orchestrator) |
| `status` | `optimal` / `feasible` / `infeasible` / `fallback` |
| `solve_seconds` | planning wall time (MilpPlanner: the whole `plan()` call) |
| `reserve_margin_kwh` | reserve this plan was solved with |
| `fallback_used` | true if any fallback (forecast or plan) was used |
| `notes` | list of messages for the operator / Explainer |

## 4. Protocols (`interfaces.py`)

| Protocol | Method |
| --- | --- |
| `ForecastAgent` | `run(start) -> Forecast` |
| `SignalAgent` | `run(start) -> SignalTable` |
| `AssetProvider` | `run(start) -> AssetConstraints` |
| `PlannerAgent` | `plan(forecast, signals, assets, weight, reserve_margin_kwh=0.0) -> PlanResult` |
| `RiskAgent` | `check(plan, forecast, signals, assets) -> RiskReport` |

The Planner may **not** relax: energy balance; grid limit and zero import during outages; battery SoC limits
(plus reserve margin) and power limits; HVAC limits, occupied minimum and comfort band; EV departure energy,
plug-in window and charger limit; generator off or within [min_kw, rated_kw]. It may not edit its inputs.

### Planner rules (#17, `energy_brain.planner.MilpPlanner`)

How the MILP turns the constraints above into per-slot rules (`planner/rules.py`, shared by the model, the
fallback and the checker `planner.find_violations`):

- **End-of-day battery:** `battery_soc_kwh[-1] >= initial_soc_kwh`. The battery must end the day at or above
  its starting charge, so savings never come from draining it. Risk and the Evaluator baselines should apply
  the same rule for a fair comparison.
- **Reserve margin:** the SoC floor in slot t is `max(soc_min, min(soc_min + reserve, soc_max,
  initial_soc + η·max_charge_kw·h·(t + 1)))`, i.e. the raised floor is reached as fast as full-power charging
  allows when it is above the starting charge. A large reserve can make a day infeasible (e.g. when the
  battery is needed to cover an outage).
- **EV across midnight:** `energy_required_kwh` is due in the plugged-in slots before `departure_slot`. If the
  window wraps midnight (`arrival_slot >= departure_slot`) those are the slots `[0, departure_slot)`; the
  evening slots from `arrival_slot` on allow charging (≤ `charger_kw`) but require nothing (that energy belongs
  to the next day's plan). Otherwise `[arrival_slot, departure_slot)`; `departure_slot = 0` means end of day.
- **HVAC:** `[min_kw_when_occupied, rated_kw]` when occupied; `[0, rated_kw]` up to `max_precool_hours` before
  an occupied slot; 0 otherwise. `min_kw_when_occupied` is the comfort stand-in: there is no thermal model in
  the contract, so the °C band is not modelled and pre-cooling never pays off.
- **Objective:** `w·cost/cost_ref + (1 − w)·co2/co2_ref`, where `cost_ref` / `co2_ref` are the totals of a
  rule-based reference schedule (no arbitrage, no shifting), floored at 1. w = 0.5 trades 1 % of cost against
  1 % of CO2.
- **Status:** `optimal`; `feasible` (time limit hit, best solution used); `fallback` (solver failed, the
  rule-based schedule passed every check); `infeasible` (no schedule meets every hard constraint).
- **Unsafe plans are machine-detectable:** `status == "infeasible"`, `fallback_used == True` and `notes[0]`
  starts with `"NOT SAFE TO EXECUTE"` (`planner.UNSAFE_PREFIX`; helper `planner.is_safe(plan)`). Its schedule is
  diagnostic only and may break the energy balance. The Explainer, Risk and API must refuse to act on it.
- `solve_seconds` is the wall time of the whole `plan()` call (build, solve, checks).

### Forecast agent rules (#14, `energy_brain.forecast.ModelForecastAgent`)

`ModelForecastAgent(weather_df=None, pv_df=None, load_df=None, models_dir=None, data_dir=None).run(start)`.
Frames passed in replace the files (tests, notebooks). Data is read lazily on the first `run` and cached, so
importing or constructing the agent never touches the git-ignored `data/processed/` or `models/`.

`Forecast.load` is the **inflexible base load** (`base_kw` of `load.parquet`): no HVAC, no EV.

| Tier | Used when | Solar P50 | Load P50 | `fallback_used` |
| --- | --- | --- | --- | --- |
| 0 Model | `start` is 00:00, all six `models/{solar,load}_p{10,50,90}.txt` exist, and weather + PV + load are complete for the target day **and** the 7 days before it (lag features) | `solar.predict` | `load.predict` | false, `note = null` |
| 1 Persistence | tier 0 blocked or failed and a recent day exists | same slot 1 day earlier (up to 7 days back) | same slot 7 days earlier (same weekday, up to 4 weeks back) | true |
| 2 Analog day | no recent day (date outside the data) | same calendar day in the latest year with data, else the latest day | same, moved to the nearest same weekday, else the latest same weekday | true |
| 3 | `data/processed/` lacks any of the three files | tiers 1–2 from `data/sample/` (one week, 2024-06-01..07) | | true |
| 4 | not even `data/sample/` | raises `ForecastUnavailableError` | | |

- **No training during planning.** The agent checks the model files itself before calling `predict()`, so
  `predict()`'s auto-train path is never reached. Missing models → tier 1/2 with `models_missing`.
- **No silent gap filling.** `predict()` interpolates/forward-fills missing weather and lags. The agent only
  calls it when the target day and its 7 lag days are complete (24 weather hours, 96 PV and 96 load slots per
  day); otherwise `out_of_range` or `history_incomplete`.
- **Persistence bands:** P10/P90 are per-slot 10th/90th percentiles over up to 7 consecutive days (solar) or 4
  same-weekday days (load) ending at the source day, widened to contain P50. With one usable day (e.g. load
  from `data/sample`) the band has zero width.
- **Quantile repair:** values are clipped to ≥ 0 and made ordered when a violation is ≤ 1e-6 kW. NaN/inf or a
  larger violation in model output is never repaired: it becomes `invalid_output` and tier 1/2 is used.
- **Start handling:** a naive `start` or one off a slot boundary raises `ValueError` (programmer error). An aware
  `start` in another zone is converted; `Forecast.start` equals it. **MVP limitation:** the models only forecast
  whole days, so a non-midnight `start` goes straight to persistence (`non_midnight_start`); the window may span
  two calendar days.
- Any other exception from the model step becomes `prediction_error`. The agent never raises for missing models
  or data.

**Observed weather as forecast.** Tier 0 feeds the target day's *observed* (reanalysis) weather into the models as
if it were a weather forecast. Tier 0 accuracy is therefore an upper bound on a real day-ahead forecast.

**Demo and backtests must replay dates inside 2022-01-01..2024-12-31.** Only there does tier 0 (the trained
models) run for real; it needs `data/processed/` and `models/` built locally (`docs/data-sources.md`, then
`python -m energy_brain.forecast.solar --train` and `... .load --train`). Any later date, such as today's,
is outside the data and always gets an `analog_day` fallback.

**Fallback note format** (one line, stable; the Explainer may quote it). `energy_brain.forecast.parse_note(note)`
returns the fields as a dict, `format_note(fields)` builds one:

```
FORECAST FALLBACK: reason=<code>[,<code>...]; method=<persistence|analog_day>; source=<processed|sample|injected>; solar_from=<YYYY-MM-DD>; load_from=<YYYY-MM-DD>; data_range=<YYYY-MM-DD..YYYY-MM-DD>; detail=<free text>
```

`solar_from` / `load_from` are the days whose slot 0 was copied. `method=persistence` only if both series use a
recent day. `detail` is free text (`;` replaced by `,`), always last. Reason codes, in check order:

| Code | Meaning |
| --- | --- |
| `non_midnight_start` | `start` is not 00:00 local |
| `axis_mismatch` | config time axis is not 96 × 15 min in Asia/Dhaka (what the models were trained on) |
| `out_of_range` | target day outside `data_range` (first..last complete day) |
| `history_incomplete` | target day or one of its 7 lag days has gaps |
| `models_missing` | a model file is missing in `models_dir` (listed in `detail`) |
| `prediction_error` | `predict()` raised (exception in `detail`) |
| `invalid_output` | model output NaN/inf, wrong length/timestamps or quantiles out of order beyond tolerance |
| `processed_data_missing` | added when `data/sample/` was used because `data/processed/` is incomplete |

Example: `FORECAST FALLBACK: reason=out_of_range; method=analog_day; source=processed; solar_from=2024-11-17;
load_from=2024-11-19; data_range=2022-01-01..2024-12-31; detail=2026-11-17 outside data range
2022-01-01..2024-12-31` (wrapped here; one line in practice).

### Signal agent rules (#15, `energy_brain.signals.CalendarSignalAgent`)

`CalendarSignalAgent(outages_df=None, signals_config=None, data_dir=None, data_range=(2022-01-01, 2024-12-31),
config=None).run(start)`. Nothing is read at import or construction; `config/signals.yaml` and the outage
calendar are loaded on the first `run` and cached. Slot count and length come from `n_slots()` /
`slot_minutes()`, never the 96 / 15 defaults of `data.outages.get_daily_outage_mask`.

| Field | Per slot t (slot start `s_t`) | Source (as of) |
| --- | --- | --- |
| `tariff_bdt_per_kwh` | `get_tariff_for_datetime(s_t)`: peak 17:00–23:00, off-peak 23:00–06:00, else flat. Slot start decides: 05:45 off-peak / 06:00 flat, 16:45 flat / 17:00 peak, 22:45 peak / 23:00 off-peak. A slot straddling a window edge (only possible if `slot_minutes` does not divide 60) raises `ValueError` | BERC MT-3 (2024-02) |
| `grid_co2_kg_per_kwh` | flat `grid_emission.factor_kg_per_kwh` = 0.62. No hourly grid-carbon data exists; none is invented | DoE / UNFCCC BUR1 (2022) |
| `diesel_price_bdt_per_l` | flat `diesel.price_bdt_per_l` = 107 | BPC (2024-05) |
| `diesel_co2_kg_per_l` | flat `diesel.emission_factor_kg_per_l` = 2.68 | IPCC 2006 / DEFRA |
| `outage` | true when any outage event overlaps any part of `[s_t, s_t + slot)` (partial overlap rounds up, same rule as `get_daily_outage_mask`). The Planner fixes grid import to 0 there | synthetic seeded calendar (#9) |

`agent.provenance()` returns one line with every source and as-of date (for the Data and AI provenance document).

**Outage events**, first that works: `outages_df` passed in (`source=injected`); `data/processed/outages.parquet`
(`source=processed`); otherwise `generate_outage_events()` in memory with its default seed and range
(`source=generated`, the same calendar the processed file is built from). The agent never needs a file, so CI
(no `data/processed/`) works. `data/sample/` is not used (one week only). Naive event timestamps are read as
Asia/Dhaka.

**Calendar range.** `data_range` (default 2022-01-01..2024-12-31 for every source) is the span the calendar
covers; a day inside it with no events means no outage. Each calendar day the window touches is resolved on its
own. A day outside the range uses the **analog day**: the same calendar day in the latest year of the range,
moved to the nearest same weekday (−3..+3 days), the same rule the Forecast agent uses for load, so on a
real 2026 date the outage day equals the Forecast's `load_from` when the Forecast reads the full
`data/processed` history (not when it falls back to the one-week `data/sample`). The analog day's events (clipped to that day)
are shifted onto the target day, and the run is flagged. Example: 2026-11-18 (Wed) → 2024-11-20 (Wed).

**Start handling.** Naive or off-slot `start` raises `ValueError`; an aware `start` in another zone is converted.
A non-midnight `start` is fully supported and is **not** a fallback: tariff is read per timestamp and the
outage window may span two calendar days (each resolved as above). The Forecast agent still falls back to
persistence for a non-midnight start (`non_midnight_start`).

**Fallback note** (one line, stable, same key=value style as the forecast note). `SignalTable` has no note
field, so the note is returned by `run_with_note(start) -> (SignalTable, note | None)`, stored in
`agent.last_note` (reset on every `run`) and logged with `logger.warning`. `energy_brain.signals.parse_note` /
`format_note` read and build it:

```
SIGNAL FALLBACK: reason=out_of_range; method=analog_day; source=<injected|processed|generated>; outage_from=<YYYY-MM-DD>[,<YYYY-MM-DD>]; data_range=<YYYY-MM-DD..YYYY-MM-DD>; detail=<free text>
```

`outage_from` lists the analog day of every out-of-range day in the window. The only reason code so far is
`out_of_range` (a day of the window is outside `data_range`). The note is `None` when every day came from the
calendar itself.

**Known gap:** signal notes do not reach `PlanResult.notes` (or `fallback_used`). The follow-up is a
`SignalTable.note` field plus an orchestrator change, to be agreed with the team.

### Risk agent rules (#18, `energy_brain.risk.P10RiskAgent`)

`P10RiskAgent(solar="p10", load="p50", advisory_load="p90").check(plan, forecast, signals, assets) -> RiskReport`.
A pure function of its four inputs: nothing is read from disk, cached or modified, so #22 (cloud event) can
pass an "actual" forecast (e.g. `P10RiskAgent(solar="p50", load="p50")` on an edited `Forecast`). It never
raises for a bad plan; `replans` is left at 0 (the orchestrator sets it). `agent.replay(...)` /
`energy_brain.risk.stress_replay(plan, forecast, signals, assets, solar, load)` return the per-slot
`StressReplay` behind the check.

**Order of checks.**

1. **Unsafe plan** (`planner.is_safe(plan)` false): `ok=False`, one `check=unsafe_plan` reason whose `detail`
   quotes the Planner's `NOT SAFE TO EXECUTE` note, `reserve_margin_kwh` = the plan's own reserve
   (`fixable=no`). A day that is infeasible at P50 cannot be fixed by more reserve. The schedule is not replayed.
2. **Gating scenario** (solar P10, load P50 by default, issue #18 / FR-06): the fixed plan is replayed with the
   recourse model below. Every contiguous block of slots with unserved energy is one `RISK FAIL` line
   (`check=unserved_energy`) and makes `ok` false.
3. **Notes** (`RISK NOTE`, never change `ok`): `check=advisory_unserved` when the same replay at
   `advisory_load` (P90) leaves energy unserved; `check=end_soc_short` when the battery ends the stressed day
   below `initial_soc_kwh` (the end-of-day rule is a Planner convenience, not a safety limit). So a plan can be
   `ok=True` with notes in `reasons`.

**Recourse model (reactive operator).** The plan fixes grid, battery, generator, HVAC and EV for every slot.
Per slot the gap is `(stress load − planned base load) + max(0, planned solar used − stress solar)` plus any
planned discharge the battery can no longer deliver. The operator closes it in this order:

1. solar the plan curtailed (if the stress solar exceeds what the plan used);
2. cut the planned battery charge;
3. extra grid import up to `max_import_kw` − planned import, **never in an outage slot**;
4. extra battery discharge up to `max_discharge_kw` and down to `soc_min_kwh` (the reserve exists for this, so
   the raised Planner floor does not apply);
5. the generator: up to `rated_kw`, or **started** (≥ `min_kw`) where the plan had it off; minimum-load surplus
   backs off discharge, grid and solar, then charges the battery.

HVAC (occupied minimum = comfort) and EV charging are never cut. What is still missing is unserved energy,
tagged `at_risk=comfort` (occupied slot), `ev` (EV charging in a required slot, `rules.ev_required_slots`) or
`base_load`. The battery carries its stressed SoC forward (later planned discharge is capped by what is
stored); in non-outage slots with no gap, spare grid recharges it back towards the planned SoC.

**Reserve margin.** `reserve_margin_kwh` = plan reserve + the largest *fixable* block's unserved kWh / η (the
stored energy the next plan must hold back), capped at `soc_max − soc_min`. It never decreases and asks for
exactly the missing energy; the orchestrator then uses `max(report, current + 50)`. `limit` per block:

| `limit` | Meaning | `fixable` |
| --- | --- | --- |
| `energy` | battery hit `soc_min_kwh` below its power limit; the run fits in the battery window | yes (margin grows) |
| `capacity` | energy-limited, but the run (slots before and through the block where any dispatch must discharge: `load + hvac + ev − solar − available grid − generator rated > 0`) needs more than `soc_max − soc_min` | no |
| `power` | battery at `max_discharge_kw`, generator at `rated_kw`, grid unavailable or at its limit | no |

A block with several kinds lists them (`energy,power`) and is not fixable. `fixable=no` blocks add nothing to
the margin, so an unfixable day gets the same margin back (the orchestrator still steps +50 kWh; see the known
limitations in section 5).

**Reason format** (one line, stable; `energy_brain.risk.parse_reason(line)` returns the fields plus
`kind=fail|note`, `format_reason(fields, fail=...)` builds one):

```
RISK FAIL: check=<code>; scenario=solar_<q>,load_<q>; slots=<a-b,c|none>; shortfall_kwh=<x.x>; limit=<energy|capacity|power[,...]|none>; at_risk=<comfort,ev,base_load|none>; reserve_kwh=<old>-><new>; fixable=<yes|no>; detail=<free text>
```

`RISK NOTE:` lines have the same fields. `detail` is free text (`;` replaced by `,`), always last; for
`unserved_energy` it names the grid state and the recourse used (`grid +x kWh, battery +y kWh, generator +z kWh,
charge cut +w kWh`). Check codes: `unserved_energy`, `unsafe_plan`, `replay_error` (fail);
`advisory_unserved`, `end_soc_short` (note). Example (wrapped here):

`RISK FAIL: check=unserved_energy; scenario=solar_p10,load_p50; slots=55; shortfall_kwh=8.9; limit=energy;
at_risk=comfort; reserve_kwh=0->9.41648; fixable=yes; detail=8.9 kWh unserved, grid unavailable (outage),
operator recourse used: generator +45.5 kWh` (stub cloudy day; the generator is already at `rated_kw` and the
battery hits `soc_min_kwh` in the last outage slot). Reserve values use Python's `:g` format.

**Measured so far** (placeholders, not pitch numbers): the test day set (`tests/test_risk_agent.py`, stub days
plus two `data/sample` days in 2024-06) is 7/7 feasible at solar P10 / load P50 after re-plan. In the #20
backtest (all 366 days of 2024, tier-0 forecasts) every plan passed Risk at w = 0.5, 1 and 0 without a re-plan.

### Evaluator (#20, `energy_brain.evaluator`)

Backtest of the optimiser against two rule-based baselines on past days. **Every headline number in the pitch
comes from `reports/backtest.json`**, with the weight and the baseline named. Rerun with one command:

```
python -m energy_brain.evaluator [--start 2024-01-01] [--end 2024-12-31] [--days N] [--weight 0.5 --weight 1 --weight 0] [--out reports]
```

Defaults: every day of 2024 (the out-of-sample test year in `reports/forecast_metrics.json`; models trained on
2022–2023), weights 0.5 (headline), 1 and 0. `--days N` picks N evenly spaced days; `--weight` repeats, the first
is the headline. Days outside the test year are refused unless `--allow-in-sample` (then flagged). Needs
`data/processed/{pv,load,weather}.parquet` and the six `models/*.txt`; refuses to run without them (no pitch
number from fallback forecasts or sample data). About 0.25 s per day for three weights (366 days: about 90 s).
Deterministic: reruns give identical numbers; only the `run` timings differ. Stub agents are refused.

**Per day.** The real Forecast, Signal and Asset agents run once and every method sees their outputs:

1. Optimiser: `orchestrator.run_plan` with `MilpPlanner` and `P10RiskAgent` (re-plans included), once per weight,
   on the P50 forecast.
2. Baselines (`evaluator.baselines`) built on the day's **actual** solar and base load (real-time rules need no
   forecast; giving them perfect information is the conservative choice). Each must pass
   `planner.find_violations` against the actuals; a violation excludes the day and is listed, the check is never
   relaxed.
3. Settlement (`evaluator.settle.settle`) of every schedule against the actuals with the same function.

**Baselines** (PRD section 2). Both use the same assets, signals, outages and hard constraints as the optimiser
(`planner.rules`), and keep the end-of-day rule (end SoC ≥ start SoC).

| Rule | B1 `baseline_solar_first` ("charge on arrival / solar-first") | B2 `baseline_time_of_use` ("simple time-of-use") |
| --- | --- | --- |
| HVAC | occupied minimum | same |
| EV | full charger power from the first required slot (00:00) until the energy is met | off-peak required slots first, then the other required slots |
| Solar | serves load first; surplus charges the battery; the rest is curtailed (no export) | same |
| Battery charge | surplus solar only | surplus solar, plus grid in off-peak slots: up to `soc_max` before the first peak slot, up to the starting SoC after the last |
| Battery discharge | covers any load solar leaves, down to the outage backup | peak slots only, down to the outage backup |
| Outage backup | battery keeps (and grid-charges to) what later outage slots need beyond the generator rating (`outage_backup_kwh`, ramped at the charge rate each slot allows) | same |
| Outage | battery above the backup, then the generator (≥ `min_kw`; surplus backs off discharge, charges the battery, then backs off solar); above its rating, the backup energy | same |
| Grid | the rest, up to `max_import_kw`; above it battery, then generator | same |
| End of day | SoC below the start: grid top-up in the latest non-outage slots, then replace the latest discharge with grid | same |

Peak / off-peak are read from the tariff signal: off-peak = slots at the day's lowest tariff, peak = at its
highest. The outage backup exists because a rule that drains the battery before a midday outage cannot carry
load above the generator's 800 kW (it broke the energy balance on stub and real days); the outage calendar is a
day-ahead signal the optimiser also sees.

**EV and the day window (conservative).** The vans park 18:00–07:00, so today's required energy is due before
07:00 (`rules.ev_required_slots`). Both baselines therefore charge from 00:00 (off-peak); a literal "charge on
arrival" at 18:00 would pay the peak tariff. This **understates the savings by about 144 kWh × 3.29 BDT
(≈ 474 BDT) per day**.

**Settlement.** `settle(schedule, actual, signals, assets)` runs `risk.stress_replay` with an "actual"
`Forecast` (P10 = P50 = P90 = measured, `settle.actual_forecast`) and `solar="p50", load="p50"`: the Risk
agent's reactive operator (curtailed solar, cut charging, extra grid never in an outage, extra battery down to
`soc_min`, generator; HVAC and EV never cut), not a second recourse model. The settled `Schedule` balances in
every slot: unserved energy (`unserved_kw`) is taken off base load, then EV, then HVAC, and reported; energy the
replay had to dump (generator minimum load, planned discharge no longer needed) is added to `base_load_kw` and
reported as `dumped_kw`; `solar_used_kw` closes the balance. Totals come from `compute_totals` only. A schedule
that does not balance (an unsafe plan's diagnostic schedule) is not settled; its day is excluded from totals and
listed with the reason. Known bias: when actual solar beats the plan with no gap to close, the replay curtails
the extra instead of replacing grid import (it was built for shortfalls). This only penalises the optimiser
(2024: about 6,300 kWh, 59,000 BDT, 0.14 % of cost).

**Adjusted figures (headline).** `adjusted_cost_bdt` / `adjusted_co2_kg` add the refill of any end-of-day battery
shortfall (`initial − soc[-1]`, if positive) from the grid at the day's lowest tariff and that slot's grid CO2,
divided by the one-way efficiency. A surplus is not credited. Raw `cost_bdt` / `co2_kg` are kept.

**Forecast quality** per day: P50 MAE (kW) for solar and load, and P10–P90 coverage, defined as in
`reports/forecast_metrics.json` (share of slots with `p10 ≤ actual ≤ p90`, all slots), plus solar daylight
coverage (slots with actual > 0 or P90 > 0). **Label everywhere: measured with observed weather as forecast
input (tier-0), NOT a true day-ahead weather forecast, so real-world error would be larger.**

**`reports/backtest.json`** (`schema_version` 1, floats rounded to 4 decimals):

| Key | Content |
| --- | --- |
| `caveats` | out-of-sample 2024, synthetic load, simulated PV, synthetic outages, the forecast label, baselines on actuals, EV understatement, adjusted figures, HVAC, settlement bias, flat grid CO2 |
| `headline.lines` | pitch sentences at the headline weight, every baseline named, the other weights beside it |
| `summary.days` | requested / included / excluded (with `excluded_dates` + reason), outage days, workdays |
| `summary.by_weight["<w>"]` | `totals` per method (sums of the row fields), `vs[<baseline>]` (cost/CO2 saved % adjusted and raw, BDT / kg saved, generator hours and diesel saved, `days_cost_worse`, `days_co2_worse`, per-day min/median/max %, splits `outage_days` / `no_outage_days` / `workdays` / `off_days`), `plan_health` (not optimal, unsafe, Risk not ok, re-planned, fallback, with dates) |
| `summary.forecast` | pooled MAE and coverage, tier-0 days, fallback dates, the label and the coverage definition |
| `config` | first/last day, weights, out-of-sample flag, test/train years, sha256 of the data files, models and `config/*.yaml`, git commit + dirty flag, package versions, provenance (Signal agent `provenance()` line plus load, PV, weather, outages, tariff, grid CO2, forecast) |
| `run` | wall-clock runtime (not deterministic) |
| `days[]` | per day: date, weekday, workday, outage slots, forecast fallback and note, signal note, forecast quality, `included` / `excluded_reason`, `methods[]` (one row per method) |

**`reports/backtest.csv`**: `#` comment lines with the caveats, then one row per day and per method
(`pandas.read_csv(path, comment="#")`). Columns: `date, method, weight` (blank for baselines), `status, plan_safe,
risk_ok, replans, reserve_margin_kwh, fallback_used, violations, settled`, settled `cost_bdt, co2_kg`,
`adjusted_cost_bdt, adjusted_co2_kg`, `planned_cost_bdt, planned_co2_kg` (the plan's own totals; for baselines =
settled), `grid_kwh, solar_kwh, generator_kwh, generator_hours, diesel_litres, unserved_kwh, dumped_kwh,
end_soc_delta_kwh, end_soc_shortfall_kwh`, `recourse_{grid,battery,generator,charge_cut}_kwh`,
`cost_saved_vs_<baseline>_pct`, `co2_saved_vs_<baseline>_pct` (`(baseline − method) / baseline × 100` on the
adjusted figures), then `workday, outage_slots, included` and the day's forecast fields.

## 5. Orchestrator

`run_plan(start, weight, *, forecast_agent, signal_agent, assets, planner, risk, max_replans=3)`:

1. Forecast, Signal and Asset agents run for `start`; outputs must share `start` and slot count, else
   `AlignmentError`.
2. Planner solves with reserve 0. Risk checks it.
3. While Risk says not ok and fewer than `max_replans` re-plans: reserve = max(Risk's margin, current + 50 kWh),
   re-plan, re-check. It **stops early** (no more re-plans) when the plan is unsafe (`planner.is_safe`) or when
   every structured Risk failure says `fixable=no` (`risk.parse_reason`); free-text reasons never stop it.
4. Returns the **best plan seen**, not simply the last: safe and Risk-ok, else safe (the latest), else the latest
   unsafe one. So a re-plan that makes the Planner infeasible never replaces an earlier safe plan. `risk` is that
   plan's report with `replans` = re-plans actually run. It never raises for a failing Risk: if the returned plan
   still fails, `optimal` becomes `feasible` and Risk's reasons are appended to `notes`. Risk notes
   (`RISK NOTE:` lines) of an ok plan are appended to `notes` under "Risk notes:". If an earlier attempt was
   returned or re-planning stopped early, `notes` says so.

Real agents: `run_plan(start, weight, **energy_brain.defaults.build_default_agents())` uses
`ModelForecastAgent` (#14), `CalendarSignalAgent` (#15), `AssetAgent` (#16), `MilpPlanner` (#17) and
`P10RiskAgent` (#18). Override any of them by keyword, e.g. `build_default_agents(risk=MyRisk())`.

**Known gap:** Signal agent notes (`SIGNAL FALLBACK:`) still do not reach `PlanResult.notes` (needs
`SignalTable.note`, a contract change to agree with the team).

## 6. Handover formats for the phase-1 teammate

**Forecaster** (#11, #12 → Forecast agent #14): provide `predict(start) -> Forecast` (or a `ForecastAgent`
with `run(start)`). Content per day: `n_slots()` (96) rows of

| timestamp (slot start, Asia/Dhaka) | solar p10_kw | solar p50_kw | solar p90_kw | load p10_kw | load p50_kw | load p90_kw |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-11-17T00:00:00+06:00 | 0.0 | 0.0 | 0.0 | 225.0 | 250.0 | 275.0 |
| … | | | | | | |

Timestamps are implicit in the model (`start` + i × 15 min); rows must be in slot order. Load = inflexible
base load (no HVAC, no EV). If you keep a DataFrame internally, convert each column with `.tolist()`.

**Signals** (#8, #10 → Signal agent #15): one `SignalTable` per day with the five per-slot lists above.

**Outages** (#10): `outage` is a list of `n_slots()` booleans, `true` = grid down for the whole slot. An
outage from 13:00 to 14:00 sets slots 52–55 to `true`. Partial-slot outages round up to the whole slot.

## 7. Stubs

`energy_brain.stubs` provides `StubForecastAgent`, `StubCloudyForecastAgent`, `StubSignalAgent`,
`StubAssetProvider` / `stub_assets(start)`, `StubPlanner`, `StubRisk`, `FailOnceRisk`.
**STUB DATA - PLACEHOLDERS ONLY, NEVER USE IN THE PITCH.** The real asset agent (#16) is
`energy_brain.assets.AssetAgent`, a drop-in replacement for `StubAssetProvider` (same shape; it differs only
in marking off-days unoccupied). The real forecast agent (#14) is `energy_brain.forecast.ModelForecastAgent`,
a drop-in replacement for `StubForecastAgent`. The real signal agent (#15) is
`energy_brain.signals.CalendarSignalAgent`, a drop-in replacement for `StubSignalAgent`. The real Risk agent
(#18) is `energy_brain.risk.P10RiskAgent`, a drop-in replacement for `StubRisk`.

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from energy_brain.orchestrator import run_plan
from energy_brain.stubs import StubAssetProvider, StubForecastAgent, StubPlanner, StubRisk, StubSignalAgent

result = run_plan(
    datetime(2026, 11, 17, tzinfo=ZoneInfo("Asia/Dhaka")), weight=0.5,
    forecast_agent=StubForecastAgent(), signal_agent=StubSignalAgent(),
    assets=StubAssetProvider(), planner=StubPlanner(), risk=StubRisk(),
)
print(result.model_dump_json(indent=2))
```
