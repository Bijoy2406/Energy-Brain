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
| `ok` | false = plan breaks a hard constraint under P10 solar / P90 load |
| `reasons` | human-readable list |
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

## 5. Orchestrator

`run_plan(start, weight, *, forecast_agent, signal_agent, assets, planner, risk, max_replans=3)`:

1. Forecast, Signal and Asset agents run for `start`; outputs must share `start` and slot count, else
   `AlignmentError`.
2. Planner solves with reserve 0. Risk checks it.
3. While Risk says not ok and fewer than `max_replans` re-plans: reserve = max(Risk's margin, current + 50 kWh),
   re-plan, re-check.
4. Returns the last `PlanResult` with `risk` filled (`replans` counted). If Risk still fails, it does **not**
   raise: `optimal` becomes `feasible` and Risk's reasons are appended to `notes`.

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
in marking off-days unoccupied).

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
