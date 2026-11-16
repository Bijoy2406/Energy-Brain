"""Smoke tests for the project setup (issue #1) and the site spec (issue #2)."""

import importlib

import pytest
import yaml
from pydantic import ValidationError

from energy_brain.config import DEFAULT_SITE_CONFIG, SiteConfig, load_site_config

SUBPACKAGES = ["data", "forecast", "signals", "assets", "planner", "risk", "explainer", "evaluator", "api"]


def test_package_imports():
    import energy_brain

    assert energy_brain.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_import(name):
    importlib.import_module(f"energy_brain.{name}")


def test_site_config_loads():
    cfg = load_site_config()
    assert cfg.site.timezone == "Asia/Dhaka"
    assert cfg.planning.n_slots == 96
    assert cfg.load.weekend_days == ["Friday"]


def test_derived_values():
    cfg = load_site_config()
    assert cfg.ev_fleet.charging_window_hours == pytest.approx(13.0)  # 18:00 -> 07:00 overnight
    assert cfg.ev_fleet.energy_needed_per_vehicle_kwh == pytest.approx(24.0)
    assert cfg.generator.min_kw == pytest.approx(240.0)


def _raw():
    with open(DEFAULT_SITE_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize(
    "section,key,bad",
    [
        ("battery", "soc_min_pct", 99),          # min above max
        ("battery", "round_trip_efficiency", 1.2),
        ("hvac", "comfort_max_c", 20),           # below comfort_min
        ("generator", "rated_kw", 2000),         # above rated_kva
        ("ev_fleet", "charger_kw", 0.5),         # cannot charge in time
        ("load", "peak_kw", 5000),               # cannot be supplied
    ],
)
def test_invalid_values_rejected(section, key, bad):
    raw = _raw()
    raw[section][key] = bad
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(raw)


def test_unknown_key_rejected():
    raw = _raw()
    raw["battery"]["capacity_kwhh"] = 1  # typo must not be silently ignored
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(raw)
