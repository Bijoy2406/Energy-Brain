"""Asset agents (#16): every constraint block comes from config/site.yaml with the right shape."""

from __future__ import annotations

import inspect
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from energy_brain import assets as assets_pkg
from energy_brain.assets import (
    AssetAgent,
    battery_constraints,
    ev_constraints,
    generator_constraints,
    grid_constraints,
    hvac_constraints,
)
from energy_brain.config import Shift, SiteConfig, load_site_config
from energy_brain.interfaces import AssetProvider
from energy_brain.orchestrator import run_plan
from energy_brain.schemas import AssetConstraints
from energy_brain.stubs import StubAssetProvider, StubForecastAgent, StubPlanner, StubRisk, StubSignalAgent
from energy_brain.timeaxis import n_slots, slot_of, slot_times

DHAKA = ZoneInfo("Asia/Dhaka")
TUESDAY = datetime(2026, 11, 17, tzinfo=DHAKA)  # working day (same as the conftest ``start``)
FRIDAY = datetime(2026, 11, 20, tzinfo=DHAKA)  # weekly holiday in load.weekend_days
VICTORY_DAY = datetime(2026, 12, 16, tzinfo=DHAKA)  # Wednesday, Bangladesh public holiday


@pytest.fixture(scope="module")
def cfg() -> SiteConfig:
    return load_site_config()


def _with(cfg: SiteConfig, section: str, **changes) -> SiteConfig:
    """Copy of ``cfg`` with fields of one section replaced."""
    return cfg.model_copy(update={section: getattr(cfg, section).model_copy(update=changes)})


def _mask_by_clock(start: datetime, mask: list[bool]) -> dict[time, bool]:
    return {ts.time(): m for ts, m in zip(slot_times(start), mask, strict=True)}


# --------------------------------------------------------------------------- grid


def test_grid_matches_config(cfg):
    assert grid_constraints(cfg).max_import_kw == cfg.grid.max_import_kw


# --------------------------------------------------------------------------- battery


def test_battery_matches_config(cfg):
    b, c = battery_constraints(cfg), cfg.battery
    assert b.capacity_kwh == c.capacity_kwh
    assert b.max_charge_kw == c.max_charge_kw
    assert b.max_discharge_kw == c.max_discharge_kw
    assert b.round_trip_efficiency == c.round_trip_efficiency
    assert b.soc_min_kwh == pytest.approx(c.capacity_kwh * c.soc_min_pct / 100)
    assert b.soc_max_kwh == pytest.approx(c.capacity_kwh * c.soc_max_pct / 100)
    assert b.initial_soc_kwh == pytest.approx(c.capacity_kwh * c.initial_soc_pct / 100)


def test_battery_sanity_ranges(cfg):
    b = battery_constraints(cfg)
    assert 0 <= b.soc_min_kwh <= b.initial_soc_kwh <= b.soc_max_kwh <= b.capacity_kwh
    assert 0 < b.round_trip_efficiency <= 1


# --------------------------------------------------------------------------- generator


def test_generator_matches_config(cfg):
    g, c = generator_constraints(cfg), cfg.generator
    assert g.rated_kw == c.rated_kw
    assert g.min_kw == pytest.approx(c.rated_kw * c.min_load_pct / 100)
    assert g.fuel_l_per_kwh == c.fuel_l_per_kwh
    assert 0 <= g.min_kw <= g.rated_kw


# --------------------------------------------------------------------------- HVAC


def test_hvac_matches_config(cfg):
    h, c = hvac_constraints(TUESDAY, cfg), cfg.hvac
    assert h.rated_kw == c.rated_kw
    assert h.min_kw_when_occupied == c.min_kw_when_occupied
    assert (h.comfort_min_c, h.comfort_max_c) == (c.comfort_min_c, c.comfort_max_c)
    assert h.max_precool_hours == c.max_precool_hours
    assert 0 <= h.min_kw_when_occupied <= h.rated_kw
    assert len(h.occupied) == n_slots()


