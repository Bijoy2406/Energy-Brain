"""STUB DATA - PLACEHOLDERS ONLY, NEVER USE IN THE PITCH.

Placeholder agents (issue #13) so every teammate can develop against ``energy_brain.interfaces`` before the
phase-1 data (#5-#12) and the real agents (#14-#18) exist. Every number marked ``PLACEHOLDER`` is invented
for plumbing tests only: not sourced, not calibrated, not a result. Replace with the real agents.
"""

from __future__ import annotations

import math
import time as _time
from datetime import datetime, time

from energy_brain.accounting import compute_totals
from energy_brain.config import SiteConfig, load_site_config
from energy_brain.schemas import (
    EPS,
    AssetConstraints,
    BatteryConstraints,
    EvConstraints,
    Forecast,
    GeneratorConstraints,
    GridConstraints,
    HvacConstraints,
    PlanResult,
    QuantileSeries,
    RiskReport,
    Schedule,
    SignalTable,
)
from energy_brain.timeaxis import n_slots, slot_hours, slot_minutes, slot_of, slot_times

# PLACEHOLDER signal values (not sourced; real values come from #8 / #15).
STUB_OFFPEAK_TARIFF_BDT_PER_KWH = 9.0  # PLACEHOLDER
STUB_PEAK_TARIFF_BDT_PER_KWH = 13.5  # PLACEHOLDER, applies 17:00-23:00
STUB_PEAK_START, STUB_PEAK_END = time(17, 0), time(23, 0)  # PLACEHOLDER
STUB_GRID_CO2_KG_PER_KWH = 0.62  # PLACEHOLDER flat factor
STUB_DIESEL_PRICE_BDT_PER_L = 110.0  # PLACEHOLDER
STUB_DIESEL_CO2_KG_PER_L = 2.68  # PLACEHOLDER
STUB_OUTAGE_START, STUB_OUTAGE_END = time(13, 0), time(14, 0)  # PLACEHOLDER one 1-hour load-shedding block

# PLACEHOLDER profile shape parameters.
STUB_SUNRISE_H, STUB_SUNSET_H = 6.0, 18.0  # PLACEHOLDER day length
STUB_SOLAR_PEAK_FRACTION = 0.8  # PLACEHOLDER: peak AC output / (kWp x (1 - losses))
STUB_OVERTIME_LOAD_FRACTION = 0.7  # PLACEHOLDER: later shifts run at 70% of (peak - base) above base


def _cfg(config: SiteConfig | None) -> SiteConfig:
    return config if config is not None else load_site_config()


def _clock_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _in_window(t: time, start: time, end: time) -> bool:
    """True if wall-clock ``t`` is in [start, end), handling windows that wrap midnight."""
    m, a, b = _clock_minutes(t), _clock_minutes(start), _clock_minutes(end)
    return a <= m < b if a < b else (m >= a or m < b)


def _slot_mid_hours(start: datetime) -> list[float]:
    """Wall-clock hour (0-24) at the middle of each slot."""
    half = slot_minutes() / 2
    return [((ts.hour * 60 + ts.minute + half) / 60) % 24 for ts in slot_times(start)]


def _solar_p50(start: datetime, cfg: SiteConfig) -> list[float]:
    peak = cfg.solar.capacity_kwp * (1 - cfg.solar.system_losses_pct / 100) * STUB_SOLAR_PEAK_FRACTION
    day = STUB_SUNSET_H - STUB_SUNRISE_H
    return [
        peak * math.sin(math.pi * (h - STUB_SUNRISE_H) / day) if STUB_SUNRISE_H < h < STUB_SUNSET_H else 0.0
        for h in _slot_mid_hours(start)
    ]


def _load_p50(start: datetime, cfg: SiteConfig) -> list[float]:
    base, peak = cfg.load.base_kw, cfg.load.peak_kw
    out = []
    for ts in slot_times(start):
        value = base
        for i, shift in enumerate(cfg.load.shifts):
            if _in_window(ts.time(), shift.start, shift.end):
                value = peak if i == 0 else base + STUB_OVERTIME_LOAD_FRACTION * (peak - base)
                break
        out.append(value)
    return out


