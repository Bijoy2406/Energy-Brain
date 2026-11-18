"""Tests for the MILP Planner agent (#17). Stub inputs: PLACEHOLDER data, never use the numbers in the pitch.

Constraint checks here are written out independently of ``planner.checks`` so a bug in the checker cannot
hide a bug in the model.
"""

from __future__ import annotations

import math
from datetime import datetime

import pyomo.environ as pe
import pytest

from energy_brain.accounting import compute_totals
from energy_brain.assets import AssetAgent
from energy_brain.interfaces import PlannerAgent
from energy_brain.orchestrator import run_plan
from energy_brain.planner import UNSAFE_PREFIX, MilpPlanner, find_violations, is_safe, rule_based_schedule
from energy_brain.planner import solve as solve_mod
from energy_brain.planner.model import Scales, build_model
from energy_brain.planner.rules import (
    ev_optional_slots,
    ev_required_slots,
    hvac_allowed,
    soc_floor_kwh,
)
from energy_brain.planner.solve import solve_model
from energy_brain.stubs import FailOnceRisk, StubForecastAgent, StubRisk, StubSignalAgent
from energy_brain.timeaxis import n_slots, slot_hours

TOL = 1e-4
WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]


@pytest.fixture(scope="module")
def planner() -> MilpPlanner:
    return MilpPlanner()


@pytest.fixture
def real_assets(start):
    return AssetAgent().run(start)


@pytest.fixture(params=["stub", "real"])
def any_assets(request, assets, real_assets):
    return assets if request.param == "stub" else real_assets


def assert_hard_constraints(plan, forecast, signals, assets, reserve=0.0):
    """Independent check of every hard constraint in docs/interfaces.md."""
    s, h = plan.schedule, slot_hours()
    b, hv, ev, gen = assets.battery, assets.hvac, assets.ev, assets.generator
    eta = math.sqrt(b.round_trip_efficiency)
    n = n_slots()
    assert len(s.grid_import_kw) == n
    assert s.check_energy_balance(TOL) <= TOL
    prev = b.initial_soc_kwh
    floor = min(b.soc_min_kwh + reserve, b.soc_max_kwh)
    for t in range(n):
        assert s.base_load_kw[t] == pytest.approx(forecast.load.p50_kw[t])
        assert s.solar_used_kw[t] + s.solar_curtailed_kw[t] == pytest.approx(
            forecast.solar.p50_kw[t], abs=TOL
        )
        assert s.grid_import_kw[t] <= assets.grid.max_import_kw + TOL
        if signals.outage[t]:
            assert s.grid_import_kw[t] <= TOL, f"grid import in outage slot {t}"
        assert s.battery_charge_kw[t] <= b.max_charge_kw + TOL
        assert s.battery_discharge_kw[t] <= b.max_discharge_kw + TOL
        assert not (s.battery_charge_kw[t] > TOL and s.battery_discharge_kw[t] > TOL), f"slot {t}"
        prev += eta * s.battery_charge_kw[t] * h - s.battery_discharge_kw[t] * h / eta
        assert s.battery_soc_kwh[t] == pytest.approx(prev, abs=1e-3)
        reachable = b.initial_soc_kwh + eta * b.max_charge_kw * h * (t + 1)
        assert s.battery_soc_kwh[t] >= max(b.soc_min_kwh, min(floor, reachable)) - TOL
        assert s.battery_soc_kwh[t] <= b.soc_max_kwh + TOL
        assert s.hvac_kw[t] <= hv.rated_kw + TOL
        if hv.occupied[t]:
            assert s.hvac_kw[t] >= hv.min_kw_when_occupied - TOL, f"comfort minimum broken in slot {t}"
        if not ev.plugged_in[t]:
            assert s.ev_charge_kw[t] <= TOL, f"EV charging while unplugged in slot {t}"
        assert s.ev_charge_kw[t] <= ev.charger_kw + TOL
        g = s.generator_kw[t]
        assert g <= TOL or gen.min_kw - TOL <= g <= gen.rated_kw + TOL, f"generator {g} kW in slot {t}"
    assert s.battery_soc_kwh[-1] >= b.initial_soc_kwh - TOL
    # EV energy due before departure (day starts 00:00 -> plugged slots before departure_slot)
    due = [t for t in range(ev.departure_slot) if ev.plugged_in[t]]
    assert sum(s.ev_charge_kw[t] for t in due) * h >= ev.energy_required_kwh - TOL


# ---------------------------------------------------------------- basic solve


def test_satisfies_planner_protocol(planner):
    assert isinstance(planner, PlannerAgent)


