"""HVAC asset agent (#16), from ``config/site.yaml`` -> ``hvac`` and ``load.shifts`` / ``load.weekend_days``.

``occupied[t]`` is True when slot t overlaps any shift in ``load.shifts`` on a working day. A slot only
partly inside a shift counts as occupied (conservative for comfort). Weekly holidays (``load.weekend_days``,
Friday) and Bangladesh public holidays are unoccupied, matching the load model in
``energy_brain.data.synthetic_load``. The comfort band is a HARD constraint (PRD section 8): it is copied
from the config unchanged and there is no parameter to relax it.
"""

from __future__ import annotations

from datetime import date, datetime

from energy_brain.assets._windows import clock_minutes, slot_overlaps
from energy_brain.config import SiteConfig, load_site_config
from energy_brain.data.synthetic_load import is_bangladesh_holiday
from energy_brain.schemas import HvacConstraints
from energy_brain.timeaxis import slot_minutes, slot_times


def is_off_day(day: date, config: SiteConfig) -> bool:
    """True on a weekly holiday from ``load.weekend_days`` or a Bangladesh public holiday."""
    return day.strftime("%A") in config.load.weekend_days or is_bangladesh_holiday(day)


def occupied_mask(start: datetime, config: SiteConfig) -> list[bool]:
    """Per-slot occupancy for the day at ``start`` (``n_slots()`` items)."""
    step = slot_minutes(config)
    return [
        not is_off_day(ts.date(), config)
        and any(slot_overlaps(clock_minutes(ts.time()), step, s.start, s.end) for s in config.load.shifts)
        for ts in slot_times(start, config)
    ]


def hvac_constraints(start: datetime, config: SiteConfig | None = None) -> HvacConstraints:
    """Power range, per-slot occupancy, comfort band and pre-cooling limit for the day at ``start``."""
    cfg = config if config is not None else load_site_config()
    hv = cfg.hvac
    return HvacConstraints(
        rated_kw=hv.rated_kw,
        min_kw_when_occupied=hv.min_kw_when_occupied,
        occupied=occupied_mask(start, cfg),
        comfort_min_c=hv.comfort_min_c,
        comfort_max_c=hv.comfort_max_c,
        max_precool_hours=hv.max_precool_hours,
    )
