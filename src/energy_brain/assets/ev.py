"""EV / e-rickshaw fleet asset agent (#16), from ``config/site.yaml`` -> ``ev_fleet``.

The fleet is aggregated: ``charger_kw`` and ``energy_required_kwh`` are fleet totals. Vehicles are plugged
in over ``[arrival_time, departure_time)``, a window that may wrap midnight (18:00-07:00 by default).
Arrival and departure must lie on slot boundaries: a partly parked slot would let the Planner count charging
time the vehicles are not there for, so such a config is rejected instead of rounded.

Departure energy is a HARD constraint (PRD section 8): there is no parameter to lower it for savings.
"""

from __future__ import annotations

from datetime import datetime, time

from energy_brain.assets._windows import clock_minutes, in_window
from energy_brain.config import SiteConfig, load_site_config
from energy_brain.schemas import EvConstraints
from energy_brain.timeaxis import slot_minutes, slot_of, slot_times


def _check_on_boundary(name: str, t: time, step: int) -> None:
    if clock_minutes(t) % step:
        raise ValueError(f"ev_fleet.{name}={t:%H:%M} is not on a {step}-minute slot boundary")


def plugged_in_mask(start: datetime, config: SiteConfig) -> list[bool]:
    """Per-slot plug-in mask for the day at ``start``; True for whole slots inside the parking window."""
    ev = config.ev_fleet
    return [
        in_window(clock_minutes(ts.time()), ev.arrival_time, ev.departure_time)
        for ts in slot_times(start, config)
    ]


def ev_constraints(start: datetime, config: SiteConfig | None = None) -> EvConstraints:
    """Plug-in mask, fleet charger power, fleet energy needed at departure and arrival/departure slots."""
    cfg = config if config is not None else load_site_config()
    ev = cfg.ev_fleet
    step = slot_minutes(cfg)
    _check_on_boundary("arrival_time", ev.arrival_time, step)
    _check_on_boundary("departure_time", ev.departure_time, step)
    if ev.arrival_time == ev.departure_time:
        raise ValueError("ev_fleet.arrival_time equals departure_time: the parking window is empty")
    return EvConstraints(
        plugged_in=plugged_in_mask(start, cfg),
        charger_kw=ev.vehicles * ev.charger_kw,
        energy_required_kwh=ev.vehicles * ev.energy_needed_per_vehicle_kwh,
        arrival_slot=slot_of(ev.arrival_time, start, cfg),
        departure_slot=slot_of(ev.departure_time, start, cfg),
    )
