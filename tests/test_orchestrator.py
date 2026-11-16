"""Orchestrator end-to-end with stub agents (issue #13)."""

import logging
from datetime import datetime, timedelta

import pytest

from energy_brain.orchestrator import AlignmentError, run_plan
from energy_brain.schemas import PlanResult, RiskReport, SignalTable
from energy_brain.stubs import (
    FailOnceRisk,
    StubAssetProvider,
    StubForecastAgent,
    StubPlanner,
    StubRisk,
    StubSignalAgent,
)


class RecordingPlanner(StubPlanner):
    """Stub planner that records the reserve margin of every call; can report ``status``."""

    def __init__(self, status: str | None = None) -> None:
        self.margins: list[float] = []
        self.status = status

    def plan(self, forecast, signals, assets, weight, reserve_margin_kwh=0.0):
        self.margins.append(reserve_margin_kwh)
        result = super().plan(forecast, signals, assets, weight, reserve_margin_kwh)
        return result.model_copy(update={"status": self.status}) if self.status else result


class AlwaysFailRisk:
    def check(self, plan, forecast, signals, assets):
        return RiskReport(ok=False, reasons=["always fails"], reserve_margin_kwh=10.0)


def _run(start, **overrides) -> PlanResult:
    kwargs = {
        "forecast_agent": StubForecastAgent(),
        "signal_agent": StubSignalAgent(),
        "assets": StubAssetProvider(),
        "planner": StubPlanner(),
        "risk": StubRisk(),
    }
    kwargs.update(overrides)
    return run_plan(start, 0.5, **kwargs)


def test_end_to_end(start):
    result = _run(start)
    assert isinstance(result, PlanResult)
    assert result.start == start
    assert result.risk is not None and result.risk.ok and result.risk.replans == 0
    assert result.weight == 0.5
    result.schedule.check_energy_balance(tol=1e-9)
    assert PlanResult.model_validate_json(result.model_dump_json()) == result


def test_fail_once_triggers_one_replan(start, caplog):
    planner = RecordingPlanner()
    with caplog.at_level(logging.INFO, logger="energy_brain.orchestrator"):
        result = _run(start, planner=planner, risk=FailOnceRisk(margin_kwh=100.0))
    assert result.risk.ok and result.risk.replans == 1
    assert planner.margins == [0.0, 100.0]  # margin raised on the re-plan
    assert result.reserve_margin_kwh == 100.0
    assert "re-plan 1/3" in caplog.text


def test_always_failing_risk_stops_at_max_replans(start):
    planner = RecordingPlanner(status="optimal")
    result = _run(start, planner=planner, risk=AlwaysFailRisk(), max_replans=3)
    assert len(planner.margins) == 4  # first plan + 3 re-plans
    assert all(b > a for a, b in zip(planner.margins, planner.margins[1:], strict=False))
    assert result.risk.ok is False and result.risk.replans == 3
    assert result.status == "feasible"  # downgraded from optimal
    assert "always fails" in result.notes


def test_zero_replans_allowed(start):
    result = _run(start, risk=AlwaysFailRisk(), max_replans=0)
    assert result.risk.replans == 0 and not result.risk.ok


class ShiftedForecastAgent(StubForecastAgent):
    def run(self, start):
        return super().run(start + timedelta(days=1))


class ShortSignalAgent(StubSignalAgent):
    def run(self, start):
        good = super().run(start)
        data = {k: v[:-1] if isinstance(v, list) else v for k, v in dict(good).items()}
        return SignalTable.model_construct(**data)  # bypasses validation, like a buggy agent


def test_mismatched_start_raises(start):
    with pytest.raises(AlignmentError, match="Forecast.start"):
        _run(start, forecast_agent=ShiftedForecastAgent())


def test_mismatched_length_raises(start):
    with pytest.raises(AlignmentError, match="SignalTable has 95 slots, expected 96"):
        _run(start, signal_agent=ShortSignalAgent())


def test_bad_arguments_raise(start):
    with pytest.raises(ValueError, match="timezone-aware"):
        _run(datetime(2026, 11, 17))
    with pytest.raises(ValueError, match="weight"):
        run_plan(
            start,
            1.2,
            forecast_agent=StubForecastAgent(),
            signal_agent=StubSignalAgent(),
            assets=StubAssetProvider(),
            planner=StubPlanner(),
            risk=StubRisk(),
        )
