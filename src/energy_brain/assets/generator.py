"""Diesel generator asset agent (#16), from ``config/site.yaml`` -> ``generator``.

On/off is the Planner's decision: when running, ``min_kw <= generator_kw <= rated_kw``, otherwise 0.
"""

from __future__ import annotations

from energy_brain.config import SiteConfig, load_site_config
from energy_brain.schemas import GeneratorConstraints


def generator_constraints(config: SiteConfig | None = None) -> GeneratorConstraints:
    """Rated kW, minimum stable load (``rated_kw * min_load_pct / 100``) and fuel use per kWh."""
    cfg = config if config is not None else load_site_config()
    g = cfg.generator
    return GeneratorConstraints(rated_kw=g.rated_kw, min_kw=g.min_kw, fuel_l_per_kwh=g.fuel_l_per_kwh)