def test_hvac_occupied_follows_shifts_on_working_day(cfg):
    occ = hvac_constraints(TUESDAY, cfg).occupied
    first = min(slot_of(s.start, TUESDAY) for s in cfg.load.shifts)
    end = max(slot_of(s.end, TUESDAY) for s in cfg.load.shifts)  # default shifts are contiguous 08-20
    assert occ == [first <= i < end for i in range(n_slots())]
    by_clock = _mask_by_clock(TUESDAY, occ)
    assert not by_clock[time(7, 45)] and by_clock[time(8, 0)] and by_clock[time(19, 45)]
    assert not by_clock[time(20, 0)]


@pytest.mark.parametrize("day", [FRIDAY, VICTORY_DAY], ids=["friday", "public-holiday"])
def test_hvac_unoccupied_on_off_days(cfg, day):
    assert not any(hvac_constraints(day, cfg).occupied)


def test_hvac_partial_slot_counts_as_occupied(cfg):
    c = _with(cfg, "load", shifts=[Shift(start=time(8, 10), end=time(9, 5))])
    by_clock = _mask_by_clock(TUESDAY, hvac_constraints(TUESDAY, c).occupied)
    assert by_clock[time(8, 0)] and by_clock[time(9, 0)]
    assert not by_clock[time(7, 45)] and not by_clock[time(9, 15)]


def test_hvac_night_shift_wraps_midnight(cfg):
    c = _with(cfg, "load", shifts=[Shift(start=time(22, 0), end=time(6, 0))])
    by_clock = _mask_by_clock(TUESDAY, hvac_constraints(TUESDAY, c).occupied)
    assert by_clock[time(0, 0)] and by_clock[time(5, 45)] and by_clock[time(22, 0)]
    assert not by_clock[time(6, 0)] and not by_clock[time(21, 45)]


# --------------------------------------------------------------------------- EV fleet


def test_ev_matches_config(cfg):
    e, c = ev_constraints(TUESDAY, cfg), cfg.ev_fleet
    assert e.charger_kw == pytest.approx(c.vehicles * c.charger_kw)
    assert e.energy_required_kwh == pytest.approx(
        c.vehicles * c.battery_kwh * (c.required_departure_soc_pct - c.arrival_soc_pct) / 100
    )
    assert e.arrival_slot == slot_of(c.arrival_time, TUESDAY)
    assert e.departure_slot == slot_of(c.departure_time, TUESDAY)
    assert len(e.plugged_in) == n_slots()


def test_ev_window_wrapping_midnight_mask(cfg):
    """Default 18:00-07:00: plugged from slot 0 up to departure and from arrival to the end of the day."""
    e = ev_constraints(TUESDAY, cfg)
    assert e.arrival_slot > e.departure_slot  # wraps
    assert e.plugged_in == [i >= e.arrival_slot or i < e.departure_slot for i in range(n_slots())]
    by_clock = _mask_by_clock(TUESDAY, e.plugged_in)
    assert by_clock[time(0, 0)] and by_clock[time(6, 45)] and by_clock[time(18, 0)] and by_clock[time(23, 45)]
    assert not by_clock[time(7, 0)] and not by_clock[time(17, 45)]


def test_ev_mask_with_noon_start_is_contiguous(cfg):
    """Same wall-clock window, day starting at 12:00: in slot indices the window no longer wraps."""
    noon = datetime(2026, 11, 17, 12, 0, tzinfo=DHAKA)
    e = ev_constraints(noon, cfg)
    assert e.arrival_slot < e.departure_slot
    assert e.plugged_in == [e.arrival_slot <= i < e.departure_slot for i in range(n_slots())]


def test_ev_non_wrapping_window(cfg):
    c = _with(cfg, "ev_fleet", arrival_time=time(9, 0), departure_time=time(15, 0))
    e = ev_constraints(TUESDAY, c)
    assert e.plugged_in == [e.arrival_slot <= i < e.departure_slot for i in range(n_slots())]


def test_ev_off_boundary_time_is_rejected(cfg):
    c = _with(cfg, "ev_fleet", arrival_time=time(18, 10))
    with pytest.raises(ValueError, match="slot boundary"):
        ev_constraints(TUESDAY, c)


