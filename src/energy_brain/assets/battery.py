"""Battery asset agent (#16), from ``config/site.yaml`` -> ``battery``.

SoC limits are HARD constraints (PRD section 8, responsible AI): they are published as fixed kWh bounds and
there is no parameter to widen them for savings.
"""

from __future__ import annotations

from energy_brain.config import SiteConfig, load_site_config
from energy_brain.schemas import BatteryConstraints


def battery_constraints(config: SiteConfig | None = None) -> BatteryConstraints:
    """Capacity, AC charge/discharge limits, round-trip efficiency and SoC bounds (percent -> kWh)."""
    cfg = config if config is not None else load_site_config()
    b = cfg.battery
    return BatteryConstraints(
        capacity_kwh=b.capacity_kwh,
        max_charge_kw=b.max_charge_kw,
        max_discharge_kw=b.max_discharge_kw,
        round_trip_efficiency=b.round_trip_efficiency,
        soc_min_kwh=b.capacity_kwh * b.soc_min_pct / 100,
        soc_max_kwh=b.capacity_kwh * b.soc_max_pct / 100,
        initial_soc_kwh=b.capacity_kwh * b.initial_soc_pct / 100,
    )
