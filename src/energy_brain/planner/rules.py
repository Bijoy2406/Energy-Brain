"""Per-slot interpretations of the hard constraints, shared by the MILP, the fallback and the checker (#17).

Everything that turns an ``AssetConstraints`` field into a per-slot rule lives here, so the model
(``planner.model``), the rule-based reference schedule (``planner.reference``) and the independent checker
(``planner.checks``) can never disagree about what a constraint means.
"""

from __future__ import annotations

from energy_brain.schemas import BatteryConstraints, EvConstraints, HvacConstraints
from energy_brain.timeaxis import n_slots, slot_hours


def soc_floor_kwh(battery: BatteryConstraints, reserve_margin_kwh: float) -> list[float]:
    """Lowest allowed SoC (kWh, end of slot) per slot, including the Risk agent's reserve margin.

    The raised floor is ``min(soc_min + reserve, soc_max)``. If it lies above the starting charge it cannot
    hold from slot 0, so in slot t it is capped at the most the battery could hold after charging at full
    power since the start of the day: ``initial + eta * max_charge_kw * h * (t + 1)``. It is never below
    ``soc_min_kwh``.
    """
    h = slot_hours()
    eta = battery.one_way_efficiency
    raised = min(battery.soc_min_kwh + reserve_margin_kwh, battery.soc_max_kwh)
    return [
        max(
            battery.soc_min_kwh,
            min(raised, battery.initial_soc_kwh + eta * battery.max_charge_kw * h * (t + 1)),
        )
        for t in range(n_slots())
    ]


def ev_required_slots(ev: EvConstraints) -> list[int]:
    """Plugged-in slots in which ``energy_required_kwh`` must be delivered (before ``departure_slot``).

    * Window does not wrap inside the day (``arrival_slot < departure``): slots ``[arrival, departure)``.
    * Window wraps midnight (``arrival_slot >= departure_slot``, e.g. day starts 00:00 and the vans park
      18:00-07:00): the vans leaving at ``departure_slot`` are the ones parked since before slot 0, so
      the energy is due in ``[0, departure)``. Slots from ``arrival_slot`` on are an optional evening
      window: charging there is allowed (up to ``charger_kw``) but nothing is required; that energy is
      the next day's plan.
    * ``departure_slot == 0`` means departure at the end of the horizon.
    """
    n = n_slots()
    end = ev.departure_slot if ev.departure_slot > 0 else n
    window = range(ev.arrival_slot, end) if ev.arrival_slot < end else range(0, end)
    return [t for t in window if ev.plugged_in[t]]


def ev_optional_slots(ev: EvConstraints) -> list[int]:
    """Plugged-in slots outside :func:`ev_required_slots` (evening arrivals for the next day)."""
    required = set(ev_required_slots(ev))
    return [t for t, p in enumerate(ev.plugged_in) if p and t not in required]


def ev_deliverable_kwh(ev: EvConstraints) -> float:
    """Most energy the chargers can deliver in the required window."""
    return ev.charger_kw * slot_hours() * len(ev_required_slots(ev))


def hvac_precool_slots(hvac: HvacConstraints) -> int:
    """Number of slots cooling may start before an occupied slot (``max_precool_hours`` in slots)."""
    return int(hvac.max_precool_hours / slot_hours() + 1e-9)


def hvac_allowed(hvac: HvacConstraints) -> list[bool]:
    """True where HVAC may run: occupied slots, plus up to ``max_precool_hours`` before an occupied slot.

    Outside these slots HVAC is off (0 kW). The model has no thermal state, so pre-cooling is allowed but
    never required (see ``planner.model``).
    """
    k = hvac_precool_slots(hvac)
    occ = hvac.occupied
    n = len(occ)
    return [occ[t] or any(occ[u] for u in range(t + 1, min(n, t + k + 1))) for t in range(n)]


def hvac_min_kw(hvac: HvacConstraints) -> list[float]:
    """Minimum HVAC power per slot: ``min_kw_when_occupied`` when occupied, else 0 (the comfort stand-in)."""
    return [hvac.min_kw_when_occupied if o else 0.0 for o in hvac.occupied]
