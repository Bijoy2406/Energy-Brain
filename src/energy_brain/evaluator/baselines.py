"""Rule-based baselines for the backtest (#20): what a site runs today without Energy Brain.

Two baselines from PRD section 2, built on the same assets, signals, outages and hard constraints as the
optimiser (``planner.rules`` interprets every constraint, ``planner.checks.find_violations`` verifies the
result). Both read the ``solar`` / ``load`` P50 series of the ``Forecast`` they are given; the backtest passes
the day's ACTUAL solar and load (real-time rules need no forecast, so they get perfect information, which is
the conservative choice for the comparison).

=================  ===================================  =========================================
rule               ``solar_first`` (B1)                 ``time_of_use`` (B2)
=================  ===================================  =========================================
HVAC               occupied minimum                     same
EV                 full charger power from the first    off-peak required slots first (full
                   required slot until the energy is    power), then the other required slots
                   met (from 00:00, off-peak)
solar              serves load first, surplus charges   same
                   the battery, the rest is curtailed
battery charge     surplus solar only                   surplus solar, plus grid in off-peak slots:
                                                        up to ``soc_max`` before the first peak
                                                        slot, up to the starting SoC after the last
battery discharge  covers any load solar leaves, down   peak slots only, down to the backup
                   to the outage backup
outage             battery first (above the outage      same
                   backup), then the generator
                   (>= ``min_kw``; its surplus backs off
                   discharge, charges the battery, then
                   backs off solar); above its rating
                   the backup energy
outage backup      battery keeps (and grid-charges to)  same
                   what later outage slots need beyond
                   the generator rating
                   (``outage_backup_kwh``)
grid               the rest, up to ``max_import_kw``;   same
                   above it battery, then generator
end of day         SoC below the start: top up from     same
                   grid in the latest non-outage slots,
                   then replace the latest discharge
                   with grid
=================  ===================================  =========================================

Peak / off-peak come from the tariff signal itself: off-peak = slots at the day's lowest tariff, peak = slots
at its highest (no time-of-use if the tariff is flat).

**EV and the day window.** The vans park 18:00-07:00, so today's required energy is due in [00:00, 07:00)
(``rules.ev_required_slots``) and evening charging is for tomorrow. B1 therefore charges from 00:00, which is
off-peak; a literal "charge on arrival" at 18:00 would pay the peak tariff. The comparison is conservative:
it understates the savings by about 144 kWh x 3.29 BDT per day (fleet energy x peak minus off-peak tariff).
"""

from __future__ import annotations

from collections.abc import Callable

from energy_brain.planner import rules
from energy_brain.schemas import EPS, AssetConstraints, Forecast, Schedule, SignalTable
from energy_brain.timeaxis import slot_hours

#: Stable method names (used in the report).
SOLAR_FIRST = "baseline_solar_first"
TIME_OF_USE = "baseline_time_of_use"
BASELINES = (SOLAR_FIRST, TIME_OF_USE)


def tariff_periods(signals: SignalTable) -> tuple[list[bool], list[bool]]:
    """``(off_peak, peak)`` masks: slots at the day's lowest / highest tariff (both empty if flat)."""
    tariff = signals.tariff_bdt_per_kwh
    lo, hi = min(tariff), max(tariff)
    if hi - lo <= EPS:
        return [False] * len(tariff), [False] * len(tariff)
    return [v <= lo + EPS for v in tariff], [v >= hi - EPS for v in tariff]


def _ev_profile(assets: AssetConstraints, order: list[int]) -> list[float]:
    ev, h = assets.ev, slot_hours()
    out = [0.0] * len(ev.plugged_in)
    remaining = ev.energy_required_kwh
    for t in order:
        if remaining <= EPS:
            break
        kw = min(ev.charger_kw, remaining / h)
        out[t] = kw
        remaining -= kw * h
    return out


