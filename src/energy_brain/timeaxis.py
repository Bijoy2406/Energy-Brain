"""Shared planning time axis (issue #13).

One planning day = ``n_slots()`` slots of ``slot_minutes()`` minutes (96 x 15 min with the default
``config/site.yaml``). Slot 0 starts at a tz-aware ``start`` datetime in the site time zone (Asia/Dhaka).
Power values in every series are kW averaged over the slot; energy per slot = kW x ``slot_hours()``.

Never hard-code 96: always derive slot counts from these helpers.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from energy_brain.config import SiteConfig, load_site_config


@lru_cache(maxsize=1)
def _default_config() -> SiteConfig:
    return load_site_config()


def _cfg(config: SiteConfig | None) -> SiteConfig:
    return config if config is not None else _default_config()


def slot_minutes(config: SiteConfig | None = None) -> int:
    """Length of one slot in minutes (15 by default)."""
    return _cfg(config).planning.slot_minutes


def n_slots(config: SiteConfig | None = None) -> int:
    """Number of slots in one planning horizon (96 by default)."""
    return _cfg(config).planning.n_slots


def slot_hours(config: SiteConfig | None = None) -> float:
    """Length of one slot in hours (0.25 by default); energy_kwh = power_kw x slot_hours."""
    return slot_minutes(config) / 60


def site_tz(config: SiteConfig | None = None) -> ZoneInfo:
    """Site time zone (Asia/Dhaka)."""
    return ZoneInfo(_cfg(config).site.timezone)


def ensure_start(start: datetime, config: SiteConfig | None = None) -> datetime:
    """Validate a planning ``start`` and return it in the site time zone.

    Raises ``ValueError`` if ``start`` is naive (no tzinfo) or not on a slot boundary.
    """
    if not isinstance(start, datetime):
        raise ValueError(f"start must be a datetime, got {type(start).__name__}")
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware (Asia/Dhaka); got a naive datetime")
    local = start.astimezone(site_tz(config))
    if local.second or local.microsecond or local.minute % slot_minutes(config):
        raise ValueError(f"start must lie on a {slot_minutes(config)}-minute slot boundary, got {local}")
    return local


def slot_times(start: datetime, config: SiteConfig | None = None) -> list[datetime]:
    """Start time of every slot, tz-aware in the site time zone (length ``n_slots()``)."""
    s = ensure_start(start, config)
    step = timedelta(minutes=slot_minutes(config))
    return [s + i * step for i in range(n_slots(config))]


def slot_of(clock: time, start: datetime, config: SiteConfig | None = None) -> int:
    """Index of the slot containing wall-clock time ``clock``, counted from ``start`` (wraps at 24 h)."""
    s = ensure_start(start, config)
    minutes = (clock.hour * 60 + clock.minute - (s.hour * 60 + s.minute)) % (24 * 60)
    return minutes // slot_minutes(config)
