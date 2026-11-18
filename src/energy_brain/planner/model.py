"""Pyomo MILP for the day-ahead schedule (#17). Building only; solving is ``planner.solve``.

Per slot t (``n_slots()`` slots, ``h = slot_hours()``), all power variables in kW and >= 0:

* ``grid``  grid import, ``<= grid.max_import_kw`` and fixed to 0 when ``signals.outage[t]``;
* ``solar_used + solar_curt == solar P50`` (curtailment is free, there is no export);
* ``charge <= max_charge_kw * mode``, ``discharge <= max_discharge_kw * (1 - mode)``, ``mode`` binary,
  so the battery never charges and discharges in the same slot;
* ``soc[t] = soc[t-1] + eta*charge*h - discharge*h/eta`` with ``soc[-1] = initial_soc_kwh`` and
  ``eta = sqrt(round_trip_efficiency)``; ``floor[t] <= soc[t] <= soc_max_kwh`` where ``floor`` includes the
  reserve margin (``rules.soc_floor_kwh``); end of day ``soc[n-1] >= initial_soc_kwh`` so savings never
  come from draining the battery;
* ``hvac`` in ``[min_kw_when_occupied, rated_kw]`` when occupied, in ``[0, rated_kw]`` up to
  ``max_precool_hours`` before an occupied slot, 0 otherwise;
* ``ev <= charger_kw`` while plugged in, 0 otherwise; ``sum(ev[required slots]) * h >= energy_required_kwh``
  (``rules.ev_required_slots``);
* ``min_kw * on <= gen <= rated_kw * on``, ``on`` binary;
* energy balance ``solar_used + grid + discharge + gen == base_load(P50) + hvac + ev + charge``.

Objective: ``w * cost / cost_scale + (1 - w) * co2 / co2_scale + TIE * (other term)``. ``cost`` and
``co2`` are exactly the ``accounting.compute_totals`` formulas. ``cost_scale`` / ``co2_scale`` are the
totals of the rule-based reference schedule (``planner.reference``, solved without reserve), floored
at :data:`MIN_SCALE`. So each term is "fraction of a do-nothing-clever day", and w = 0.5 trades 1 % of cost
against 1 % of CO2. The tiny ``TIE`` term breaks ties at w = 1 / w = 0 (cheapest plan, then the cleanest of
those, and vice versa) without changing the optimum otherwise.

Limitation (comfort): ``AssetConstraints`` has no indoor/outdoor temperature or building model, so the
comfort band in degrees C cannot be modelled. ``min_kw_when_occupied`` is the comfort stand-in. Without a
thermal state, cooling before a shift does not reduce cooling during it, so pre-cooling never pays off and
HVAC stays at its occupied minimum. Real pre-cooling needs a thermal model and new schema fields.

Slack mode (``elastic=True``): adds ``unserved[t] >= 0`` kW on the supply side of the balance and
``ev_short >= 0`` kWh on the EV requirement, heavily penalised. Every other hard constraint is kept. Used
only to diagnose an infeasible day; such a schedule is never safe to execute.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyomo.environ as pe

from energy_brain.planner import rules
from energy_brain.schemas import AssetConstraints, Forecast, SignalTable
from energy_brain.timeaxis import n_slots, slot_hours

#: Floor for the objective scales (BDT and kg), so an all-solar reference day never divides by ~0.
MIN_SCALE = 1.0
#: Weight of the tie-break term relative to the main objective.
TIE = 1e-6
#: Penalty per kWh of unserved energy / EV shortfall in slack mode (objective is otherwise O(1)).
SLACK_PENALTY = 1e3


@dataclass(frozen=True)
class Scales:
    """Normalisation for the objective: cost in BDT and CO2 in kg of the reference schedule."""

    cost_bdt: float
    co2_kg: float

    @classmethod
    def from_totals(cls, cost_bdt: float, co2_kg: float) -> Scales:
        return cls(max(cost_bdt, MIN_SCALE), max(co2_kg, MIN_SCALE))


def build_model(
    forecast: Forecast,
    signals: SignalTable,
    assets: AssetConstraints,
    weight: float,
    reserve_margin_kwh: float,
    scales: Scales,
    *,
    elastic: bool = False,
) -> pe.ConcreteModel:
    """Return the Pyomo model described in the module docstring. Reads inputs only, never modifies them."""
    n, h = n_slots(), slot_hours()
    b, hv, ev, gen = assets.battery, assets.hvac, assets.ev, assets.generator
    eta = b.one_way_efficiency
    solar, load = forecast.solar.p50_kw, forecast.load.p50_kw
    floor = rules.soc_floor_kwh(b, reserve_margin_kwh)
    allowed, hmin = rules.hvac_allowed(hv), rules.hvac_min_kw(hv)
    fuel = gen.fuel_l_per_kwh

    m = pe.ConcreteModel(name="energy_brain_day_ahead")
    m.T = pe.RangeSet(0, n - 1)
    nn = pe.NonNegativeReals

    m.grid = pe.Var(
        m.T, within=nn, bounds=lambda _, t: (0, 0 if signals.outage[t] else assets.grid.max_import_kw)
    )
    m.solar_used = pe.Var(m.T, within=nn, bounds=lambda _, t: (0, solar[t]))
    m.solar_curt = pe.Var(m.T, within=nn, bounds=lambda _, t: (0, solar[t]))
    m.charge = pe.Var(m.T, within=nn, bounds=(0, b.max_charge_kw))
    m.discharge = pe.Var(m.T, within=nn, bounds=(0, b.max_discharge_kw))
    m.mode = pe.Var(m.T, within=pe.Binary)
    m.soc = pe.Var(m.T, within=nn, bounds=lambda _, t: (floor[t], b.soc_max_kwh))
    m.hvac = pe.Var(m.T, within=nn, bounds=lambda _, t: (hmin[t], hv.rated_kw if allowed[t] else 0.0))
    m.ev = pe.Var(m.T, within=nn, bounds=lambda _, t: (0, ev.charger_kw if ev.plugged_in[t] else 0.0))
    m.gen = pe.Var(m.T, within=nn, bounds=(0, gen.rated_kw))
    m.gen_on = pe.Var(m.T, within=pe.Binary)

    m.solar_split = pe.Constraint(m.T, rule=lambda m, t: m.solar_used[t] + m.solar_curt[t] == solar[t])
    m.charge_mode = pe.Constraint(m.T, rule=lambda m, t: m.charge[t] <= b.max_charge_kw * m.mode[t])
    m.discharge_mode = pe.Constraint(
        m.T, rule=lambda m, t: m.discharge[t] <= b.max_discharge_kw * (1 - m.mode[t])
    )

    def soc_rule(m, t):
        prev = b.initial_soc_kwh if t == 0 else m.soc[t - 1]
        return m.soc[t] == prev + eta * h * m.charge[t] - (h / eta) * m.discharge[t]

    m.soc_dyn = pe.Constraint(m.T, rule=soc_rule)
    m.soc_end = pe.Constraint(expr=m.soc[n - 1] >= b.initial_soc_kwh)

    m.gen_min = pe.Constraint(m.T, rule=lambda m, t: m.gen[t] >= gen.min_kw * m.gen_on[t])
    m.gen_max = pe.Constraint(m.T, rule=lambda m, t: m.gen[t] <= gen.rated_kw * m.gen_on[t])

    req = rules.ev_required_slots(ev)
    if elastic:
        m.unserved = pe.Var(m.T, within=nn)
        m.ev_short = pe.Var(within=nn)
    ev_delivered = sum(m.ev[t] for t in req) * h
    ev_slack = m.ev_short if elastic else 0.0
    m.ev_energy = pe.Constraint(expr=ev_delivered + ev_slack >= ev.energy_required_kwh)

    def balance(m, t):
        supply = m.solar_used[t] + m.grid[t] + m.discharge[t] + m.gen[t]
        if elastic:
            supply = supply + m.unserved[t]
        return supply == load[t] + m.hvac[t] + m.ev[t] + m.charge[t]

    m.balance = pe.Constraint(m.T, rule=balance)

    # Exactly the accounting.compute_totals formulas.
    m.cost_bdt = pe.Expression(
        expr=sum(
            m.grid[t] * h * signals.tariff_bdt_per_kwh[t]
            + m.gen[t] * h * fuel * signals.diesel_price_bdt_per_l[t]
            for t in m.T
        )
    )
    m.co2_kg = pe.Expression(
        expr=sum(
            m.grid[t] * h * signals.grid_co2_kg_per_kwh[t]
            + m.gen[t] * h * fuel * signals.diesel_co2_kg_per_l[t]
            for t in m.T
        )
    )
    c_norm = m.cost_bdt / scales.cost_bdt
    e_norm = m.co2_kg / scales.co2_kg
    obj = weight * c_norm + (1 - weight) * e_norm + TIE * (c_norm + e_norm)
    if elastic:
        obj = obj + SLACK_PENALTY * (sum(m.unserved[t] for t in m.T) * h + m.ev_short)
    m.obj = pe.Objective(expr=obj, sense=pe.minimize)
    return m
