"""Agent interfaces (issue #13): ``typing.Protocol`` classes only, no logic.

Any object with matching methods satisfies a Protocol (no inheritance needed). Real agents live in their
own packages (#14-#18); placeholders are in ``energy_brain.stubs``. Schemas: ``energy_brain.schemas``.
All series have ``n_slots()`` items on the time axis of ``energy_brain.timeaxis`` (Asia/Dhaka, 15 min).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from energy_brain.planner.fixes import SlotFixes
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, RiskReport, SignalTable


@runtime_checkable
class ForecastAgent(Protocol):
    """Forecast agent (#14)."""

    def run(self, start: datetime) -> Forecast:
        """P10/P50/P90 solar (kW AC) and inflexible base load (kW, excl. HVAC + EV) for the day at ``start``.

        ``start`` is tz-aware (Asia/Dhaka) and the returned ``Forecast.start`` must equal it. On model or
        data failure return a persistence forecast with ``fallback_used=True`` instead of raising.
        """
        ...


@runtime_checkable
class SignalAgent(Protocol):
    """Signal agent (#15)."""

    def run(self, start: datetime) -> SignalTable:
        """Per-slot tariff (BDT/kWh), grid CO2 (kg/kWh), diesel price (BDT/L), diesel CO2 (kg/L), outages.

        ``outage[t]`` True = grid unavailable. ``SignalTable.start`` must equal ``start``.
        """
        ...


@runtime_checkable
class AssetProvider(Protocol):
    """Asset agents (#16), aggregated."""

    def run(self, start: datetime) -> AssetConstraints:
        """Hard limits of grid, battery, HVAC, EV fleet and generator for the day at ``start``."""
        ...


@runtime_checkable
class PlannerAgent(Protocol):
    """Planner agent (#17): deterministic MILP. It decides every number; the LLM never does."""

    def plan(
        self,
        forecast: Forecast,
        signals: SignalTable,
        assets: AssetConstraints,
        weight: float,
        reserve_margin_kwh: float = 0.0,
        fixes: SlotFixes | None = None,
    ) -> PlanResult:
        """Minimise ``weight * cost_bdt + (1 - weight) * co2_kg`` (weight 1 = cheapest, 0 = cleanest).

        The Planner must NOT change or relax (hard constraints):
          * the energy balance in every slot (``Schedule.check_energy_balance``);
          * grid_import_kw <= grid.max_import_kw, and grid_import_kw = 0 when ``signals.outage`` is True;
          * battery SoC within [soc_min_kwh + reserve_margin_kwh, soc_max_kwh] (reserve capped at soc_max),
            charge/discharge power limits;
          * HVAC limits and comfort band, min_kw_when_occupied while occupied;
          * EV departure energy, charging only while plugged in, charger_kw limit;
          * generator 0 or within [min_kw, rated_kw];
          * the inputs themselves (forecast, signals, assets) — it may not edit them.
        Totals must come from ``energy_brain.accounting.compute_totals``. If no feasible plan exists, return a
        ``PlanResult`` with ``status="infeasible"`` or ``"fallback"`` rather than raising.
        """
        ...


@runtime_checkable
class RiskAgent(Protocol):
    """Risk agent (#18)."""

    def check(
        self,
        plan: PlanResult,
        forecast: Forecast,
        signals: SignalTable,
        assets: AssetConstraints,
    ) -> RiskReport:
        """Re-check ``plan`` under pessimistic conditions (default P10 solar, P50 load; docs/interfaces.md).

        Return ``ok=False`` with human-readable ``reasons`` and a larger ``reserve_margin_kwh``
        (kWh of battery to hold back) when hard constraints would break. Must not modify the plan.
        """
        ...