class StubForecastAgent:
    """PLACEHOLDER forecast: midday solar bell curve and shift-shaped load. Ignores weekends and weather."""

    solar_p10_factor = 0.7  # PLACEHOLDER
    solar_p50_factor = 1.0
    solar_p90_factor = 1.2  # PLACEHOLDER

    def __init__(self, config: SiteConfig | None = None) -> None:
        self.config = _cfg(config)

    def run(self, start: datetime) -> Forecast:
        base = _solar_p50(start, self.config)
        load = _load_p50(start, self.config)
        p50 = [self.solar_p50_factor * v for v in base]
        return Forecast(
            start=start,
            solar=QuantileSeries(
                p10_kw=[self.solar_p10_factor * v for v in base],
                p50_kw=p50,
                p90_kw=[max(self.solar_p90_factor * v, p) for v, p in zip(base, p50, strict=True)],
            ),
            load=QuantileSeries(
                p10_kw=[0.9 * v for v in load],  # PLACEHOLDER band
                p50_kw=load,
                p90_kw=[1.1 * v for v in load],  # PLACEHOLDER band
            ),
            note="STUB forecast - placeholder data, never use in the pitch",
        )


class StubCloudyForecastAgent(StubForecastAgent):
    """PLACEHOLDER cloudy day: solar P50 x 0.5 and P10 x 0.2, for exercising the Risk agent."""

    solar_p10_factor = 0.2
    solar_p50_factor = 0.5


class StubSignalAgent:
    """PLACEHOLDER signals: two-level tariff, flat grid CO2, fixed diesel, one 1-hour outage."""

    def run(self, start: datetime) -> SignalTable:
        n = n_slots()
        times = [ts.time() for ts in slot_times(start)]
        return SignalTable(
            start=start,
            tariff_bdt_per_kwh=[
                STUB_PEAK_TARIFF_BDT_PER_KWH
                if _in_window(t, STUB_PEAK_START, STUB_PEAK_END)
                else STUB_OFFPEAK_TARIFF_BDT_PER_KWH
                for t in times
            ],
            grid_co2_kg_per_kwh=[STUB_GRID_CO2_KG_PER_KWH] * n,
            diesel_price_bdt_per_l=[STUB_DIESEL_PRICE_BDT_PER_L] * n,
            diesel_co2_kg_per_l=[STUB_DIESEL_CO2_KG_PER_L] * n,
            outage=[_in_window(t, STUB_OUTAGE_START, STUB_OUTAGE_END) for t in times],
        )


def stub_assets(start: datetime, config: SiteConfig | None = None) -> AssetConstraints:
    """AssetConstraints read straight from ``config/site.yaml``. Simplified; the real mapping is issue #16."""
    cfg = _cfg(config)
    b, hv, ev, gen = cfg.battery, cfg.hvac, cfg.ev_fleet, cfg.generator
    times = [ts.time() for ts in slot_times(start)]
    occupied = [any(_in_window(t, s.start, s.end) for s in cfg.load.shifts) for t in times]
    plugged = [_in_window(t, ev.arrival_time, ev.departure_time) for t in times]
    return AssetConstraints(
        start=start,
        grid=GridConstraints(max_import_kw=cfg.grid.max_import_kw),
        battery=BatteryConstraints(
            capacity_kwh=b.capacity_kwh,
            max_charge_kw=b.max_charge_kw,
            max_discharge_kw=b.max_discharge_kw,
            round_trip_efficiency=b.round_trip_efficiency,
            soc_min_kwh=b.capacity_kwh * b.soc_min_pct / 100,
            soc_max_kwh=b.capacity_kwh * b.soc_max_pct / 100,
            initial_soc_kwh=b.capacity_kwh * b.initial_soc_pct / 100,
        ),
        hvac=HvacConstraints(
            rated_kw=hv.rated_kw,
            min_kw_when_occupied=hv.min_kw_when_occupied,
            occupied=occupied,
            comfort_min_c=hv.comfort_min_c,
            comfort_max_c=hv.comfort_max_c,
            max_precool_hours=hv.max_precool_hours,
        ),
        ev=EvConstraints(
            plugged_in=plugged,
            charger_kw=ev.vehicles * ev.charger_kw,
            energy_required_kwh=ev.vehicles * ev.energy_needed_per_vehicle_kwh,
            arrival_slot=slot_of(ev.arrival_time, start, cfg),
            departure_slot=slot_of(ev.departure_time, start, cfg),
        ),
        generator=GeneratorConstraints(
            rated_kw=gen.rated_kw, min_kw=gen.min_kw, fuel_l_per_kwh=gen.fuel_l_per_kwh
        ),
    )


class StubAssetProvider:
    """``AssetProvider`` wrapper around :func:`stub_assets`."""

    def __init__(self, config: SiteConfig | None = None) -> None:
        self.config = _cfg(config)

    def run(self, start: datetime) -> AssetConstraints:
        return stub_assets(start, self.config)