def test_solves_sample_day_fast_and_feasible(planner, forecast, signals, any_assets):
    plan = planner.plan(forecast, signals, any_assets, weight=0.5)
    assert plan.status in ("optimal", "feasible")
    assert plan.solve_seconds < 10.0
    assert is_safe(plan)
    assert not plan.fallback_used
    assert plan.weight == 0.5
    assert plan.start == forecast.start
    assert_hard_constraints(plan, forecast, signals, any_assets)
    assert find_violations(plan.schedule, forecast, signals, any_assets) == []


def test_totals_equal_compute_totals(planner, forecast, signals, any_assets):
    plan = planner.plan(forecast, signals, any_assets, weight=0.3)
    assert plan.totals == compute_totals(plan.schedule, signals, any_assets)


def test_model_cost_and_co2_match_accounting(forecast, signals, assets):
    m = build_model(forecast, signals, assets, 0.5, 0.0, Scales(1.0, 1.0))
    out = solve_model(m, list(forecast.load.p50_kw))
    assert out.status == "optimal"
    totals = compute_totals(out.schedule, signals, assets)
    assert pe.value(m.cost_bdt) == pytest.approx(totals.cost_bdt, rel=1e-6)
    assert pe.value(m.co2_kg) == pytest.approx(totals.co2_kg, rel=1e-6)


def test_inputs_not_mutated(planner, forecast, signals, assets):
    before = (forecast.model_dump_json(), signals.model_dump_json(), assets.model_dump_json())
    planner.plan(forecast, signals, assets, weight=0.5, reserve_margin_kwh=100.0)
    assert (forecast.model_dump_json(), signals.model_dump_json(), assets.model_dump_json()) == before


def test_outage_slots_use_battery_or_generator(planner, forecast, signals, assets):
    plan = planner.plan(forecast, signals, assets, weight=0.5)
    s = plan.schedule
    out = [t for t, o in enumerate(signals.outage) if o]
    assert out, "stub day should contain an outage"
    for t in out:
        assert s.grid_import_kw[t] == 0.0
        assert s.battery_discharge_kw[t] + s.generator_kw[t] > 0


def test_invalid_arguments_raise(planner, forecast, signals, assets):
    with pytest.raises(ValueError):
        planner.plan(forecast, signals, assets, weight=1.5)
    with pytest.raises(ValueError):
        planner.plan(forecast, signals, assets, weight=0.5, reserve_margin_kwh=-1.0)


# ---------------------------------------------------------------- weight sweep


@pytest.fixture(scope="module")
def sweep():
    from zoneinfo import ZoneInfo

    start = datetime(2026, 11, 17, tzinfo=ZoneInfo("Asia/Dhaka"))
    f, s, a = StubForecastAgent().run(start), StubSignalAgent().run(start), AssetAgent().run(start)
    p = MilpPlanner()
    return {w: p.plan(f, s, a, weight=w) for w in WEIGHTS}


def test_weight_extremes_are_cheapest_and_cleanest(sweep):
    costs = {w: p.totals.cost_bdt for w, p in sweep.items()}
    co2 = {w: p.totals.co2_kg for w, p in sweep.items()}
    assert costs[1.0] <= min(costs.values()) * (1 + 1e-6)
    assert co2[0.0] <= min(co2.values()) * (1 + 1e-6)
    # the stub day has a real trade-off (peak tariff vs battery losses)
    assert costs[1.0] < costs[0.0]
    assert co2[0.0] < co2[1.0]


def test_totals_move_monotonically_with_weight(sweep):
    ws = sorted(sweep)
    for lo, hi in zip(ws, ws[1:], strict=False):
        assert sweep[hi].totals.cost_bdt <= sweep[lo].totals.cost_bdt * (1 + 1e-6)
        assert sweep[hi].totals.co2_kg >= sweep[lo].totals.co2_kg * (1 - 1e-6)


def test_weight_changes_battery_schedule(sweep):
    # w=1 (cost) uses the battery for peak-tariff arbitrage, w=0 (CO2) avoids the round-trip losses
    through = {w: sum(p.schedule.battery_discharge_kw) for w, p in sweep.items()}
    assert through[1.0] > through[0.0]


# ---------------------------------------------------------------- reserve margin


