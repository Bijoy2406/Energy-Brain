"""Independent hard-constraint checker for a ``Schedule`` (#17).

The Planner runs this on every schedule before returning it (MILP or fallback), so a solver or extraction
bug can never ship as a "safe" plan. It re-derives every rule from the inputs via ``planner.rules`` and does
not look at the optimisation model.
"""

from __future__ import annotations

from energy_brain.planner import rules
from energy_brain.planner.fixes import SlotFixes
from energy_brain.schemas import AssetConstraints, Forecast, Schedule, SignalTable
from energy_brain.timeaxis import slot_hours

#: Default tolerance in kW / kWh for comparing solver output with limits.
TOL = 1e-4


def _slots(ts: list[int], limit: int = 8) -> str:
    shown = ", ".join(str(t) for t in ts[:limit])
    return shown + (f" (+{len(ts) - limit} more)" if len(ts) > limit else "")


def find_violations(
    schedule: Schedule,
    forecast: Forecast,
    signals: SignalTable,
    assets: AssetConstraints,
    reserve_margin_kwh: float = 0.0,
    tol: float = TOL,
    *,
    fixes: SlotFixes | None = None,
) -> list[str]:
    """Return one human-readable message per violated hard constraint (empty list = all satisfied)."""
    s, h = schedule, slot_hours()
    b, hv, ev, gen = assets.battery, assets.hvac, assets.ev, assets.generator
    eta = b.one_way_efficiency
    out: list[str] = []

    def check(name: str, bad: list[int]) -> None:
        if bad:
            out.append(f"{name} violated in slots {_slots(bad)}")

    n = len(s.grid_import_kw)
    rng = range(n)
    resid = s.energy_balance_residual_kw()
    check("energy balance", [t for t in rng if abs(resid[t]) > tol])
    check("base load = load P50", [t for t in rng if abs(s.base_load_kw[t] - forecast.load.p50_kw[t]) > tol])
    check(
        "solar used + curtailed = solar P50",
        [
            t
            for t in rng
            if abs(s.solar_used_kw[t] + s.solar_curtailed_kw[t] - forecast.solar.p50_kw[t]) > tol
        ],
    )
    check("grid max import", [t for t in rng if s.grid_import_kw[t] > assets.grid.max_import_kw + tol])
    check(
        "zero grid import during outage", [t for t in rng if signals.outage[t] and s.grid_import_kw[t] > tol]
    )

    check("battery max charge", [t for t in rng if s.battery_charge_kw[t] > b.max_charge_kw + tol])
    check("battery max discharge", [t for t in rng if s.battery_discharge_kw[t] > b.max_discharge_kw + tol])
    check(
        "no simultaneous battery charge and discharge",
        [t for t in rng if s.battery_charge_kw[t] > tol and s.battery_discharge_kw[t] > tol],
    )
    prev, bad_dyn = b.initial_soc_kwh, []
    for t in rng:
        expect = prev + eta * s.battery_charge_kw[t] * h - s.battery_discharge_kw[t] * h / eta
        if abs(s.battery_soc_kwh[t] - expect) > tol:
            bad_dyn.append(t)
        prev = s.battery_soc_kwh[t]
    check("battery SoC dynamics", bad_dyn)
    floor = rules.soc_floor_kwh(b, reserve_margin_kwh)
    check("battery SoC floor (incl. reserve)", [t for t in rng if s.battery_soc_kwh[t] < floor[t] - tol])
    check("battery SoC max", [t for t in rng if s.battery_soc_kwh[t] > b.soc_max_kwh + tol])
    if s.battery_soc_kwh[-1] < b.initial_soc_kwh - tol:
        out.append(
            f"battery end-of-day SoC {s.battery_soc_kwh[-1]:.1f} kWh below the starting "
            f"{b.initial_soc_kwh:.1f} kWh"
        )

    allowed, hmin = rules.hvac_allowed(hv), rules.hvac_min_kw(hv)
    check("HVAC rated power", [t for t in rng if s.hvac_kw[t] > hv.rated_kw + tol])
    check("HVAC occupied minimum (comfort)", [t for t in rng if s.hvac_kw[t] < hmin[t] - tol])
    check(
        "HVAC off outside occupied/pre-cool slots", [t for t in rng if not allowed[t] and s.hvac_kw[t] > tol]
    )

    check(
        "EV charging only while plugged in",
        [t for t in rng if not ev.plugged_in[t] and s.ev_charge_kw[t] > tol],
    )
    check("EV charger limit", [t for t in rng if s.ev_charge_kw[t] > ev.charger_kw + tol])
    delivered = sum(s.ev_charge_kw[t] for t in rules.ev_required_slots(ev)) * h
    if delivered < ev.energy_required_kwh - tol:
        out.append(
            f"EV departure energy: {delivered:.1f} kWh delivered before departure, "
            f"{ev.energy_required_kwh:.1f} kWh required"
        )

    check(
        "generator off or within [min_kw, rated_kw]",
        [
            t
            for t in rng
            if s.generator_kw[t] > tol and not (gen.min_kw - tol <= s.generator_kw[t] <= gen.rated_kw + tol)
        ],
    )
    if fixes is not None:
        for fix in fixes.slots:
            for name, value in fix.values().items():
                check(
                    f"fixed {name} = {value:g} kW",
                    [fix.slot] if abs(getattr(s, name)[fix.slot] - value) > tol else [],
                )
    return out
