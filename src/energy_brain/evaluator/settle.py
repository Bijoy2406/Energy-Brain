"""Settle a schedule against the day's actual solar and load (#20).

A plan is made from forecasts; the day is paid for at what really happened. :func:`settle` replays any
``Schedule`` (optimiser or baseline) with the Risk agent's reactive-operator recourse
(``risk.stress_replay``: curtailed solar, cut charging, extra grid, extra battery, generator; never HVAC
or EV) using an "actual" ``Forecast`` whose P10 = P50 = P90 = the measured series. The same function
settles every method, so they are judged by one rule.

The settled ``Schedule`` balances in every slot by construction:

* ``base_load_kw`` = actual base load served + energy the replay had to dump (generator minimum-load surplus
  or a planned discharge that was no longer needed and had nowhere to go). Dumped energy is reported in
  ``dumped_kw``; it is wasted, not consumed.
* Unserved energy (``unserved_kw``) is taken off the base load first, then EV charging, then HVAC, so the
  settled flows show only what was really supplied. It is always reported, never dropped.
* ``solar_used_kw`` is what closes the balance (at most the actual solar); the rest is curtailed.

Cost and CO2 come from ``accounting.compute_totals`` only. The **adjusted** figures add the cost and CO2 of
refilling any end-of-day battery shortfall (``initial_soc - soc[-1]``, if positive) from the grid at the day's
lowest tariff (off-peak) and that slot's grid CO2 factor, divided by the one-way efficiency; a surplus is not
credited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from energy_brain.accounting import compute_totals
from energy_brain.risk import stress_replay
from energy_brain.schemas import (
    AssetConstraints,
    Forecast,
    PlanResult,
    QuantileSeries,
    Schedule,
    SignalTable,
    Totals,
)
from energy_brain.timeaxis import slot_hours

#: kW tolerance for the consistency checks of the settled flows.
TOL = 1e-6


@dataclass(frozen=True)
class Settlement:
    """Outcome of :func:`settle` (kWh unless the name says otherwise)."""

    schedule: Schedule
    totals: Totals
    unserved_kw: list[float]
    dumped_kw: list[float]
    end_soc_delta_kwh: float  # soc[-1] - initial (negative = battery left emptier than it started)
    end_soc_shortfall_kwh: float  # max(0, -end_soc_delta)
    adjusted_cost_bdt: float
    adjusted_co2_kg: float
    grid_extra_kwh: float
    discharge_extra_kwh: float
    generator_extra_kwh: float
    charge_cut_kwh: float

    @property
    def unserved_kwh(self) -> float:
        return sum(self.unserved_kw) * slot_hours()

    @property
    def dumped_kwh(self) -> float:
        return sum(self.dumped_kw) * slot_hours()


def actual_forecast(start: datetime, solar_kw: list[float], load_kw: list[float]) -> Forecast:
    """A ``Forecast`` whose three quantiles all equal the measured series (for replay and checks)."""
    solar = [max(0.0, float(v)) for v in solar_kw]
    load = [max(0.0, float(v)) for v in load_kw]
    return Forecast(
        start=start,
        solar=QuantileSeries(p10_kw=solar, p50_kw=solar, p90_kw=solar),
        load=QuantileSeries(p10_kw=load, p50_kw=load, p90_kw=load),
        note="actual measured solar and base load (evaluator settlement)",
    )


def settle(
    schedule: Schedule,
    actual: Forecast,
    signals: SignalTable,
    assets: AssetConstraints,
) -> Settlement:
    """Settle ``schedule`` against ``actual`` (from :func:`actual_forecast`); see the module docstring.

    Raises ``ValueError`` if the schedule does not balance (an unsafe plan's diagnostic schedule): such a
    schedule cannot be executed, so it is not settled.
    """
    schedule.check_energy_balance(tol=1e-3)
    h = slot_hours()
    b = assets.battery
    eta = b.one_way_efficiency
    wrapper = PlanResult(
        start=actual.start,
        weight=0.0,
        schedule=schedule,
        totals=compute_totals(schedule, signals, assets),
        status="optimal",
    )
    r = stress_replay(wrapper, actual, signals, assets, solar="p50", load="p50")

    sun, dem = actual.solar.p50_kw, actual.load.p50_kw
    base, hvac, ev, used, curt, unserved, dumped = ([] for _ in range(7))
    for t in range(len(sun)):
        short = r.unserved_kw[t]
        cut_base = min(short, dem[t])
        cut_ev = min(short - cut_base, schedule.ev_charge_kw[t])
        cut_hvac = min(short - cut_base - cut_ev, schedule.hvac_kw[t])
        if short - cut_base - cut_ev - cut_hvac > 1e-3:
            raise ValueError(f"slot {t}: unserved {short:.3f} kW exceeds the demand it could come from")
        b_t = dem[t] - cut_base
        ev_t = schedule.ev_charge_kw[t] - cut_ev
        hv_t = schedule.hvac_kw[t] - cut_hvac
        supply = r.grid_kw[t] + r.battery_discharge_kw[t] + r.generator_kw[t]
        need_solar = b_t + hv_t + ev_t + r.battery_charge_kw[t] - supply
        if need_solar > sun[t] + 1e-3:
            raise ValueError(f"slot {t}: replay needs {need_solar:.3f} kW solar, only {sun[t]:.3f} available")
        u = min(max(need_solar, 0.0), sun[t])
        dump = max(0.0, -need_solar)
        base.append(b_t + dump)
        hvac.append(hv_t)
        ev.append(ev_t)
        used.append(u)
        curt.append(sun[t] - u)
        unserved.append(short)
        dumped.append(dump)

    settled = Schedule(
        grid_import_kw=list(r.grid_kw),
        solar_used_kw=used,
        solar_curtailed_kw=curt,
        battery_charge_kw=list(r.battery_charge_kw),
        battery_discharge_kw=list(r.battery_discharge_kw),
        battery_soc_kwh=[max(0.0, v) for v in r.soc_kwh],
        hvac_kw=hvac,
        ev_charge_kw=ev,
        generator_kw=list(r.generator_kw),
        base_load_kw=base,
    )
    settled.check_energy_balance(tol=1e-3)
    totals = compute_totals(settled, signals, assets)

    delta = settled.battery_soc_kwh[-1] - b.initial_soc_kwh
    shortfall = max(0.0, -delta)
    cheapest = min(range(len(sun)), key=lambda t: (signals.tariff_bdt_per_kwh[t], t))
    refill_kwh = shortfall / eta
    return Settlement(
        schedule=settled,
        totals=totals,
        unserved_kw=unserved,
        dumped_kw=dumped,
        end_soc_delta_kwh=delta,
        end_soc_shortfall_kwh=shortfall,
        adjusted_cost_bdt=totals.cost_bdt + refill_kwh * signals.tariff_bdt_per_kwh[cheapest],
        adjusted_co2_kg=totals.co2_kg + refill_kwh * signals.grid_co2_kg_per_kwh[cheapest],
        grid_extra_kwh=sum(r.grid_extra_kw) * h,
        discharge_extra_kwh=sum(r.discharge_extra_kw) * h,
        generator_extra_kwh=sum(r.generator_extra_kw) * h,
        charge_cut_kwh=sum(r.charge_cut_kw) * h,
    )
