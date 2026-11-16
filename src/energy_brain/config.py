"""Load and validate the site and asset spec in ``config/site.yaml`` (issue #2).

Every other module should read site numbers through :func:`load_site_config` instead of
hard-coding them, so the whole system uses one source of truth.

Example::

    from energy_brain.config import load_site_config
    cfg = load_site_config()
    cfg.battery.capacity_kwh   # 1000.0
    cfg.planning.n_slots       # 96
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SITE_CONFIG = REPO_ROOT / "config" / "site.yaml"

Weekday = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Site(_Strict):
    name: str
    type: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str


class Planning(_Strict):
    slot_minutes: int = Field(gt=0)
    horizon_hours: int = Field(gt=0)

    @model_validator(mode="after")
    def _slots_fit(self) -> Planning:
        if (self.horizon_hours * 60) % self.slot_minutes:
            raise ValueError("horizon_hours must be a whole number of slots")
        return self

    @property
    def n_slots(self) -> int:
        return self.horizon_hours * 60 // self.slot_minutes


class Grid(_Strict):
    max_import_kw: float = Field(gt=0)
    export_allowed: bool


class Shift(_Strict):
    start: time
    end: time


class Load(_Strict):
    peak_kw: float = Field(gt=0)
    base_kw: float = Field(ge=0)
    weekend_days: list[Weekday]
    shifts: list[Shift]

    @model_validator(mode="after")
    def _base_below_peak(self) -> Load:
        if self.base_kw >= self.peak_kw:
            raise ValueError("load.base_kw must be below load.peak_kw")
        return self


class Solar(_Strict):
    capacity_kwp: float = Field(gt=0)
    tilt_deg: float = Field(ge=0, le=90)
    azimuth_deg: float = Field(ge=0, lt=360)
    system_losses_pct: float = Field(ge=0, lt=100)


class Battery(_Strict):
    capacity_kwh: float = Field(gt=0)
    max_charge_kw: float = Field(gt=0)
    max_discharge_kw: float = Field(gt=0)
    round_trip_efficiency: float = Field(gt=0, le=1)
    soc_min_pct: float = Field(ge=0, le=100)
    soc_max_pct: float = Field(ge=0, le=100)
    initial_soc_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _soc_order(self) -> Battery:
        if not self.soc_min_pct < self.soc_max_pct:
            raise ValueError("battery.soc_min_pct must be below soc_max_pct")
        if not self.soc_min_pct <= self.initial_soc_pct <= self.soc_max_pct:
            raise ValueError("battery.initial_soc_pct must be within [soc_min_pct, soc_max_pct]")
        return self


class Hvac(_Strict):
    rated_kw: float = Field(gt=0)
    min_kw_when_occupied: float = Field(ge=0)
    comfort_min_c: float
    comfort_max_c: float
    max_precool_hours: float = Field(ge=0)

    @model_validator(mode="after")
    def _ranges(self) -> Hvac:
        if self.min_kw_when_occupied > self.rated_kw:
            raise ValueError("hvac.min_kw_when_occupied must not exceed rated_kw")
        if not self.comfort_min_c < self.comfort_max_c:
            raise ValueError("hvac.comfort_min_c must be below comfort_max_c")
        return self


class EvFleet(_Strict):
    vehicles: int = Field(ge=0)
    battery_kwh: float = Field(gt=0)
    charger_kw: float = Field(gt=0)
    arrival_time: time
    departure_time: time
    arrival_soc_pct: float = Field(ge=0, le=100)
    required_departure_soc_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _soc_and_feasible(self) -> EvFleet:
        if self.required_departure_soc_pct < self.arrival_soc_pct:
            raise ValueError("ev_fleet.required_departure_soc_pct must be >= arrival_soc_pct")
        if self.charging_window_hours * self.charger_kw < self.energy_needed_per_vehicle_kwh:
            raise ValueError("ev_fleet: charger cannot deliver the required energy within the parking window")
        return self

    @property
    def charging_window_hours(self) -> float:
        """Hours parked between arrival and departure (handles overnight wrap)."""
        a = self.arrival_time.hour * 60 + self.arrival_time.minute
        d = self.departure_time.hour * 60 + self.departure_time.minute
        return ((d - a) % (24 * 60)) / 60

    @property
    def energy_needed_per_vehicle_kwh(self) -> float:
        return self.battery_kwh * (self.required_departure_soc_pct - self.arrival_soc_pct) / 100


class Generator(_Strict):
    rated_kva: float = Field(gt=0)
    rated_kw: float = Field(gt=0)
    min_load_pct: float = Field(ge=0, lt=100)
    fuel_l_per_kwh: float = Field(gt=0)

    @field_validator("rated_kw")
    @classmethod
    def _kw_le_kva(cls, v: float, info) -> float:
        kva = info.data.get("rated_kva")
        if kva is not None and v > kva:
            raise ValueError("generator.rated_kw cannot exceed rated_kva")
        return v

    @property
    def min_kw(self) -> float:
        return self.rated_kw * self.min_load_pct / 100


class SiteConfig(_Strict):
    site: Site
    planning: Planning
    grid: Grid
    load: Load
    solar: Solar
    battery: Battery
    hvac: Hvac
    ev_fleet: EvFleet
    generator: Generator

    @model_validator(mode="after")
    def _cross_checks(self) -> SiteConfig:
        if self.hvac.rated_kw > self.load.peak_kw:
            raise ValueError("hvac.rated_kw is part of load.peak_kw and cannot exceed it")
        supply_kw = self.grid.max_import_kw + self.generator.rated_kw + self.battery.max_discharge_kw
        if self.load.peak_kw > supply_kw:
            raise ValueError("peak load cannot be supplied by grid + generator + battery")
        if self.load.peak_kw > self.generator.rated_kw + self.battery.max_discharge_kw:
            raise ValueError("during an outage, generator + battery cannot cover peak load")
        return self


def load_site_config(path: str | Path = DEFAULT_SITE_CONFIG) -> SiteConfig:
    """Read and validate a site YAML file. Raises ``pydantic.ValidationError`` on bad values."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return SiteConfig.model_validate(raw)
