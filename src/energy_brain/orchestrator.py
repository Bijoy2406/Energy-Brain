"""Agent orchestrator (issue #13).

Order (PRD section 5): Forecast, Signal and Asset agents -> Planner -> Risk. If Risk rejects the plan, the
Planner re-solves with a larger battery reserve, up to ``max_replans`` times. The optimiser decides every
number; the LLM Explainer (#19) only reads the returned ``PlanResult``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from energy_brain.interfaces import AssetProvider, ForecastAgent, PlannerAgent, RiskAgent, SignalAgent
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, SignalTable
from energy_brain.timeaxis import ensure_start, n_slots

logger = logging.getLogger(__name__)

#: Minimum increase of the battery reserve (kWh) between re-plans if the Risk agent asks for less.
DEFAULT_MARGIN_STEP_KWH = 50.0


class AlignmentError(ValueError):
    """Agent outputs do not share the same ``start`` or number of slots."""


def _check(name: str, got_start: datetime, length: int, start: datetime, n: int) -> None:
    if got_start != start:
        raise AlignmentError(f"{name}.start is {got_start.isoformat()}, expected {start.isoformat()}")
    if length != n:
        raise AlignmentError(f"{name} has {length} slots, expected {n}")


def check_inputs(start: datetime, forecast: Forecast, signals: SignalTable, assets: AssetConstraints) -> None:
    """Raise ``AlignmentError`` unless all agent outputs share ``start`` and have ``n_slots()`` slots."""
    n = n_slots()
    _check("Forecast", forecast.start, len(forecast.solar.p50_kw), start, n)
    _check("Forecast.load", forecast.start, len(forecast.load.p50_kw), start, n)
    _check("SignalTable", signals.start, len(signals.tariff_bdt_per_kwh), start, n)
    _check("AssetConstraints", assets.start, len(assets.hvac.occupied), start, n)


def _check_plan(plan: PlanResult, start: datetime) -> None:
    _check("PlanResult", plan.start, len(plan.schedule.grid_import_kw), start, n_slots())


def run_plan(
    start: datetime,
    weight: float,
    *,
    forecast_agent: ForecastAgent,
    signal_agent: SignalAgent,
    assets: AssetProvider,
    planner: PlannerAgent,
    risk: RiskAgent,
    max_replans: int = 3,
    margin_step_kwh: float = DEFAULT_MARGIN_STEP_KWH,
) -> PlanResult:
    """Run one day-ahead planning cycle and return a ``PlanResult`` with ``risk`` filled.

    ``weight`` in [0, 1] (1 = cheapest, 0 = cleanest). On each Risk rejection the reserve becomes
    ``max(report.reserve_margin_kwh, current + margin_step_kwh)`` kWh and the Planner re-solves. If the plan
    still fails after ``max_replans`` re-plans, the last plan is returned (never raises for this): an
    ``optimal`` status is downgraded to ``feasible`` and the Risk reasons are appended to ``notes``.

    Raises ``AlignmentError`` if agent outputs disagree on ``start`` or slot count, ``ValueError`` for a naive
    ``start`` or a weight outside [0, 1].
    """
    start = ensure_start(start)
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"weight must be in [0, 1], got {weight}")
    if max_replans < 0:
        raise ValueError("max_replans must be >= 0")

    forecast = forecast_agent.run(start)
    signals = signal_agent.run(start)
    constraints = assets.run(start)
    check_inputs(start, forecast, signals, constraints)
    if forecast.fallback_used:
        logger.warning("forecast fallback in use: %s", forecast.note)

    margin = 0.0
    replans = 0
    plan = planner.plan(forecast, signals, constraints, weight, reserve_margin_kwh=margin)
    _check_plan(plan, start)
    report = risk.check(plan, forecast, signals, constraints)
    logger.info(
        "plan status=%s cost=%.0f BDT co2=%.0f kg risk_ok=%s",
        plan.status,
        plan.totals.cost_bdt,
        plan.totals.co2_kg,
        report.ok,
    )

    while not report.ok and replans < max_replans:
        margin = max(report.reserve_margin_kwh, margin + margin_step_kwh)
        replans += 1
        logger.info(
            "risk rejected plan (%s); re-plan %d/%d with reserve %.1f kWh",
            "; ".join(report.reasons) or "no reason given",
            replans,
            max_replans,
            margin,
        )
        plan = planner.plan(forecast, signals, constraints, weight, reserve_margin_kwh=margin)
        _check_plan(plan, start)
        report = risk.check(plan, forecast, signals, constraints)

    update: dict = {"risk": report.model_copy(update={"replans": replans})}
    if not report.ok:
        logger.warning("risk still failing after %d re-plans; returning last plan", replans)
        if plan.status == "optimal":
            update["status"] = "feasible"
        update["notes"] = [
            *plan.notes,
            f"Risk check still failing after {replans} re-plans:",
            *report.reasons,
        ]
    return plan.model_copy(update=update)