@pytest.mark.parametrize("reserve", [0.0, 100.0, 300.0, 500.0])
def test_reserve_margin_raises_soc_floor(planner, forecast, signals, real_assets, reserve):
    plan = planner.plan(forecast, signals, real_assets, weight=1.0, reserve_margin_kwh=reserve)
    assert plan.status in ("optimal", "feasible")
    assert plan.reserve_margin_kwh == reserve
    floor = soc_floor_kwh(real_assets.battery, reserve)
    assert all(soc >= f - TOL for soc, f in zip(plan.schedule.battery_soc_kwh, floor, strict=True))
    assert_hard_constraints(plan, forecast, signals, real_assets, reserve)


def test_soc_floor_ramps_from_initial_charge(assets):
    b = assets.battery  # 1000 kWh, min 100, max 950, initial 500, 500 kW, rte 0.9
    floor = soc_floor_kwh(b, 600.0)
    step = math.sqrt(b.round_trip_efficiency) * b.max_charge_kw * slot_hours()
    assert floor[0] == pytest.approx(b.initial_soc_kwh + step)
    assert floor[-1] == pytest.approx(min(b.soc_min_kwh + 600.0, b.soc_max_kwh))
    assert all(x <= y + 1e-9 for x, y in zip(floor, floor[1:], strict=False))
    assert soc_floor_kwh(b, 0.0) == [b.soc_min_kwh] * n_slots()
    assert max(soc_floor_kwh(b, 10_000.0)) == pytest.approx(b.soc_max_kwh)


def test_larger_reserve_never_lowers_minimum_soc(planner, forecast, signals, real_assets):
    lows = [
        min(planner.plan(forecast, signals, real_assets, 1.0, reserve_margin_kwh=r).schedule.battery_soc_kwh)
        for r in (0.0, 150.0, 300.0)
    ]
    assert lows == sorted(lows)


# ---------------------------------------------------------------- EV and HVAC rules


def test_ev_rule_across_midnight(planner, forecast, signals, real_assets):
    ev = real_assets.ev  # parked 18:00-07:00, day starts 00:00
    req, opt = ev_required_slots(ev), ev_optional_slots(ev)
    assert req == list(range(ev.departure_slot))
    assert opt == list(range(ev.arrival_slot, n_slots()))
    plan = planner.plan(forecast, signals, real_assets, weight=0.5)
    delivered = sum(plan.schedule.ev_charge_kw[t] for t in req) * slot_hours()
    assert delivered >= ev.energy_required_kwh - TOL
    assert any("evening plug-in window" in n for n in plan.notes)


def test_ev_rule_without_wrap(assets):
    ev = assets.ev.model_copy(
        update={
            "plugged_in": [10 <= t < 40 for t in range(n_slots())],
            "arrival_slot": 10,
            "departure_slot": 40,
        }
    )
    assert ev_required_slots(ev) == list(range(10, 40))
    assert ev_optional_slots(ev) == []
    ev0 = assets.ev.model_copy(
        update={"plugged_in": [t >= 50 for t in range(n_slots())], "arrival_slot": 50, "departure_slot": 0}
    )
    assert ev_required_slots(ev0) == list(range(50, n_slots()))


def test_hvac_allowed_includes_precool_window_only(real_assets):
    hv = real_assets.hvac
    allowed = hvac_allowed(hv)
    first = hv.occupied.index(True)
    k = int(hv.max_precool_hours / slot_hours())
    assert all(allowed[first - k : first])
    assert not any(allowed[: first - k])


def test_friday_off_day_solves(planner):
    from zoneinfo import ZoneInfo

    friday = datetime(2026, 11, 20, tzinfo=ZoneInfo("Asia/Dhaka"))
    f, s, a = StubForecastAgent().run(friday), StubSignalAgent().run(friday), AssetAgent().run(friday)
    assert not any(a.hvac.occupied)
    plan = planner.plan(f, s, a, weight=0.5)
    assert plan.status == "optimal"
    assert max(plan.schedule.hvac_kw) == 0.0
    assert_hard_constraints(plan, f, s, a)


# ---------------------------------------------------------------- infeasible / fallback


def _assert_unsafe(plan, signals, assets):
    assert plan.status == "infeasible"
    assert plan.fallback_used is True
    assert plan.notes[0].startswith(UNSAFE_PREFIX)
    assert not is_safe(plan)
    assert plan.totals == compute_totals(plan.schedule, signals, assets)


def test_outage_all_day_without_backup_is_flagged_not_raised(planner, forecast, signals, assets):
    dark = signals.model_copy(update={"outage": [True] * n_slots()})
    tiny = assets.model_copy(
        update={
            "generator": assets.generator.model_copy(update={"rated_kw": 1.0, "min_kw": 0.0}),
            "battery": assets.battery.model_copy(
                update={"capacity_kwh": 1.0, "soc_min_kwh": 0.0, "soc_max_kwh": 1.0, "initial_soc_kwh": 0.5}
            ),
        }
    )
    plan = planner.plan(forecast, dark, tiny, weight=0.5)
    _assert_unsafe(plan, dark, tiny)
    assert any("cannot be served" in n for n in plan.notes)
    assert all(g == 0.0 for g in plan.schedule.grid_import_kw)  # outage still respected


