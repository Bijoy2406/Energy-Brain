"""Grid connection limit (#16), from ``config/site.yaml`` -> ``grid``."""

from __future__ import annotations

from energy_brain.config import SiteConfig, load_site_config
from energy_brain.schemas import GridConstraints


def grid_constraints(config: SiteConfig | None = None) -> GridConstraints:
    """Sanctioned import limit. Export is not modelled in the MVP (``grid.export_allowed`` is false)."""
    cfg = config if config is not None else load_site_config()
    return GridConstraints(max_import_kw=cfg.grid.max_import_kw)
