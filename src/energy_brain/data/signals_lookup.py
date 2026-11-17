"""Signals configuration and lookup (Issue #8).

Loads and validates economic and environmental parameters from config/signals.yaml:
- Time-of-use grid tariffs (flat, peak, off-peak)
- Grid carbon emission factor
- Diesel fuel price and diesel emission factor

Provides slot-aligned lookup functions for the Signal agent (#15).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SIGNALS_CONFIG = Path(__file__).resolve().parents[3] / "config" / "signals.yaml"
DHAKA_TZ = ZoneInfo("Asia/Dhaka")


class TariffRates(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    flat_bdt_per_kwh: float = Field(..., gt=0.0)
    peak_bdt_per_kwh: float = Field(..., gt=0.0)
    off_peak_bdt_per_kwh: float = Field(..., gt=0.0)


class TariffConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_class: str
    utility: str
    regulation: str
    as_of: str
    source: str
    source_url: str
    currency: str
    rates: TariffRates
    demand_charge_bdt_per_kw_month: float = Field(..., ge=0.0)


class GridEmissionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    factor_tco2_per_mwh: float = Field(..., gt=0.0)
    factor_kg_per_kwh: float = Field(..., gt=0.0)
    as_of: str
    authority: str
    report: str
    source_url: str


class DieselConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    price_bdt_per_l: float = Field(..., gt=0.0)
    as_of: str
    price_authority: str
    source_url: str
    emission_factor_kg_per_l: float = Field(..., gt=0.0)
    emission_source: str


class SignalsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tariff: TariffConfig
    grid_emission: GridEmissionConfig
    diesel: DieselConfig


def load_signals_config(path: Path | None = None) -> SignalsConfig:
    """Load and validate config/signals.yaml."""
    cfg_path = path or DEFAULT_SIGNALS_CONFIG
    if not cfg_path.exists():
        raise FileNotFoundError(f"Signals config file not found at {cfg_path}")

    with open(cfg_path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    return SignalsConfig.model_validate(raw)


def get_tariff_for_datetime(dt: datetime.datetime, cfg: SignalsConfig) -> float:
    """Return the BERC TOU tariff rate (BDT/kWh) for a given timestamp in Asia/Dhaka.

    Windows (BERC MT-3):
    - Peak: 17:00 to 23:00 (5:00 PM to 11:00 PM) -> peak_bdt_per_kwh
    - Off-peak: 23:00 to 06:00 (11:00 PM to 6:00 AM) -> off_peak_bdt_per_kwh
    - Flat: 06:00 to 17:00 (6:00 AM to 5:00 PM) -> flat_bdt_per_kwh
    """
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware (Asia/Dhaka)")

    local_dt = dt.astimezone(DHAKA_TZ)
    hour = local_dt.hour

    if 17 <= hour < 23:
        return cfg.tariff.rates.peak_bdt_per_kwh
    elif hour >= 23 or hour < 6:
        return cfg.tariff.rates.off_peak_bdt_per_kwh
    else:
        return cfg.tariff.rates.flat_bdt_per_kwh
