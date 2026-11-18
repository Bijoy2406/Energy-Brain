"""What the Explainer reads (#19): one planning run plus the agent outputs behind it.

``PlanResult`` alone does not hold the forecast, signals or asset limits, and ``orchestrator.run_plan`` does
not return them, so :meth:`PlanContext.from_agents` runs the input agents once and passes their outputs to
``run_plan`` through fixed wrappers (the orchestrator is unchanged). The Signal agent's fallback note, which
does not reach ``PlanResult.notes`` (known gap, ``docs/interfaces.md``), is kept here.

Nothing is read at import. :func:`load_backtest` reads ``reports/backtest.json`` only when called.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from energy_brain.orchestrator import run_plan
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, SignalTable

DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_BACKTEST_PATH = DEFAULT_ROOT_DIR / "reports" / "backtest.json"


@dataclass(frozen=True)
class PlanContext:
    """Read-only inputs of the Explainer tools. ``backtest``: parsed ``reports/backtest.json`` or None."""

    plan: PlanResult
    forecast: Forecast
    signals: SignalTable
    assets: AssetConstraints
    signal_note: str | None = None
    backtest: dict[str, Any] | None = None
    #: Gating stress scenario of the Risk agent, e.g. ``solar_p10,load_p50`` (None if unknown).
    risk_scenario: str | None = None

    @classmethod
    def from_agents(
        cls,
        start: datetime,
        weight: float,
        *,
        forecast_agent: Any,
        signal_agent: Any,
        assets: Any,
        planner: Any,
        risk: Any,
        backtest: dict[str, Any] | None = None,
        **run_plan_kwargs: Any,
    ) -> PlanContext:
        """Run the agents once, plan with ``run_plan`` on those exact outputs, and keep everything.

        Takes the same keywords as ``run_plan`` (e.g. ``**build_default_agents()``).
        """
        forecast = forecast_agent.run(start)
        if hasattr(signal_agent, "run_with_note"):
            signals, signal_note = signal_agent.run_with_note(start)
        else:
            signals, signal_note = signal_agent.run(start), None
        constraints = assets.run(start)
        plan = run_plan(
            start,
            weight,
            forecast_agent=_Fixed(forecast),
            signal_agent=_Fixed(signals),
            assets=_Fixed(constraints),
            planner=planner,
            risk=risk,
            **run_plan_kwargs,
        )
        solar_q, load_q = getattr(risk, "solar", None), getattr(risk, "load", None)
        scenario = f"solar_{solar_q},load_{load_q}" if solar_q and load_q else None
        return cls(plan, forecast, signals, constraints, signal_note, backtest, scenario)


class _Fixed:
    """Agent stand-in that returns one precomputed output (so each agent runs exactly once)."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def run(self, start: datetime) -> Any:
        return self.value


def load_backtest(path: str | Path | None = None) -> dict[str, Any] | None:
    """Parsed backtest report, or None if the file does not exist."""
    p = Path(path) if path is not None else DEFAULT_BACKTEST_PATH
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
