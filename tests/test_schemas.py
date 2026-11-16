"""Schemas, time axis, accounting and stubs (issue #13)."""

from datetime import datetime, time, timedelta

import pytest
from pydantic import ValidationError

from energy_brain import stubs
from energy_brain.accounting import compute_totals
from energy_brain.interfaces import AssetProvider, ForecastAgent, PlannerAgent, RiskAgent, SignalAgent
from energy_brain.schemas import (
    BatteryConstraints,
    EvConstraints,
    Forecast,
    GeneratorConstraints,
    PlanResult,
    QuantileSeries,
    RiskReport,
    Schedule,
    SignalTable,
)
from energy_brain.timeaxis import ensure_start, n_slots, slot_hours, slot_of, slot_times

N = n_slots()


def zeros() -> list[float]:
    return [0.0] * N


def quantiles(p10=1.0, p50=2.0, p90=3.0, n=None) -> dict:
    n = N if n is None else n
    return {"p10_kw": [p10] * n, "p50_kw": [p50] * n, "p90_kw": [p90] * n}


# --------------------------------------------------------------------------- time axis


def test_time_axis(start):
    assert N == 96  # from config/site.yaml (15 min x 24 h), not hard-coded in the code
    assert slot_hours() == pytest.approx(0.25)
    times = slot_times(start)
    assert len(times) == N
    assert times[1] - times[0] == timedelta(minutes=15)
    assert times[-1].hour == 23 and times[-1].minute == 45
    assert str(times[0].tzinfo) == "Asia/Dhaka"
    assert slot_of(time(18, 0), start) == 72
    assert slot_of(time(7, 0), start) == 28


def test_start_converted_to_site_tz(start):
    from datetime import UTC

    utc = start.astimezone(UTC)
    assert ensure_start(utc) == start
    assert str(ensure_start(utc).tzinfo) == "Asia/Dhaka"


def test_naive_start_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        ensure_start(datetime(2026, 11, 17))


def test_off_slot_start_rejected(start):
    with pytest.raises(ValueError, match="slot boundary"):
        ensure_start(start + timedelta(minutes=7))


# --------------------------------------------------------------------------- validation


