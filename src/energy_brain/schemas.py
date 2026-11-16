"""Typed messages passed between agents (issue #13). THIS IS THE CONTRACT BETWEEN ALL AGENTS.

Tell the team before changing a field: the data/forecast side and the planner/risk/API side both code
against these models. Human-readable version: ``docs/interfaces.md``.

Conventions
-----------
* Time axis: ``energy_brain.timeaxis``. One day = ``n_slots()`` slots (96 x 15 min by default). Slot 0
  starts at ``start``, a tz-aware datetime in Asia/Dhaka on a slot boundary.
* Every per-slot series is a plain ``list[float]`` (or ``list[bool]``) of exactly ``n_slots()`` items.
* Units are in field names: ``_kw`` (average power over the slot), ``_kwh``, ``_bdt``, ``_kg``, ``_l``,
  ``_pct``. Energy per slot = kW x ``slot_hours()``.
* All models forbid unknown fields, reject NaN/inf, are frozen, and round-trip through
  ``model_dump_json`` / ``model_validate_json``.
* The optimiser (Planner) decides every number in a ``Schedule``; the LLM Explainer only reads them.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, field_validator, model_validator

from energy_brain.timeaxis import ensure_start, n_slots, slot_hours

#: Absolute tolerance (kW or kWh) used for ordering checks on floating-point series.
EPS = 1e-9

PlanStatus = Literal["optimal", "feasible", "infeasible", "fallback"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class _SlotModel(_Model):
    """Base for models holding per-slot series: every ``list`` field must have ``n_slots()`` items."""

    @model_validator(mode="after")
    def _series_length(self):
        n = n_slots()
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, list) and len(value) != n:
                raise ValueError(f"{type(self).__name__}.{name} has {len(value)} slots, expected {n}")
        return self


class _Dated(_Model):
    """Mixin: ``start`` must be tz-aware and on a slot boundary; stored in the site time zone."""

    start: datetime

    @field_validator("start")
    @classmethod
    def _aware_start(cls, v: datetime) -> datetime:
        return ensure_start(v)


# --------------------------------------------------------------------------- forecast


class QuantileSeries(_SlotModel):
    """P10/P50/P90 power forecast in kW per slot. Requires 0 <= p10 <= p50 <= p90 in every slot."""

    p10_kw: list[NonNegativeFloat]
    p50_kw: list[NonNegativeFloat]
    p90_kw: list[NonNegativeFloat]

    @model_validator(mode="after")
    def _ordered(self) -> QuantileSeries:
        for i, (a, b, c) in enumerate(zip(self.p10_kw, self.p50_kw, self.p90_kw, strict=True)):
            if a > b + EPS or b > c + EPS:
                raise ValueError(f"quantiles out of order at slot {i}: p10={a}, p50={b}, p90={c}")
        return self


class Forecast(_Dated):
    """Forecast agent output (#14) for one planning day.

    ``solar``: PV AC output in kW. ``load``: INFLEXIBLE base load in kW, i.e. site demand EXCLUDING the
    HVAC and EV charging that the Planner schedules. ``fallback_used`` is True when a persistence /
    backup forecast replaced the model (PRD section 8); explain why in ``note``.
    """

    solar: QuantileSeries
    load: QuantileSeries
    fallback_used: bool = False
    note: str | None = None


# --------------------------------------------------------------------------- signals


class SignalTable(_Dated, _SlotModel):
    """Signal agent output (#15): prices, emission factors and outages per slot.

    ``outage[t]`` is True when the grid is UNAVAILABLE in slot t (load-shedding): grid import must be 0.
    """

    tariff_bdt_per_kwh: list[NonNegativeFloat]
    grid_co2_kg_per_kwh: list[NonNegativeFloat]
    diesel_price_bdt_per_l: list[NonNegativeFloat]
    diesel_co2_kg_per_l: list[NonNegativeFloat]
    outage: list[bool]


# --------------------------------------------------------------------------- asset constraints
# Shapes only. The mapping from SiteConfig to these values is issue #16.


class GridConstraints(_Model):
    """Grid connection limit. Export is not modelled (no net metering in the MVP)."""

    max_import_kw: float = Field(gt=0)


class BatteryConstraints(_Model):
    """Battery limits. Charge/discharge kW are measured at the AC side.

    Hard constraints: soc_min_kwh <= SoC <= soc_max_kwh in every slot; charge <= max_charge_kw;
    discharge <= max_discharge_kw. SoC update (convention for the Planner):
    ``soc[t] = soc[t-1] + eta * charge[t] * h - discharge[t] * h / eta`` with ``eta = sqrt(rte)``,
    ``soc[-1] = initial_soc_kwh`` and ``h = slot_hours()``.
    """

    capacity_kwh: float = Field(gt=0)
    max_charge_kw: float = Field(gt=0)
    max_discharge_kw: float = Field(gt=0)
    round_trip_efficiency: float = Field(gt=0, le=1)
    soc_min_kwh: float = Field(ge=0)
    soc_max_kwh: float = Field(ge=0)
    initial_soc_kwh: float = Field(ge=0)

    @model_validator(mode="after")
    def _soc_order(self) -> BatteryConstraints:
        if not self.soc_min_kwh <= self.initial_soc_kwh <= self.soc_max_kwh <= self.capacity_kwh:
            raise ValueError("need soc_min_kwh <= initial_soc_kwh <= soc_max_kwh <= capacity_kwh")
        return self

    @property
    def one_way_efficiency(self) -> float:
        """Charge (and discharge) efficiency, sqrt(round_trip_efficiency)."""
        return math.sqrt(self.round_trip_efficiency)


class HvacConstraints(_SlotModel):
    """HVAC (the main shiftable load). ``occupied[t]`` True = workers on the floor in slot t.

    Hard constraints: 0 <= hvac_kw <= rated_kw; hvac_kw >= min_kw_when_occupied whenever occupied; the
    comfort band [comfort_min_c, comfort_max_c] is never traded for savings. Cooling may be shifted at most
    ``max_precool_hours`` earlier than needed.
    """

    rated_kw: float = Field(gt=0)
    min_kw_when_occupied: float = Field(ge=0)
    occupied: list[bool]
    comfort_min_c: float
    comfort_max_c: float
    max_precool_hours: float = Field(ge=0)

    @model_validator(mode="after")
    def _ranges(self) -> HvacConstraints:
        if self.min_kw_when_occupied > self.rated_kw:
            raise ValueError("min_kw_when_occupied must not exceed rated_kw")
        if not self.comfort_min_c < self.comfort_max_c:
            raise ValueError("comfort_min_c must be below comfort_max_c")
        return self


class EvConstraints(_SlotModel):
    """EV fleet charging, aggregated over all vehicles.

    ``plugged_in[t]`` True = vehicles parked and chargeable in slot t. ``charger_kw`` = total charger power
    of the fleet. Hard constraints: ev_charge_kw = 0 when not plugged in; ev_charge_kw <= charger_kw; total
    delivered energy >= ``energy_required_kwh`` by ``departure_slot`` (departure charge is never traded).
    ``arrival_slot`` / ``departure_slot`` are slot indices in [0, n_slots); the window may wrap midnight.
    """

    plugged_in: list[bool]
    charger_kw: float = Field(ge=0)
    energy_required_kwh: float = Field(ge=0)
    arrival_slot: int = Field(ge=0)
    departure_slot: int = Field(ge=0)

    @model_validator(mode="after")
    def _feasible(self) -> EvConstraints:
        n = n_slots()
        if self.arrival_slot >= n or self.departure_slot >= n:
            raise ValueError(f"arrival_slot and departure_slot must be < {n}")
        max_kwh = self.charger_kw * sum(self.plugged_in) * slot_hours()
        if self.energy_required_kwh > max_kwh + EPS:
            raise ValueError(f"energy_required_kwh={self.energy_required_kwh} exceeds deliverable {max_kwh}")
        return self


class GeneratorConstraints(_Model):
    """Diesel generator. When running, min_kw <= generator_kw <= rated_kw; otherwise 0.

    Fuel use = generator_kwh x fuel_l_per_kwh (cost and CO2 via the diesel signals).
    """

    rated_kw: float = Field(gt=0)
    min_kw: float = Field(ge=0)
    fuel_l_per_kwh: float = Field(gt=0)

    @model_validator(mode="after")
    def _min_le_rated(self) -> GeneratorConstraints:
        if self.min_kw > self.rated_kw:
            raise ValueError("min_kw must not exceed rated_kw")
        return self


class AssetConstraints(_Dated):
    """Asset agents output (#16): all hard limits the Planner must respect for the day at ``start``."""

    grid: GridConstraints
    battery: BatteryConstraints
    hvac: HvacConstraints
    ev: EvConstraints
    generator: GeneratorConstraints


# --------------------------------------------------------------------------- plan


class Schedule(_SlotModel):
    """Planner output: power flows per slot, all in kW (averaged over the slot) and >= 0.

    Energy balance (must hold in every slot)::

        solar_used + grid_import + battery_discharge + generator
            == base_load + hvac + ev_charge + battery_charge

    ``solar_used + solar_curtailed`` = available solar. ``battery_soc_kwh[t]`` is the SoC at the END of
    slot t (see ``BatteryConstraints`` for the update rule). ``base_load_kw`` is the inflexible load the
    plan was built for (normally the Forecast load P50).
    """

    grid_import_kw: list[NonNegativeFloat]
    solar_used_kw: list[NonNegativeFloat]
    solar_curtailed_kw: list[NonNegativeFloat]
    battery_charge_kw: list[NonNegativeFloat]
    battery_discharge_kw: list[NonNegativeFloat]
    battery_soc_kwh: list[NonNegativeFloat]
    hvac_kw: list[NonNegativeFloat]
    ev_charge_kw: list[NonNegativeFloat]
    generator_kw: list[NonNegativeFloat]
    base_load_kw: list[NonNegativeFloat]

    def energy_balance_residual_kw(self) -> list[float]:
        """Supply minus demand per slot in kW (0 when balanced)."""
        return [
            (s + g + bd + gen) - (bl + hv + ev + bc)
            for s, g, bd, gen, bl, hv, ev, bc in zip(
                self.solar_used_kw,
                self.grid_import_kw,
                self.battery_discharge_kw,
                self.generator_kw,
                self.base_load_kw,
                self.hvac_kw,
                self.ev_charge_kw,
                self.battery_charge_kw,
                strict=True,
            )
        ]

    def check_energy_balance(self, tol: float = 1e-6) -> float:
        """Return the max absolute energy-balance violation in kW; raise ``ValueError`` if above ``tol``."""
        residual = self.energy_balance_residual_kw()
        worst = max(abs(r) for r in residual)
        if worst > tol:
            slot = max(range(len(residual)), key=lambda i: abs(residual[i]))
            raise ValueError(f"energy balance violated by {worst:.6g} kW at slot {slot} (tol {tol})")
        return worst


class Totals(_SlotModel):
    """Day totals and per-slot cost/CO2. Always produced by ``energy_brain.accounting.compute_totals``."""

    cost_bdt: NonNegativeFloat
    co2_kg: NonNegativeFloat
    grid_kwh: NonNegativeFloat
    solar_kwh: NonNegativeFloat  # solar actually used (curtailed solar excluded)
    generator_kwh: NonNegativeFloat
    generator_hours: NonNegativeFloat
    diesel_litres: NonNegativeFloat
    slot_cost_bdt: list[NonNegativeFloat]
    slot_co2_kg: list[NonNegativeFloat]


class RiskReport(_Model):
    """Risk agent output (#18).

    ``ok`` False = plan fails under pessimistic (P10 solar / P90 load) conditions; ``reasons`` say why.
    ``reserve_margin_kwh`` = battery reserve the Risk agent asks the Planner to keep on the next re-plan.
    ``replans`` = number of re-plans the orchestrator ran before this report (filled by the orchestrator).
    """

    ok: bool
    reasons: list[str] = Field(default_factory=list)
    reserve_margin_kwh: NonNegativeFloat = 0.0
    replans: int = Field(default=0, ge=0)


class PlanResult(_Dated):
    """Final output of one planning run (what the API and dashboard show).

    ``weight`` w in [0, 1]: the Planner minimises ``w * cost + (1 - w) * CO2``; 1 = cheapest, 0 = cleanest.
    ``reserve_margin_kwh`` is the battery reserve this plan was solved with.
    """

    weight: float = Field(ge=0, le=1)
    schedule: Schedule
    totals: Totals
    risk: RiskReport | None = None
    status: PlanStatus
    solve_seconds: NonNegativeFloat = 0.0
    reserve_margin_kwh: NonNegativeFloat = 0.0
    fallback_used: bool = False
    notes: list[str] = Field(default_factory=list)