def test_ev_empty_window_is_rejected(cfg):
    c = _with(cfg, "ev_fleet", arrival_time=time(7, 0), departure_time=time(7, 0))
    with pytest.raises(ValueError, match="empty"):
        ev_constraints(TUESDAY, c)


def test_ev_required_energy_is_deliverable(cfg):
    e = ev_constraints(TUESDAY, cfg)
    assert e.energy_required_kwh <= e.charger_kw * sum(e.plugged_in) * cfg.planning.slot_minutes / 60


# --------------------------------------------------------------------------- aggregated agent


def test_agent_satisfies_protocol():
    assert isinstance(AssetAgent(), AssetProvider)


@pytest.mark.parametrize("day", [TUESDAY, FRIDAY], ids=["working-day", "friday"])
def test_agent_output_validates_and_round_trips(day):
    out = AssetAgent().run(day)
    assert isinstance(out, AssetConstraints)
    assert out.start == day
    assert AssetConstraints.model_validate_json(out.model_dump_json()) == out


def test_agent_converts_start_to_dhaka():
    utc_start = datetime(2026, 11, 16, 18, 0, tzinfo=ZoneInfo("UTC"))  # 00:00 Dhaka on Nov 17
    assert AssetAgent().run(utc_start).start == TUESDAY


def test_agent_rejects_naive_start():
    with pytest.raises(ValueError, match="timezone-aware"):
        AssetAgent().run(datetime(2026, 11, 17))


def test_agent_uses_given_config(cfg):
    c = _with(cfg, "battery", capacity_kwh=2000.0)
    assert AssetAgent(c).run(TUESDAY).battery.capacity_kwh == 2000.0


def test_no_parameter_to_relax_hard_constraints():
    """Comfort band, EV departure energy and SoC limits come only from config: no override arguments."""
    for fn in (battery_constraints, generator_constraints, grid_constraints):
        assert list(inspect.signature(fn).parameters) == ["config"]
    for fn in (hvac_constraints, ev_constraints):
        assert list(inspect.signature(fn).parameters) == ["start", "config"]
    assert list(inspect.signature(AssetAgent.run).parameters) == ["self", "start"]
    assert set(assets_pkg.__all__) == {
        "AssetAgent",
        "battery_constraints",
        "ev_constraints",
        "generator_constraints",
        "grid_constraints",
        "hvac_constraints",
    }


# --------------------------------------------------------------------------- stub compatibility


def _shape(model) -> dict:
    """Field names, value types and list lengths of a (nested) pydantic model."""
    out = {}
    for name in type(model).model_fields:
        value = getattr(model, name)
        if hasattr(type(value), "model_fields"):
            out[name] = _shape(value)
        elif isinstance(value, list):
            out[name] = ("list", len(value), {type(v) for v in value})
        else:
            out[name] = type(value)
    return out


@pytest.mark.parametrize("day", [TUESDAY, FRIDAY], ids=["working-day", "friday"])
def test_same_shape_as_stub(day):
    assert _shape(AssetAgent().run(day)) == _shape(StubAssetProvider().run(day))


def test_matches_stub_values_on_working_day():
    """On a working day with slot-aligned config times the real agent agrees with the stub exactly."""
    assert AssetAgent().run(TUESDAY) == StubAssetProvider().run(TUESDAY)


def test_friday_differs_from_stub_only_in_occupancy():
    real, stub = AssetAgent().run(FRIDAY), StubAssetProvider().run(FRIDAY)
    assert real.model_copy(update={"hvac": stub.hvac}) == stub
    assert not any(real.hvac.occupied) and any(stub.hvac.occupied)


def test_orchestrator_runs_with_real_assets():
    result = run_plan(
        TUESDAY,
        weight=0.5,
        forecast_agent=StubForecastAgent(),
        signal_agent=StubSignalAgent(),
        assets=AssetAgent(),
        planner=StubPlanner(),
        risk=StubRisk(),
    )
    assert result.start == TUESDAY
    assert len(result.schedule.hvac_kw) == n_slots()
