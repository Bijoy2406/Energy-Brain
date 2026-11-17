"""Tests for economic and carbon signals data and configuration (Issue #8)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from energy_brain.config import load_site_config
from energy_brain.data.signals_lookup import (
    DEFAULT_SIGNALS_CONFIG,
    SignalsConfig,
    get_tariff_for_datetime,
    load_signals_config,
)

DHAKA = ZoneInfo("Asia/Dhaka")


def test_signals_config_loads_and_validates():
    assert DEFAULT_SIGNALS_CONFIG.exists()
    cfg = load_signals_config()

    # Tariff validation
    assert cfg.tariff.customer_class == "MT-3 (11kV Medium Tension Industry)"
    assert cfg.tariff.currency == "BDT"
    assert cfg.tariff.rates.off_peak_bdt_per_kwh == pytest.approx(8.49)
    assert cfg.tariff.rates.flat_bdt_per_kwh == pytest.approx(9.43)
    assert cfg.tariff.rates.peak_bdt_per_kwh == pytest.approx(11.78)

    # Tariff ordering: off-peak < flat < peak
    assert cfg.tariff.rates.off_peak_bdt_per_kwh < cfg.tariff.rates.flat_bdt_per_kwh
    assert cfg.tariff.rates.flat_bdt_per_kwh < cfg.tariff.rates.peak_bdt_per_kwh

    # Grid carbon validation
    assert cfg.grid_emission.factor_kg_per_kwh == pytest.approx(0.62)
    assert cfg.grid_emission.factor_tco2_per_mwh == pytest.approx(0.62)

    # Diesel validation
    assert cfg.diesel.price_bdt_per_l == pytest.approx(107.00)
    assert cfg.diesel.emission_factor_kg_per_l == pytest.approx(2.68)


def test_get_tariff_for_datetime():
    cfg = load_signals_config()

    # Peak: 17:00 to 23:00 (e.g. 18:30)
    peak_time = datetime(2024, 6, 1, 18, 30, tzinfo=DHAKA)
    assert get_tariff_for_datetime(peak_time, cfg) == pytest.approx(11.78)

    # Flat: 06:00 to 17:00 (e.g. 10:15)
    flat_time = datetime(2024, 6, 1, 10, 15, tzinfo=DHAKA)
    assert get_tariff_for_datetime(flat_time, cfg) == pytest.approx(9.43)

    # Off-peak: 23:00 to 06:00 (e.g. 02:45)
    off_peak_time = datetime(2024, 6, 1, 2, 45, tzinfo=DHAKA)
    assert get_tariff_for_datetime(off_peak_time, cfg) == pytest.approx(8.49)

    # Naive datetime must raise ValueError
    with pytest.raises(ValueError, match="timezone-aware"):
        get_tariff_for_datetime(datetime(2024, 6, 1, 12, 0), cfg)


def test_diesel_vs_grid_economic_differential():
    signals = load_signals_config()
    site = load_site_config()

    # Effective diesel cost per kWh = fuel_l_per_kwh * diesel_price
    diesel_cost_per_kwh = site.generator.fuel_l_per_kwh * signals.diesel.price_bdt_per_l
    assert diesel_cost_per_kwh == pytest.approx(0.27 * 107.00)  # 28.89 BDT/kWh

    # Must be strictly higher than peak grid tariff
    assert diesel_cost_per_kwh > signals.tariff.rates.peak_bdt_per_kwh * 2.0


def test_invalid_signals_config_rejected():
    with pytest.raises(ValidationError):
        SignalsConfig.model_validate({"tariff": {"invalid_key": 123}})
