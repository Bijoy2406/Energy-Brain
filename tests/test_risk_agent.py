"""Tests for the P10 Risk agent (issue #18). No dependency on data/processed or models/."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from energy_brain.assets import AssetAgent
from energy_brain.defaults import build_default_agents
from energy_brain.forecast import ModelForecastAgent
from energy_brain.interfaces import RiskAgent
from energy_brain.orchestrator import run_plan
from energy_brain.planner import UNSAFE_PREFIX, MilpPlanner, is_safe
from energy_brain.risk import P10RiskAgent, format_reason, parse_reason, stress_replay
from energy_brain.risk import agent as risk_agent_mod
from energy_brain.signals import CalendarSignalAgent
from energy_brain.stubs import StubAssetProvider, StubCloudyForecastAgent, StubForecastAgent, StubSignalAgent
from energy_brain.timeaxis import n_slots

DHAKA = ZoneInfo("Asia/Dhaka")
START = datetime(2026, 11, 17, tzinfo=DHAKA)
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"
STUB_OUTAGE = range(52, 56)  # StubSignalAgent: 13:00-14:00


class OutageSignals(StubSignalAgent):
    """Stub signals with the outage moved to slots [first, last)."""

    def __init__(self, first: int, last: int) -> None:
        self.first, self.last = first, last

    def run(self, start):
        table = super().run(start)
        return table.model_copy(update={"outage": [self.first <= t < self.last for t in range(n_slots())]})


def _inputs(forecast_agent=None, signal_agent=None):
    forecast = (forecast_agent or StubForecastAgent()).run(START)
    signals = (signal_agent or StubSignalAgent()).run(START)
    assets = StubAssetProvider().run(START)
    return forecast, signals, assets


def _plan(forecast, signals, assets, reserve=0.0):
    return MilpPlanner().plan(forecast, signals, assets, 0.5, reserve_margin_kwh=reserve)


def _run(forecast_agent, signal_agent, risk=None, start=START, assets=None):
    return run_plan(
        start,
        0.5,
        forecast_agent=forecast_agent,
        signal_agent=signal_agent,
        assets=assets or StubAssetProvider(),
        planner=MilpPlanner(),
        risk=risk or P10RiskAgent(),
    )


def _parsed(report, kind=None):
    out = [parse_reason(r) for r in report.reasons]
    assert all(p is not None for p in out)
    return [p for p in out if kind is None or p["kind"] == kind]


# ---------------------------------------------------------------- contract


def test_satisfies_protocol_and_validates_scenario():
    assert isinstance(P10RiskAgent(), RiskAgent)
    agent = P10RiskAgent()
    assert (agent.solar, agent.load, agent.advisory_load) == ("p10", "p50", "p90")
    assert P10RiskAgent(load="p90").advisory_load is None  # advisory equal to the gate is skipped
    with pytest.raises(ValueError):
        P10RiskAgent(solar="p05")
    with pytest.raises(ValueError):
        P10RiskAgent(advisory_load="max")


def test_default_agents_use_p10_risk_and_keep_overrides():
    agents = build_default_agents()
    assert isinstance(agents["risk"], P10RiskAgent)
    other = P10RiskAgent(load="p90")
    assert build_default_agents(risk=other)["risk"] is other


# ---------------------------------------------------------------- gating check


def test_plan_safe_at_p10_is_ok_without_reasons():
    forecast, signals, assets = _inputs()
    plan = _plan(forecast, signals, assets)
    report = P10RiskAgent().check(plan, forecast, signals, assets)
    assert report.ok is True
    assert report.reasons == []
    assert report.reserve_margin_kwh == 0.0
    assert report.replans == 0


def test_cloudy_day_fails_with_reasons_and_a_sensible_margin():
    forecast, signals, assets = _inputs(StubCloudyForecastAgent())
    plan = _plan(forecast, signals, assets)
    agent = P10RiskAgent()
    report = agent.check(plan, forecast, signals, assets)
    assert report.ok is False
    fails = _parsed(report, "fail")
    assert len(fails) == 1
    f = fails[0]
    assert f["check"] == "unserved_energy"
    assert f["scenario"] == "solar_p10,load_p50"
    assert f["limit"] == "energy" and f["fixable"] == "yes"
    assert "comfort" in f["at_risk"]  # occupied slots during the outage
    assert "grid unavailable (outage)" in f["detail"]
    replay = agent.replay(plan, forecast, signals, assets)
    assert set(replay.short_slots()) <= set(STUB_OUTAGE)
    # Margin = missing stored energy: unserved kWh / one-way efficiency, well below the battery window.
    expected = replay.unserved_kwh / assets.battery.one_way_efficiency
    assert report.reserve_margin_kwh == pytest.approx(expected)
    assert 0 < report.reserve_margin_kwh < assets.battery.soc_max_kwh - assets.battery.soc_min_kwh
    assert f["reserve_kwh"] == f"0->{report.reserve_margin_kwh:g}"
    assert float(f["shortfall_kwh"]) == pytest.approx(replay.unserved_kwh, abs=0.05)


def test_cloudy_day_is_fixed_by_run_plan_replan():
    result = _run(StubCloudyForecastAgent(), StubSignalAgent())
    assert result.risk.ok is True
    assert result.risk.replans >= 1
    assert result.reserve_margin_kwh > 0
    assert is_safe(result) and result.status == "optimal"
    assert not _parsed(result.risk, "fail")
    # The load-P90 replay is reported as an advisory note only.
    notes = _parsed(result.risk, "note")
    assert [n["check"] for n in notes] == ["advisory_unserved"]
    assert notes[0]["scenario"] == "solar_p10,load_p90"
    assert "does not fail the plan" in notes[0]["detail"]


def test_outage_heavy_day_detects_outage_shortfall_and_never_uses_grid_in_outage():
    sig_agent = OutageSignals(68, 84)  # 17:00-21:00, after sunset and during the evening shift
    forecast, signals, assets = _inputs(StubForecastAgent(), sig_agent)
    plan = _plan(forecast, signals, assets)
    agent = P10RiskAgent()
    report = agent.check(plan, forecast, signals, assets)
    assert report.ok is False
    replay = agent.replay(plan, forecast, signals, assets)
    short = replay.short_slots()
    assert short and all(signals.outage[t] for t in short)
    for t in range(n_slots()):
        if signals.outage[t]:
            assert replay.grid_kw[t] == 0.0
            assert replay.grid_extra_kw[t] == 0.0
    result = _run(StubForecastAgent(), sig_agent)
    assert result.risk.ok is True and result.risk.replans >= 1 and is_safe(result)


@pytest.mark.parametrize("load", ["p10", "p50", "p90"])
@pytest.mark.parametrize("cloudy", [False, True])
@pytest.mark.parametrize("reserve", [0.0, 150.0])
def test_replay_never_imports_grid_in_outage_and_respects_limits(load, cloudy, reserve):
    fa = StubCloudyForecastAgent() if cloudy else StubForecastAgent()
    forecast, signals, assets = _inputs(fa, OutageSignals(50, 66))
    plan = _plan(forecast, signals, assets, reserve)
    replay = stress_replay(plan, forecast, signals, assets, "p10", load)
    b, gen = assets.battery, assets.generator
    for t in range(n_slots()):
        assert replay.grid_kw[t] <= assets.grid.max_import_kw + 1e-6
        if signals.outage[t]:
            assert replay.grid_kw[t] == 0.0
        assert b.soc_min_kwh - 1e-6 <= replay.soc_kwh[t] <= b.soc_max_kwh + 1e-6
        assert replay.battery_discharge_kw[t] <= b.max_discharge_kw + 1e-6
        assert replay.battery_charge_kw[t] <= b.max_charge_kw + 1e-6
        g = replay.generator_kw[t]
        assert g == 0.0 or gen.min_kw - 1e-6 <= g <= gen.rated_kw + 1e-6


def test_power_limited_shortfall_is_not_fixable_and_keeps_the_margin():
    forecast, signals, assets = _inputs(StubCloudyForecastAgent())
    plan = _plan(forecast, signals, assets, reserve=200.0)
    report = P10RiskAgent(load="p90").check(plan, forecast, signals, assets)
    assert report.ok is False
    fails = _parsed(report, "fail")
    assert [f["limit"] for f in fails] == ["power"]
    assert fails[0]["fixable"] == "no"
    assert report.reserve_margin_kwh == plan.reserve_margin_kwh


def test_capacity_limited_shortfall_is_not_fixable_and_keeps_the_margin():
    # Cloudy day, 2-hour midday outage: the plan already enters the outage with a full battery (soc_max),
    # so no reserve can add the missing energy. Escalating the reserve would only make the Planner infeasible.
    forecast, signals, assets = _inputs(StubCloudyForecastAgent(), OutageSignals(52, 60))
    plan = _plan(forecast, signals, assets)
    assert is_safe(plan)
    assert plan.schedule.battery_soc_kwh[51] == pytest.approx(assets.battery.soc_max_kwh, abs=1e-3)
    report = P10RiskAgent().check(plan, forecast, signals, assets)
    assert report.ok is False
    (f,) = _parsed(report, "fail")
    assert f["limit"] == "capacity" and f["fixable"] == "no"
    assert "more reserve cannot fix this" in f["detail"]
    assert report.reserve_margin_kwh == plan.reserve_margin_kwh


def test_generator_started_by_operator_where_plan_had_it_off():
    forecast, signals, assets = _inputs(StubForecastAgent(), OutageSignals(80, 84))
    plan = _plan(forecast, signals, assets)
    replay = P10RiskAgent(load="p90").replay(plan, forecast, signals, assets)
    started = [
        t for t in range(n_slots()) if plan.schedule.generator_kw[t] == 0 and replay.generator_kw[t] > 0
    ]
    assert started  # B: the operator may start the generator
    assert all(replay.generator_kw[t] >= assets.generator.min_kw - 1e-6 for t in started)


def test_end_of_day_soc_short_is_a_note_not_a_failure():
    # Late outage, gating at load P90: the operator drains the battery and cannot recharge before midnight.
    forecast, signals, assets = _inputs(StubForecastAgent(), OutageSignals(88, 96))
    plan = _plan(forecast, signals, assets)
    report = P10RiskAgent(load="p90").check(plan, forecast, signals, assets)
    notes = [n for n in _parsed(report, "note") if n["check"] == "end_soc_short"]
    assert len(notes) == 1
    assert "does not fail the plan" in notes[0]["detail"]
    assert report.ok is (not _parsed(report, "fail"))


# ---------------------------------------------------------------- unsafe and errors


def test_unsafe_plan_fails_quoting_marker_without_pretending_reserve_helps():
    forecast, signals, assets = _inputs(StubCloudyForecastAgent(), OutageSignals(48, 68))
    plan = _plan(forecast, signals, assets, reserve=75.0)
    assert not is_safe(plan)
    report = P10RiskAgent().check(plan, forecast, signals, assets)
    assert report.ok is False
    (f,) = _parsed(report)
    assert f["check"] == "unsafe_plan" and f["fixable"] == "no"
    assert UNSAFE_PREFIX in f["detail"]
    assert report.reserve_margin_kwh == plan.reserve_margin_kwh == 75.0
    assert f["reserve_kwh"] == "75->75"


def test_unexpected_error_becomes_a_failure_not_an_exception(monkeypatch):
    forecast, signals, assets = _inputs()
    plan = _plan(forecast, signals, assets, reserve=20.0)

    def boom(*a, **k):
        raise RuntimeError("bad; plan")

    monkeypatch.setattr(risk_agent_mod, "stress_replay", boom)
    report = P10RiskAgent().check(plan, forecast, signals, assets)
    assert report.ok is False and report.reserve_margin_kwh == 20.0
    (f,) = _parsed(report)
    assert f["check"] == "replay_error" and "RuntimeError" in f["detail"]


# ---------------------------------------------------------------- format and purity


def test_reasons_round_trip_through_parse_helper():
    forecast, signals, assets = _inputs(StubCloudyForecastAgent())
    plan = _plan(forecast, signals, assets)
    report = P10RiskAgent().check(plan, forecast, signals, assets)
    assert report.reasons
    for line in report.reasons:
        fields = parse_reason(line)
        kind = fields.pop("kind")
        assert format_reason(fields, fail=kind == "fail") == line
        assert "\n" not in line
    fields = {"check": "unserved_energy", "slots": "1-3,7", "detail": "a; b = c"}
    parsed = parse_reason(format_reason(fields, fail=False))
    assert parsed["kind"] == "note" and parsed["slots"] == "1-3,7" and parsed["detail"] == "a, b = c"
    assert parse_reason(None) is None and parse_reason("FORECAST FALLBACK: x=1") is None


def test_check_does_not_modify_inputs_and_is_repeatable():
    forecast, signals, assets = _inputs(StubCloudyForecastAgent())
    plan = _plan(forecast, signals, assets)
    before = [m.model_dump_json() for m in (plan, forecast, signals, assets)]
    agent = P10RiskAgent()
    first = agent.check(plan, forecast, signals, assets)
    second = agent.check(plan, forecast, signals, assets)
    assert first == second
    assert [m.model_dump_json() for m in (plan, forecast, signals, assets)] == before


# ---------------------------------------------------------------- multi-day share (issue "Done when")


@pytest.fixture(scope="module")
def sample_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("risk_data")
    shutil.copytree(SAMPLE_DIR, out / "sample")
    return out


def test_share_of_days_feasible_at_p10_after_replan(sample_dir):
    """Every day the Planner can solve at P50 is feasible at solar P10 (gating scenario) after re-plan."""
    model = ModelForecastAgent(data_dir=sample_dir, models_dir=sample_dir / "models")
    calendar = CalendarSignalAgent(data_dir=sample_dir)
    days = [
        ("stub sunny", StubForecastAgent(), StubSignalAgent(), START),
        ("stub cloudy", StubCloudyForecastAgent(), StubSignalAgent(), START),
        ("stub sunny, 17-21 outage", StubForecastAgent(), OutageSignals(68, 84), START),
        ("stub cloudy, 13:00-13:45 outage", StubCloudyForecastAgent(), OutageSignals(52, 55), START),
        ("stub cloudy, 06-08 outage", StubCloudyForecastAgent(), OutageSignals(24, 32), START),
        ("sample 2024-06-03", model, calendar, datetime(2024, 6, 3, tzinfo=DHAKA)),
        ("sample 2024-06-05", model, calendar, datetime(2024, 6, 5, tzinfo=DHAKA)),
    ]
    solvable, feasible, replanned = 0, 0, 0
    for name, fa, sa, day in days:
        result = _run(fa, sa, start=day, assets=AssetAgent())
        assert result.risk.reserve_margin_kwh >= 0
        if result.risk.replans == 0 and not is_safe(result):
            continue  # infeasible even at P50: not a Risk failure
        solvable += 1
        ok = result.risk.ok and is_safe(result)
        assert ok, f"{name}: {result.risk.reasons}"
        feasible += ok
        replanned += result.risk.replans > 0
    assert solvable == len(days)
    assert feasible / solvable == 1.0
    assert replanned >= 2  # the Risk agent actually changed some plans


def test_run_plan_end_to_end_with_real_agents(sample_dir):
    day = datetime(2024, 6, 3, tzinfo=DHAKA)  # calendar outage 19:30-20:45
    signals = CalendarSignalAgent(data_dir=sample_dir)
    agents = build_default_agents(
        forecast_agent=ModelForecastAgent(data_dir=sample_dir, models_dir=sample_dir / "models"),
        signal_agent=signals,
    )
    assert isinstance(agents["risk"], P10RiskAgent)
    result = run_plan(day, 0.5, **agents)
    assert result.risk is not None and result.risk.ok is True
    assert is_safe(result) and result.status in ("optimal", "feasible")
    outage = signals.run(day).outage
    assert any(outage)
    assert all(
        result.schedule.grid_import_kw[t] == pytest.approx(0.0, abs=1e-9)
        for t in range(n_slots())
        if outage[t]
    )
    result.schedule.check_energy_balance(tol=1e-6)