def _ev_profile(ev: EvConstraints) -> list[float]:
    """Charge at full fleet power from arrival onwards (wrapping) until the required energy is delivered."""
    n, h = len(ev.plugged_in), slot_hours()
    out = [0.0] * n
    remaining = ev.energy_required_kwh
    for k in range(n):
        t = (ev.arrival_slot + k) % n
        if remaining <= EPS:
            break
        if ev.plugged_in[t]:
            kw = min(ev.charger_kw, remaining / h)
            out[t] = kw
            remaining -= kw * h
    return out


class StubPlanner:
    """PLACEHOLDER planner, NOT an optimiser: solar first, then grid; generator during outages.

    HVAC runs at its occupied minimum, EVs charge as early as possible, the battery only absorbs generator
    minimum-load surplus or covers generator shortfall during an outage. Ignores ``weight`` and
    ``reserve_margin_kwh`` (recorded in the result only). Satisfies the energy balance exactly.
    """

    def plan(
        self,
        forecast: Forecast,
        signals: SignalTable,
        assets: AssetConstraints,
        weight: float,
        reserve_margin_kwh: float = 0.0,
    ) -> PlanResult:
        t0 = _time.perf_counter()
        n, h = n_slots(), slot_hours()
        eta = assets.battery.one_way_efficiency
        gen_c = assets.generator
        ev_kw = _ev_profile(assets.ev)
        rows: dict[str, list[float]] = {k: [] for k in Schedule.model_fields}
        soc = assets.battery.initial_soc_kwh
        notes = ["STUB planner - placeholder schedule, never use in the pitch"]
        for t in range(n):
            base = forecast.load.p50_kw[t]
            hvac = assets.hvac.min_kw_when_occupied if assets.hvac.occupied[t] else 0.0
            demand = base + hvac + ev_kw[t]
            solar = forecast.solar.p50_kw[t]
            used = min(solar, demand)
            residual = demand - used
            grid = gen = charge = discharge = 0.0
            if not signals.outage[t]:
                grid = residual
                if grid > assets.grid.max_import_kw + EPS:
                    notes.append(f"slot {t}: grid import {grid:.0f} kW exceeds limit (stub does not fix)")
            elif residual > EPS:
                gen = min(max(residual, gen_c.min_kw), gen_c.rated_kw)
                charge = max(gen - residual, 0.0)
                discharge = max(residual - gen, 0.0)
            soc += eta * charge * h - discharge * h / eta
            for key, val in (
                ("grid_import_kw", grid),
                ("solar_used_kw", used),
                ("solar_curtailed_kw", solar - used),
                ("battery_charge_kw", charge),
                ("battery_discharge_kw", discharge),
                ("battery_soc_kwh", soc),
                ("hvac_kw", hvac),
                ("ev_charge_kw", ev_kw[t]),
                ("generator_kw", gen),
                ("base_load_kw", base),
            ):
                rows[key].append(val)
        schedule = Schedule(**rows)
        return PlanResult(
            start=forecast.start,
            weight=weight,
            schedule=schedule,
            totals=compute_totals(schedule, signals, assets),
            status="feasible",
            solve_seconds=_time.perf_counter() - t0,
            reserve_margin_kwh=reserve_margin_kwh,
            fallback_used=forecast.fallback_used,
            notes=notes,
        )


class StubRisk:
    """PLACEHOLDER risk agent that always passes."""

    def check(
        self, plan: PlanResult, forecast: Forecast, signals: SignalTable, assets: AssetConstraints
    ) -> RiskReport:
        return RiskReport(ok=True, reserve_margin_kwh=plan.reserve_margin_kwh)


class FailOnceRisk:
    """PLACEHOLDER risk agent: fails the first call (asking for ``margin_kwh`` reserve), passes afterwards."""

    def __init__(self, margin_kwh: float = 100.0) -> None:
        self.margin_kwh = margin_kwh
        self.calls = 0

    def check(
        self, plan: PlanResult, forecast: Forecast, signals: SignalTable, assets: AssetConstraints
    ) -> RiskReport:
        self.calls += 1
        if self.calls == 1:
            return RiskReport(
                ok=False,
                reasons=["STUB: battery reserve too low at P10 solar (placeholder reason)"],
                reserve_margin_kwh=plan.reserve_margin_kwh + self.margin_kwh,
            )
        return RiskReport(ok=True, reserve_margin_kwh=plan.reserve_margin_kwh)
