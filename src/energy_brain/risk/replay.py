"""Stress replay (#18): execute a fixed plan slot by slot under a stress scenario with a reactive operator.

The plan fixes every decision for the day. Under the stress scenario (default solar P10, load P50) some slots
have a gap between what the plan supplies and what the site needs. A reactive operator closes it in this
order, never touching HVAC or EV charging (hard constraints, see ``planner.rules``):

1. use solar the plan curtailed (only if the stress solar exceeds what the plan used);
2. cut the planned battery charge;
3. extra grid import up to ``grid.max_import_kw`` - planned import; **never in an outage slot**;
4. extra battery discharge up to ``max_discharge_kw`` and down to ``soc_min_kwh`` (the Risk reserve exists
   for this, so the raised Planner floor does not apply here);
5. generator: raise it to ``rated_kw``, or start it (at least ``min_kw``) where the plan had it off; the
   minimum-load surplus is absorbed by backing off discharge, grid and solar, then charging the battery.

What is still missing is unserved energy. The battery carries its actual (stressed) SoC from slot to slot, so
a planned discharge later in the day is capped by what is really stored (down to ``soc_min_kwh``); the missing
part joins the gap. In slots without an outage and with no gap left, spare grid capacity recharges the
battery back towards the planned SoC (never above it).

Pure function of its inputs; nothing is modified. Used by ``P10RiskAgent`` and reusable by #22 (pass an
"actual" forecast and ``solar="p50", load="p50"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from energy_brain.planner import rules
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, SignalTable
from energy_brain.timeaxis import slot_hours

Quantile = Literal["p10", "p50", "p90"]
QUANTILES: tuple[str, ...] = ("p10", "p50", "p90")

#: Tolerance (kW / kWh) below which a shortfall or a flow is treated as zero.
TOL = 1e-6


@dataclass(frozen=True)
class StressReplay:
    """Per-slot outcome of :func:`stress_replay` (all lists have one item per slot, kW unless noted).

    ``limit[t]``: why slot t is short: ``"energy"`` (battery hit ``soc_min_kwh`` before its power limit, so
    more stored energy would help), ``"power"`` (battery at ``max_discharge_kw``, generator at ``rated_kw``,
    grid unavailable or at its limit: more reserve cannot help), ``""`` when nothing is unserved.

    ``min_battery_kw[t]``: battery discharge that ANY dispatch with the plan's HVAC and EV would need in slot
    t at this scenario, ``max(0, load + hvac + ev - solar - available grid - generator rated_kw)``. Its sum
    over a run bounds what a larger reserve can fix (``capacity`` in the Risk reasons).
    """

    solar: str
    load: str
    unserved_kw: list[float]
    grid_extra_kw: list[float]
    discharge_extra_kw: list[float]
    generator_extra_kw: list[float]
    charge_cut_kw: list[float]
    grid_kw: list[float]
    generator_kw: list[float]
    battery_charge_kw: list[float]
    battery_discharge_kw: list[float]
    soc_kwh: list[float]
    min_battery_kw: list[float]
    limit: list[str]
    at_risk: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def scenario(self) -> str:
        return f"solar_{self.solar},load_{self.load}"

    @property
    def unserved_kwh(self) -> float:
        return sum(self.unserved_kw) * slot_hours()

    def short_slots(self) -> list[int]:
        h = slot_hours()
        return [t for t, v in enumerate(self.unserved_kw) if v * h > TOL]


def _series(q, name: str) -> list[float]:
    if name not in QUANTILES:
        raise ValueError(f"quantile must be one of {QUANTILES}, got {name!r}")
    return list(getattr(q, f"{name}_kw"))


def stress_replay(
    plan: PlanResult,
    forecast: Forecast,
    signals: SignalTable,
    assets: AssetConstraints,
    solar: str = "p10",
    load: str = "p50",
) -> StressReplay:
    """Replay ``plan`` at the ``solar`` / ``load`` quantiles of ``forecast``; see the module docstring."""
    s, h = plan.schedule, slot_hours()
    b, gen_c, grid_max = assets.battery, assets.generator, assets.grid.max_import_kw
    eta = b.one_way_efficiency
    sun = _series(forecast.solar, solar)
    dem = _series(forecast.load, load)
    ev_required = set(rules.ev_required_slots(assets.ev))
    n = len(s.grid_import_kw)

    cols: dict[str, list] = {
        k: []
        for k in (
            "unserved_kw",
            "grid_extra_kw",
            "discharge_extra_kw",
            "generator_extra_kw",
            "charge_cut_kw",
            "grid_kw",
            "generator_kw",
            "battery_charge_kw",
            "battery_discharge_kw",
            "soc_kwh",
            "min_battery_kw",
            "limit",
            "at_risk",
        )
    }
    level = b.initial_soc_kwh
    for t in range(n):
        outage = signals.outage[t]
        g0, q0, c0, d0, u0 = (
            s.grid_import_kw[t],
            s.generator_kw[t],
            s.battery_charge_kw[t],
            s.battery_discharge_kw[t],
            s.solar_used_kw[t],
        )
        # Discharge the battery can really deliver (down to soc_min) from its stressed level.
        dis_room = max(0.0, (level - b.soc_min_kwh) * eta / h)
        d = min(d0, dis_room)
        solar_now = min(u0, sun[t])
        spare_solar = max(0.0, sun[t] - solar_now)
        grid = 0.0 if outage else g0
        gap = (dem[t] - s.base_load_kw[t]) + (u0 - solar_now) + (d0 - d) + (g0 - grid)

        c, gen = c0, q0
        dis_x = 0.0
        if gap < 0:  # surplus (e.g. load below plan): back off grid, generator above min, then solar
            surplus = -gap
            back = min(surplus, grid)
            grid -= back
            surplus -= back
            if gen > 0:
                back = min(surplus, max(0.0, gen - gen_c.min_kw))
                gen -= back
                surplus -= back
            solar_now = max(0.0, solar_now - surplus)  # curtail; any remainder is dumped (never unsafe)
            gap = 0.0
        if gap > TOL:  # 1. curtailed solar
            x = min(gap, spare_solar)
            solar_now += x
            gap -= x
        if gap > TOL:  # 2. cut planned charging
            x = min(gap, c)
            c -= x
            gap -= x
        if gap > TOL and not outage:  # 3. extra grid import (never during an outage)
            x = min(gap, max(0.0, grid_max - grid))
            grid += x
            gap -= x
        if gap > TOL and c <= TOL:  # 4. extra battery discharge down to soc_min
            dis_x = max(0.0, min(gap, b.max_discharge_kw - d, dis_room - d))
            d += dis_x
            gap -= dis_x
        if gap > TOL:  # 5. generator, started if needed
            if gen > TOL:
                x = min(gap, gen_c.rated_kw - gen)
                gen += x
                gap -= x
            else:
                gen = min(max(gap, gen_c.min_kw), gen_c.rated_kw)
                surplus = gen - gap
                gap = max(0.0, gap - gen)
                for name in ("dis_x", "d", "grid", "solar"):  # absorb minimum-load surplus
                    if surplus <= TOL:
                        break
                    if name == "dis_x":
                        back = min(surplus, dis_x)
                        dis_x -= back
                        d -= back
                    elif name == "d":
                        back = min(surplus, d)
                        d -= back
                    elif name == "grid":
                        back = min(surplus, grid)
                        grid -= back
                    else:
                        back = min(surplus, solar_now)
                        solar_now -= back
                    surplus -= back
                if surplus > TOL and d <= TOL:  # store the rest if possible; otherwise it is dumped
                    room = max(0.0, min(b.max_charge_kw - c, (b.soc_max_kwh - level) / (eta * h)))
                    c += min(surplus, room)

        unserved = max(0.0, gap)
        limit = ""
        risk: tuple[str, ...] = ()
        if unserved * h > TOL:
            limit = "power" if d >= b.max_discharge_kw - 1e-6 else "energy"
            risk = tuple(
                name
                for name, hit in (
                    ("comfort", assets.hvac.occupied[t] and s.hvac_kw[t] > TOL),
                    ("ev", t in ev_required and s.ev_charge_kw[t] > TOL),
                )
                if hit
            ) or ("base_load",)
        elif not outage and level + eta * c * h - d * h / eta < s.battery_soc_kwh[t] - TOL:
            # Recovery: spare grid replaces discharge, then charges back towards the planned SoC.
            spare = max(0.0, grid_max - grid)
            need_kwh = s.battery_soc_kwh[t] - (level + eta * c * h - d * h / eta)
            back = min(d, spare, need_kwh * eta / h)
            d -= back
            grid += back
            spare -= back
            need_kwh -= back * h / eta
            if d <= TOL and need_kwh > TOL:
                x = max(
                    0.0,
                    min(
                        spare,
                        b.max_charge_kw - c,
                        need_kwh / (eta * h),
                        (b.soc_max_kwh - level) / (eta * h) - c,
                    ),
                )
                c += x
                grid += x
        level = level + eta * c * h - d * h / eta

        for key, val in (
            ("unserved_kw", unserved),
            ("grid_extra_kw", max(0.0, grid - g0)),
            ("discharge_extra_kw", max(0.0, d - d0)),
            ("generator_extra_kw", max(0.0, gen - q0)),
            ("charge_cut_kw", max(0.0, c0 - c)),
            ("grid_kw", grid),
            ("generator_kw", gen),
            ("battery_charge_kw", c),
            ("battery_discharge_kw", d),
            ("soc_kwh", level),
            (
                "min_battery_kw",
                max(
                    0.0,
                    dem[t]
                    + s.hvac_kw[t]
                    + s.ev_charge_kw[t]
                    - sun[t]
                    - (0.0 if outage else grid_max)
                    - gen_c.rated_kw,
                ),
            ),
            ("limit", limit),
            ("at_risk", risk),
        ):
            cols[key].append(val)
    return StressReplay(solar=solar, load=load, **cols)