def test_ev_window_too_short_is_flagged(planner, forecast, signals, real_assets):
    # 600 kWh fits the whole plug-in window (schema check) but not the slots before departure
    ev = real_assets.ev
    need = ev.charger_kw * slot_hours() * len(ev_required_slots(ev)) + 50.0
    bad = real_assets.model_copy(update={"ev": ev.model_copy(update={"energy_required_kwh": need})})
    plan = planner.plan(forecast, signals, bad, weight=0.5)
    _assert_unsafe(plan, signals, bad)
    assert any("EV fleet needs" in n for n in plan.notes)


def test_solver_error_uses_safe_rule_based_fallback(planner, forecast, signals, assets, monkeypatch):
    def broken(*args, **kwargs):
        return solve_mod.SolveOutcome("error", 0.0, message="simulated solver crash")

    monkeypatch.setattr("energy_brain.planner.agent.solve_model", broken)
    plan = planner.plan(forecast, signals, assets, weight=0.5)
    assert plan.status == "fallback"
    assert plan.fallback_used is True
    assert is_safe(plan)
    assert "simulated solver crash" in plan.notes[0]
    assert_hard_constraints(plan, forecast, signals, assets)


def test_solver_error_with_unsafe_fallback_is_flagged(planner, forecast, signals, assets, monkeypatch):
    def broken(*args, **kwargs):
        return solve_mod.SolveOutcome("error", 0.0, message="simulated solver crash")

    monkeypatch.setattr("energy_brain.planner.agent.solve_model", broken)
    dark = signals.model_copy(update={"outage": [True] * n_slots()})
    plan = planner.plan(forecast, dark, assets, weight=0.5)
    _assert_unsafe(plan, dark, assets)


def test_solver_exception_never_escapes(forecast, signals, assets, monkeypatch):
    class Boom:
        def available(self):
            raise RuntimeError("highs exploded")

    monkeypatch.setattr(solve_mod, "Highs", Boom)
    m = build_model(forecast, signals, assets, 0.5, 0.0, Scales(1.0, 1.0))
    out = solve_model(m, list(forecast.load.p50_kw))
    assert out.status == "error" and "highs exploded" in out.message


def test_reference_schedule_is_valid_on_sample_day(forecast, signals, any_assets):
    ref = rule_based_schedule(forecast, signals, any_assets)
    assert find_violations(ref, forecast, signals, any_assets) == []


def test_checker_detects_violations(planner, forecast, signals, assets):
    plan = planner.plan(forecast, signals, assets, weight=0.5)
    t = signals.outage.index(True)
    rows = plan.schedule.model_dump()
    rows["grid_import_kw"][t] += 10.0
    rows["solar_used_kw"][t] = max(0.0, rows["solar_used_kw"][t] - 10.0)
    broken = type(plan.schedule)(**rows)
    msgs = find_violations(broken, forecast, signals, assets)
    assert any("outage" in m for m in msgs)


# ---------------------------------------------------------------- orchestrator


def test_run_plan_end_to_end_with_milp(start):
    plan = run_plan(
        start,
        0.5,
        forecast_agent=StubForecastAgent(),
        signal_agent=StubSignalAgent(),
        assets=AssetAgent(),
        planner=MilpPlanner(),
        risk=StubRisk(),
    )
    assert plan.status == "optimal"
    assert plan.risk is not None and plan.risk.ok and plan.risk.replans == 0
    assert plan.totals == compute_totals(plan.schedule, StubSignalAgent().run(start), AssetAgent().run(start))


def test_run_plan_replans_with_reserve(start):
    plan = run_plan(
        start,
        1.0,
        forecast_agent=StubForecastAgent(),
        signal_agent=StubSignalAgent(),
        assets=AssetAgent(),
        planner=MilpPlanner(),
        risk=FailOnceRisk(margin_kwh=200.0),
    )
    assert plan.risk.replans == 1
    assert plan.reserve_margin_kwh == 200.0
    floor = soc_floor_kwh(AssetAgent().run(start).battery, 200.0)
    assert all(s >= f - TOL for s, f in zip(plan.schedule.battery_soc_kwh, floor, strict=True))
