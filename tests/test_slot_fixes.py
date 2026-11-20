"""Fixed decisions must constrain every solve, including diagnosis and Risk re-plans."""

from dataclasses import FrozenInstanceError

import pytest

from energy_brain.orchestrator import run_plan
from energy_brain.planner import MilpPlanner, find_violations, is_safe
from energy_brain.planner.fixes import SlotFix, SlotFixes
from energy_brain.stubs import FailOnceRisk, StubAssetProvider, StubForecastAgent, StubSignalAgent


def test_fixed_battery_and_generator(forecast, signals, assets):
    fixes = SlotFixes(
        (
            SlotFix(0, battery_charge_kw=100, battery_discharge_kw=0),
            SlotFix(76, generator_kw=assets.generator.min_kw),
        )
    )
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5, fixes=fixes)
    assert is_safe(plan)
    assert plan.schedule.battery_charge_kw[0] == pytest.approx(100)
    assert plan.schedule.battery_discharge_kw[0] == 0
    assert plan.schedule.generator_kw[76] == pytest.approx(assets.generator.min_kw)
    assert find_violations(plan.schedule, forecast, signals, assets, fixes=fixes) == []
    with pytest.raises(FrozenInstanceError):
        fixes.slots = ()


def test_generator_below_min_is_invalid(forecast, signals, assets):
    with pytest.raises(ValueError, match="generator_kw"):
        MilpPlanner().plan(
            forecast,
            signals,
            assets,
            0.5,
            fixes=SlotFixes((SlotFix(0, generator_kw=assets.generator.min_kw / 2),)),
        )


def test_impossible_soc_fix_is_unsafe_not_exception(forecast, signals, assets):
    fixes = SlotFixes(
        tuple(
            SlotFix(t, battery_charge_kw=0, battery_discharge_kw=assets.battery.max_discharge_kw)
            for t in range(8)
        )
    )
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5, fixes=fixes)
    assert not is_safe(plan)
    assert plan.status == "infeasible"
    assert any("Fixed slots" in n and "SoC" in n for n in plan.notes)
    assert find_violations(plan.schedule, forecast, signals, assets, fixes=fixes)


def test_elastic_keeps_fixes(forecast, signals, assets):
    # No supply during a night outage: the elastic solve can only explain unserved demand.
    signals = signals.model_copy(update={"outage": [True] * 96})
    fixes = SlotFixes((SlotFix(0, battery_charge_kw=0, battery_discharge_kw=0, generator_kw=0),))
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5, fixes=fixes)
    assert not is_safe(plan)
    assert plan.schedule.generator_kw[0] == 0
    assert plan.schedule.battery_discharge_kw[0] == 0
    assert any("Fixed slots" in n and "0" in n for n in plan.notes)


def test_none_fixes_preserves_default(forecast, signals, assets):
    planner = MilpPlanner()
    a = planner.plan(forecast, signals, assets, 0.5)
    b = planner.plan(forecast, signals, assets, 0.5, fixes=None)
    assert a.model_dump(exclude={"solve_seconds"}) == b.model_dump(exclude={"solve_seconds"})


def test_checker_checks_fixes_and_all_hard_constraints(forecast, signals, assets):
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5)
    fixes = SlotFixes((SlotFix(0, generator_kw=assets.generator.rated_kw),))
    messages = find_violations(plan.schedule, forecast, signals, assets, fixes=fixes)
    assert any("fixed generator_kw" in m for m in messages)


def test_fixes_survive_risk_replans(start):
    fixes = SlotFixes((SlotFix(0, battery_charge_kw=100, battery_discharge_kw=0),))
    plan = run_plan(
        start,
        0.5,
        forecast_agent=StubForecastAgent(),
        signal_agent=StubSignalAgent(),
        assets=StubAssetProvider(),
        planner=MilpPlanner(),
        risk=FailOnceRisk(margin_kwh=200),
        fixes=fixes,
    )
    assert plan.risk.replans == 1
    assert plan.risk.ok
    assert plan.schedule.battery_charge_kw[0] == pytest.approx(100)


def test_solver_failure_cannot_silently_drop_fix(forecast, signals, assets, monkeypatch):
    from energy_brain.planner.solve import SolveOutcome

    monkeypatch.setattr(
        "energy_brain.planner.agent.solve_model",
        lambda *a, **k: SolveOutcome("error", 0, message="simulated failure"),
    )
    fixes = SlotFixes((SlotFix(0, generator_kw=assets.generator.rated_kw),))
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5, fixes=fixes)
    assert not is_safe(plan)
    assert any("fixed generator_kw" in n for n in plan.notes)


def test_conflicting_battery_modes_are_infeasible(forecast, signals, assets):
    fixes = SlotFixes((SlotFix(0, battery_charge_kw=100, battery_discharge_kw=100),))
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5, fixes=fixes)
    assert not is_safe(plan)
    assert any("Fixed slots 0" in n for n in plan.notes)