def outage_backup_kwh(
    demand_kw: list[float], solar_kw: list[float], signals: SignalTable, assets: AssetConstraints
) -> list[float]:
    """SoC (kWh, end of slot t) the baselines keep for later outage slots: an operator's backup reserve.

    In an outage slot the battery must cover ``demand - solar - generator rated_kw`` (if positive); this is
    summed backwards over the day's outage slots and ramped down through the slots before them at the charge
    rate each slot allows (battery limit and grid headroom above the load), so grid charging reaches it in
    time. Floor ``soc_min``, cap ``soc_max``. The
    outage calendar is a day-ahead signal, the same one the optimiser sees.
    """
    b, h = assets.battery, slot_hours()
    eta = b.one_way_efficiency
    n = len(demand_kw)
    need = [b.soc_min_kwh] * n
    for t in range(n - 2, -1, -1):
        nxt = t + 1
        if signals.outage[nxt]:
            short = max(0.0, demand_kw[nxt] - solar_kw[nxt] - assets.generator.rated_kw)
            need[t] = need[nxt] + min(short, b.max_discharge_kw) * h / eta
        else:
            rate = max(
                0.0, min(b.max_charge_kw, assets.grid.max_import_kw - (demand_kw[nxt] - solar_kw[nxt]))
            )
            need[t] = max(b.soc_min_kwh, need[nxt] - eta * rate * h)
        need[t] = min(need[t], b.soc_max_kwh)
    return need


def _dispatch(
    forecast: Forecast,
    signals: SignalTable,
    assets: AssetConstraints,
    ev: list[float],
    discharge_ok: Callable[[int], bool],
    grid_charge_target: Callable[[int], float],
) -> Schedule:
    """Shared slot-by-slot rule engine; the two baselines differ only in the callables and the EV profile.

    ``discharge_ok(t)``: battery may cover load in a non-outage slot t. ``grid_charge_target(t)``: SoC (kWh)
    the battery may be charged to from the grid in slot t (0 = no grid charging).
    """
    n, h = len(signals.outage), slot_hours()
    b, gen_c, grid_max = assets.battery, assets.generator, assets.grid.max_import_kw
    eta = b.one_way_efficiency
    hvac = rules.hvac_min_kw(assets.hvac)
    demand = [forecast.load.p50_kw[t] + hvac[t] + ev[t] for t in range(n)]
    backup = outage_backup_kwh(demand, forecast.solar.p50_kw, signals, assets)
    grid, used, curt, ch, dis, soc, gen = ([0.0] * n for _ in range(7))
    level = b.initial_soc_kwh
    for t in range(n):
        load, solar = demand[t], forecast.solar.p50_kw[t]
        charge_room = max(0.0, min(b.max_charge_kw, (b.soc_max_kwh - level) / (eta * h)))
        dis_room = max(0.0, min(b.max_discharge_kw, (level - b.soc_min_kwh) * eta / h))
        free_room = max(0.0, min(b.max_discharge_kw, (level - backup[t]) * eta / h))  # above the backup
        u = min(solar, load)
        residual = load - u
        c = min(solar - u, charge_room)  # surplus solar into the battery
        u += c
        d = g = q = 0.0
        if signals.outage[t]:
            d = min(residual, free_room)  # battery first (keeping what later outage slots need),
            rest = residual - d
            if rest > EPS:  # then the generator,
                q = min(max(rest, gen_c.min_kw), gen_c.rated_kw)
                if q < rest:  # above its rating: the backup energy (down to soc_min)
                    d += min(rest - q, dis_room - d)
                excess = max(0.0, q - (residual - d))
                back = min(excess, d)  # minimum-load surplus: back off discharge,
                d -= back
                excess -= back
                if d <= EPS:  # then charge the battery,
                    x = min(excess, charge_room - c)
                    c += x
                    excess -= x
                u -= min(excess, u)  # then back off solar
        else:
            g = residual
            target = max(grid_charge_target(t), backup[t])
            if target > EPS:  # grid charging never pushes import above the limit
                x = min(charge_room - c, max(0.0, (target - level) / (eta * h) - c), max(0.0, grid_max - g))
                if x > EPS:
                    c += x
                    g += x
            if c <= EPS and discharge_ok(t):
                d = min(g, free_room)
                g -= d
            over = g - grid_max
            if over > EPS and c <= EPS:  # import limit: battery, then generator
                x = min(over, dis_room - d)
                d += x
                g -= x
                over -= x
            if over > EPS:
                q = min(max(over, gen_c.min_kw), gen_c.rated_kw)
                g = max(0.0, g - q)
        level += eta * c * h - d * h / eta
        grid[t], used[t], curt[t], ch[t], dis[t], soc[t], gen[t] = g, u, solar - u, c, d, level, q

    _end_of_day(signals, assets, grid, ch, dis, soc)
    return Schedule(
        grid_import_kw=grid,
        solar_used_kw=used,
        solar_curtailed_kw=curt,
        battery_charge_kw=ch,
        battery_discharge_kw=dis,
        battery_soc_kwh=[max(0.0, v) for v in soc],
        hvac_kw=hvac,
        ev_charge_kw=ev,
        generator_kw=gen,
        base_load_kw=list(forecast.load.p50_kw),
    )


