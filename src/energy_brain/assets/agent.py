"""Aggregated asset agent (#16): implements ``energy_brain.interfaces.AssetProvider``."""

from __future__ import annotations

from datetime import datetime

from energy_brain.assets.battery import battery_constraints
from energy_brain.assets.ev import ev_constraints
from energy_brain.assets.generator import generator_constraints
from energy_brain.assets.grid import grid_constraints
from energy_brain.assets.hvac import hvac_constraints
from energy_brain.config import SiteConfig, load_site_config
from energy_brain.schemas import AssetConstraints
from energy_brain.timeaxis import ensure_start


class AssetAgent:
    """Publishes the hard limits of grid, battery, HVAC, EV fleet and generator, all read from site config.

    Drop-in replacement for ``energy_brain.stubs.StubAssetProvider``.
    """

    def __init__(self, config: SiteConfig | None = None) -> None:
        self.config = config if config is not None else load_site_config()

    def run(self, start: datetime) -> AssetConstraints:
        """All asset constraint blocks for the day starting at ``start`` (tz-aware, on a slot boundary)."""
        cfg = self.config
        start = ensure_start(start, cfg)
        return AssetConstraints(
            start=start,
            grid=grid_constraints(cfg),
            battery=battery_constraints(cfg),
            hvac=hvac_constraints(start, cfg),
            ev=ev_constraints(start, cfg),
            generator=generator_constraints(cfg),
        )
