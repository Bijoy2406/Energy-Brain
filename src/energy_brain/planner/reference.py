"""Rule-based reference schedule (#17): objective scaling and the solver-failure fallback.

No arbitrage and no load shifting: HVAC at its occupied minimum, EVs charge as early as possible in the
required window, solar first, then grid. The battery only absorbs surplus solar or generator minimum-load
surplus, covers what the grid / generator cannot (outages, import limit), keeps the reserve floor and is
topped back up to its starting charge from the cheapest grid slots at the end.

It tries to respect every hard constraint but is not guaranteed to: the Planner always passes it through
``planner.checks.find_violations`` before using it as a fallback.
"""

from __future__ import annotations

from energy_brain.planner import rules
from energy_brain.schemas import EPS, AssetConstraints, Forecast, Schedule, SignalTable
from energy_brain.timeaxis import n_slots, slot_hours


def _ev_asap(assets: AssetConstraints) -> list[float]:
    ev, h = assets.ev, slot_hours()
    out = [0.0] * n_slots()
    remaining = ev.energy_required_kwh
    for t in rules.ev_required_slots(ev):
        if remaining <= EPS:
            break
        kw = min(ev.charger_kw, remaining / h)
        out[t] = kw
        remaining -= kw * h
    return out


def rule_based_schedule(
    forecast: Forecast, signals: SignalTable, assets: AssetConstraints, reserve_margin_kwh: float = 0.0
) -> Schedule:
    """Simple safe-as-possible schedule; see the module docstring for the rules."""
    n, h = n_slots(), slot_hours()
    b, gen_c, grid_max = assets.battery, assets.generator, assets.grid.max_import_kw
    eta = b.one_way_efficiency
    floor = rules.soc_floor_kwh(b, reserve_margin_kwh)
    hvac = rules.hvac_min_kw(assets.hvac)
    ev = _ev_asap(assets)

    grid, used, curt, ch, dis, soc, gen = ([0.0] * n for _ in range(7))
    level = b.initial_soc_kwh
    for t in range(n):
        load = forecast.load.p50_kw[t] + hvac[t] + ev[t]
        solar = forecast.solar.p50_kw[t]
        u = min(solar, load)
        residual = load - u
        charge_room = max(0.0, min(b.max_charge_kw, (b.soc_max_kwh - level) / (eta * h)))
        dis_room = max(0.0, min(b.max_discharge_kw, (level - floor[t]) * eta / h))
        c = min(solar - u, charge_room)  # store surplus solar
        u += c
        d = g = gr = 0.0
        if signals.outage[t]:
            if residual <= dis_room + EPS:
                d = residual
            elif residual > EPS:
                g = min(max(residual, gen_c.min_kw), gen_c.rated_kw)
                excess = g - residual
                if excess > 0:  # generator minimum load: back off solar, then charge the battery
                    back = min(excess, u - c)
                    u -= back
                    c = min(c + excess - back, charge_room)
                else:
                    d = min(residual - g, dis_room)
        else:
            gr = residual
            need = (floor[t] - (level + eta * c * h)) / (eta * h)  # reach the (ramped) reserve floor
            if need > EPS:
                extra = min(need, charge_room - c, max(0.0, grid_max - gr))
                c += extra
                gr += extra
            if gr > grid_max:
                over = gr - grid_max
                gr = grid_max
                d = min(over, dis_room) if c <= EPS else 0.0
                rest = over - d
                if rest > EPS:
                    g = min(max(rest, gen_c.min_kw), gen_c.rated_kw)
                    gr = max(0.0, gr - (g - rest))
        level += eta * c * h - d * h / eta
        grid[t], used[t], curt[t], ch[t], dis[t], soc[t], gen[t] = gr, u, solar - u, c, d, level, g

    # Top the battery back up to its starting charge from the cheapest grid slots (end-of-day rule).
    deficit = b.initial_soc_kwh - soc[-1]
    order = sorted(
        (t for t in range(n) if not signals.outage[t] and dis[t] <= EPS),
        key=lambda t: (signals.tariff_bdt_per_kwh[t], signals.grid_co2_kg_per_kwh[t], t),
    )
    for t in order:
        if deficit <= EPS:
            break
        headroom = min(b.soc_max_kwh - soc[u] for u in range(t, n)) / (eta * h)
        x = min(deficit / (eta * h), b.max_charge_kw - ch[t], grid_max - grid[t], headroom)
        if x <= EPS:
            continue
        ch[t] += x
        grid[t] += x
        for u in range(t, n):
            soc[u] += eta * x * h
        deficit -= eta * x * h

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
