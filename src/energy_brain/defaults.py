"""Default agent wiring for ``orchestrator.run_plan`` (issue #14).

``run_plan(start, weight, **build_default_agents())`` runs the real Forecast agent (#14), the real Asset
agent (#16) and the MILP Planner (#17). Signal (#15) and Risk (#18) are still the stubs until those issues
land; swap them here, not in the orchestrator.
"""

from __future__ import annotations

from typing import Any

from energy_brain.assets import AssetAgent
from energy_brain.config import SiteConfig, load_site_config
from energy_brain.forecast import ModelForecastAgent
from energy_brain.planner import MilpPlanner
from energy_brain.stubs import StubRisk, StubSignalAgent


def build_default_agents(config: SiteConfig | None = None, **overrides: Any) -> dict[str, Any]:
    """Keyword arguments for ``run_plan``: real Forecast, Asset and Planner agents; stub Signal and Risk.

    Any of ``forecast_agent``, ``signal_agent``, ``assets``, ``planner``, ``risk`` can be overridden.
    """
    cfg = config if config is not None else load_site_config()
    agents: dict[str, Any] = {
        "forecast_agent": ModelForecastAgent(config=cfg),
        "signal_agent": StubSignalAgent(),  # STUB until #15
        "assets": AssetAgent(cfg),
        "planner": MilpPlanner(),
        "risk": StubRisk(),  # STUB until #18
    }
    unknown = set(overrides) - set(agents)
    if unknown:
        raise TypeError(f"unknown agent override(s): {sorted(unknown)}")
    agents.update(overrides)
    return agents