def _end_of_day(
    signals: SignalTable,
    assets: AssetConstraints,
    grid: list[float],
    ch: list[float],
    dis: list[float],
    soc: list[float],
) -> None:
    """End-of-day rule (``soc[-1] >= initial``), in place: grid top-up latest first, then less discharge."""
    b, grid_max, h = assets.battery, assets.grid.max_import_kw, slot_hours()
    eta = b.one_way_efficiency
    n = len(soc)

    def raise_from(t: int, kwh: float) -> None:
        for u in range(t, n):
            soc[u] += kwh

    for t in reversed(range(n)):  # 1. top up from grid in the latest non-outage, non-discharge slots
        deficit = b.initial_soc_kwh - soc[-1]
        if deficit <= EPS:
            return
        if signals.outage[t] or dis[t] > EPS:
            continue
        headroom = min(b.soc_max_kwh - soc[u] for u in range(t, n))
        x = min(deficit / (eta * h), b.max_charge_kw - ch[t], grid_max - grid[t], headroom / (eta * h))
        if x > EPS:
            ch[t] += x
            grid[t] += x
            raise_from(t, eta * x * h)
    for t in reversed(range(n)):  # 2. replace the latest discharge with grid import
        deficit = b.initial_soc_kwh - soc[-1]
        if deficit <= EPS:
            return
        if signals.outage[t] or dis[t] <= EPS:
            continue
        headroom = min(b.soc_max_kwh - soc[u] for u in range(t, n))
        x = min(dis[t], deficit * eta / h, grid_max - grid[t], headroom * eta / h)
        if x > EPS:
            dis[t] -= x
            grid[t] += x
            raise_from(t, x * h / eta)


def solar_first_schedule(forecast: Forecast, signals: SignalTable, assets: AssetConstraints) -> Schedule:
    """B1 "charge on arrival / solar-first" (see the module table). Pass actual solar/load as ``forecast``."""
    ev = _ev_profile(assets, rules.ev_required_slots(assets.ev))
    return _dispatch(
        forecast, signals, assets, ev, discharge_ok=lambda t: True, grid_charge_target=lambda t: 0.0
    )


def time_of_use_schedule(forecast: Forecast, signals: SignalTable, assets: AssetConstraints) -> Schedule:
    """B2 "simple time-of-use rule" (see the module table). Pass actual solar/load as ``forecast``."""
    off, peak = tariff_periods(signals)
    required = rules.ev_required_slots(assets.ev)
    ev = _ev_profile(assets, [t for t in required if off[t]] + [t for t in required if not off[t]])
    peaks = [t for t, p in enumerate(peak) if p]
    first_peak = peaks[0] if peaks else len(peak)
    last_peak = peaks[-1] if peaks else -1
    b = assets.battery

    def target(t: int) -> float:
        if not off[t]:
            return 0.0
        if t < first_peak:
            return b.soc_max_kwh
        return b.initial_soc_kwh if t > last_peak else 0.0

    return _dispatch(forecast, signals, assets, ev, discharge_ok=lambda t: peak[t], grid_charge_target=target)


BASELINE_FUNCS: dict[str, Callable[[Forecast, SignalTable, AssetConstraints], Schedule]] = {
    SOLAR_FIRST: solar_first_schedule,
    TIME_OF_USE: time_of_use_schedule,
}