def test_quantiles_ok():
    q = QuantileSeries(**quantiles())
    assert len(q.p50_kw) == N


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (quantiles(n=95), "95 slots"),  # wrong length
        (quantiles(p10=-1.0), "greater than or equal"),  # negative
        (quantiles(p10=3.0, p50=2.0), "out of order"),  # p10 > p50
        (quantiles(p50=4.0, p90=3.0), "out of order"),  # p50 > p90
        (quantiles(p50=float("nan")), "finite"),  # NaN
    ],
)
def test_quantiles_rejected(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        QuantileSeries(**kwargs)


def test_quantile_lengths_must_match():
    q = quantiles()
    q["p90_kw"] = q["p90_kw"][:-1]
    with pytest.raises(ValidationError):
        QuantileSeries(**q)


def test_forecast_naive_start_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        Forecast(start=datetime(2026, 11, 17), solar=quantiles(), load=quantiles())


def test_forecast_unknown_field_rejected(start):
    with pytest.raises(ValidationError, match="extra"):
        Forecast(start=start, solar=quantiles(), load=quantiles(), confidence=0.9)


def test_models_are_frozen(forecast):
    with pytest.raises(ValidationError):
        forecast.fallback_used = True


def _signal_kwargs(start) -> dict:
    return {
        "start": start,
        "tariff_bdt_per_kwh": [10.0] * N,
        "grid_co2_kg_per_kwh": [0.5] * N,
        "diesel_price_bdt_per_l": [100.0] * N,
        "diesel_co2_kg_per_l": [2.5] * N,
        "outage": [False] * N,
    }


@pytest.mark.parametrize(
    "field,bad",
    [
        ("outage", [False] * (N - 1)),
        ("tariff_bdt_per_kwh", [-1.0] * N),
        ("grid_co2_kg_per_kwh", [0.5] * (N + 1)),
    ],
)
def test_signal_table_rejected(start, field, bad):
    kw = _signal_kwargs(start)
    kw[field] = bad
    with pytest.raises(ValidationError):
        SignalTable(**kw)


def test_battery_soc_order_rejected():
    with pytest.raises(ValidationError, match="soc_min_kwh"):
        BatteryConstraints(
            capacity_kwh=100,
            max_charge_kw=50,
            max_discharge_kw=50,
            round_trip_efficiency=0.9,
            soc_min_kwh=50,
            soc_max_kwh=90,
            initial_soc_kwh=10,
        )


def test_ev_infeasible_energy_rejected():
    plugged = [False] * N
    plugged[0] = True  # one slot x 10 kW x 0.25 h = 2.5 kWh deliverable
    with pytest.raises(ValidationError, match="exceeds deliverable"):
        EvConstraints(
            plugged_in=plugged, charger_kw=10, energy_required_kwh=5, arrival_slot=0, departure_slot=1
        )


def test_generator_min_above_rated_rejected():
    with pytest.raises(ValidationError):
        GeneratorConstraints(rated_kw=100, min_kw=200, fuel_l_per_kwh=0.3)


def test_schedule_negative_rejected(plan):
    data = plan.schedule.model_dump()
    data["hvac_kw"][3] = -5.0
    with pytest.raises(ValidationError):
        Schedule(**data)


def test_weight_out_of_range_rejected(plan):
    data = plan.model_dump()
    data["weight"] = 1.5
    with pytest.raises(ValidationError):
        PlanResult(**data)


def test_energy_balance_violation_detected(plan):
    data = plan.schedule.model_dump()
    data["grid_import_kw"][10] += 5.0
    bad = Schedule(**data)
    assert bad.energy_balance_residual_kw()[10] == pytest.approx(5.0)
    with pytest.raises(ValueError, match="slot 10"):
        bad.check_energy_balance(tol=1e-6)


# --------------------------------------------------------------------------- JSON round trip


@pytest.mark.parametrize("name", ["forecast", "signals", "assets", "plan"])
def test_json_round_trip(request, name):
    obj = request.getfixturevalue(name)
    back = type(obj).model_validate_json(obj.model_dump_json())
    assert back == obj


def test_json_round_trip_nested(plan):
    for obj in (
        plan.schedule,
        plan.totals,
        RiskReport(ok=False, reasons=["x"], reserve_margin_kwh=5, replans=2),
    ):
        assert type(obj).model_validate_json(obj.model_dump_json()) == obj
    with_risk = plan.model_copy(update={"risk": RiskReport(ok=True, replans=1)})
    assert PlanResult.model_validate_json(with_risk.model_dump_json()) == with_risk


# --------------------------------------------------------------------------- accounting


def test_compute_totals_hand_example(start, assets):
    # slot_hours = 0.25 h. Signals: tariff 10 BDT/kWh, grid 0.5 kg/kWh, diesel 100 BDT/L, 2.5 kg/L.
    # Generator fuel 0.25 L/kWh.
    # slot 0: grid 100 kW   -> 25 kWh  -> cost 25*10 = 250 BDT,         CO2 25*0.5 = 12.5 kg
    # slot 1: gen 400 kW    -> 100 kWh -> 100*0.25 = 25 L -> cost 25*100 = 2500 BDT, CO2 25*2.5 = 62.5 kg
    # slot 2: solar 200 kW  -> 50 kWh solar, no cost/CO2
    # totals: cost 2750 BDT, CO2 75 kg, grid 25 kWh, gen 100 kWh, 0.25 gen-hours, 25 L, solar 50 kWh
    signals = SignalTable(**_signal_kwargs(start))
    assets = assets.model_copy(
        update={"generator": GeneratorConstraints(rated_kw=800, min_kw=0, fuel_l_per_kwh=0.25)}
    )
    grid, gen, solar, base = zeros(), zeros(), zeros(), zeros()
    grid[0], gen[1], solar[2] = 100.0, 400.0, 200.0
    base[0], base[1], base[2] = 100.0, 400.0, 200.0
    z = zeros()
    sched = Schedule(
        grid_import_kw=grid,
        solar_used_kw=solar,
        solar_curtailed_kw=z,
        battery_charge_kw=z,
        battery_discharge_kw=z,
        battery_soc_kwh=z,
        hvac_kw=z,
        ev_charge_kw=z,
        generator_kw=gen,
        base_load_kw=base,
    )
    assert sched.check_energy_balance(1e-12) == 0.0
    t = compute_totals(sched, signals, assets)
    assert t.cost_bdt == pytest.approx(2750.0)
    assert t.co2_kg == pytest.approx(75.0)
    assert t.grid_kwh == pytest.approx(25.0)
    assert t.generator_kwh == pytest.approx(100.0)
    assert t.generator_hours == pytest.approx(0.25)
    assert t.diesel_litres == pytest.approx(25.0)
    assert t.solar_kwh == pytest.approx(50.0)
    assert t.slot_cost_bdt[:3] == pytest.approx([250.0, 2500.0, 0.0])
    assert t.slot_co2_kg[:3] == pytest.approx([12.5, 62.5, 0.0])


# --------------------------------------------------------------------------- stubs


def test_stub_module_is_marked():
    assert "STUB DATA - PLACEHOLDERS ONLY, NEVER USE IN THE PITCH" in stubs.__doc__


def test_stubs_satisfy_protocols():
    assert isinstance(stubs.StubForecastAgent(), ForecastAgent)
    assert isinstance(stubs.StubSignalAgent(), SignalAgent)
    assert isinstance(stubs.StubAssetProvider(), AssetProvider)
    assert isinstance(stubs.StubPlanner(), PlannerAgent)
    assert isinstance(stubs.StubRisk(), RiskAgent)
    assert isinstance(stubs.FailOnceRisk(), RiskAgent)


def test_stub_forecast_shape(forecast, start):
    solar = forecast.solar.p50_kw
    assert solar[0] == 0.0 and solar[-1] == 0.0  # night
    assert max(range(N), key=solar.__getitem__) in range(44, 52)  # peak around midday
    load = forecast.load.p50_kw
    assert min(load) == pytest.approx(250.0) and max(load) == pytest.approx(1200.0)


def test_stub_cloudy_forecast(start, forecast):
    cloudy = stubs.StubCloudyForecastAgent().run(start)
    assert sum(cloudy.solar.p50_kw) == pytest.approx(0.5 * sum(forecast.solar.p50_kw))
    assert sum(cloudy.solar.p10_kw) == pytest.approx(0.2 * sum(forecast.solar.p50_kw))


def test_stub_signals(signals):
    assert sum(signals.outage) * slot_hours() == pytest.approx(1.0)  # one 1-hour block
    assert max(signals.tariff_bdt_per_kwh) > min(signals.tariff_bdt_per_kwh)


def test_stub_schedule_balanced_and_feasible(plan, signals, assets):
    s = plan.schedule
    assert s.check_energy_balance(tol=1e-9) <= 1e-9
    for t in range(N):
        if signals.outage[t]:
            assert s.grid_import_kw[t] == 0.0
        assert s.grid_import_kw[t] <= assets.grid.max_import_kw
        assert s.ev_charge_kw[t] == 0.0 or assets.ev.plugged_in[t]
    assert sum(s.ev_charge_kw) * slot_hours() == pytest.approx(assets.ev.energy_required_kwh)
    assert plan.totals == compute_totals(s, signals, assets)
