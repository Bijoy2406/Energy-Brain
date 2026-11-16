"""One shared cost/CO2 calculation (issue #13).

Planner, Risk, Evaluator, stubs and the API must all call :func:`compute_totals` so cost and CO2 are always
computed the same way. Per slot t, with ``h = slot_hours()``::

    grid_kwh[t]   = grid_import_kw[t] * h
    gen_kwh[t]    = generator_kw[t] * h
    diesel_l[t]   = gen_kwh[t] * fuel_l_per_kwh
    cost_bdt[t]   = grid_kwh[t] * tariff_bdt_per_kwh[t]  + diesel_l[t] * diesel_price_bdt_per_l[t]
    co2_kg[t]     = grid_kwh[t] * grid_co2_kg_per_kwh[t] + diesel_l[t] * diesel_co2_kg_per_l[t]

Battery and solar have no marginal cost or CO2 here.
"""

from __future__ import annotations

from energy_brain.schemas import EPS, AssetConstraints, Schedule, SignalTable, Totals
from energy_brain.timeaxis import slot_hours


def compute_totals(schedule: Schedule, signals: SignalTable, assets: AssetConstraints) -> Totals:
    """Cost (BDT), CO2 (kg) and energy totals for ``schedule`` under ``signals`` and ``assets``."""
    h = slot_hours()
    fuel = assets.generator.fuel_l_per_kwh
    slot_cost: list[float] = []
    slot_co2: list[float] = []
    grid_kwh = gen_kwh = diesel_l = gen_hours = 0.0
    for t, (grid_kw, gen_kw) in enumerate(zip(schedule.grid_import_kw, schedule.generator_kw, strict=True)):
        g_kwh = grid_kw * h
        d_kwh = gen_kw * h
        litres = d_kwh * fuel
        slot_cost.append(g_kwh * signals.tariff_bdt_per_kwh[t] + litres * signals.diesel_price_bdt_per_l[t])
        slot_co2.append(g_kwh * signals.grid_co2_kg_per_kwh[t] + litres * signals.diesel_co2_kg_per_l[t])
        grid_kwh += g_kwh
        gen_kwh += d_kwh
        diesel_l += litres
        if gen_kw > EPS:
            gen_hours += h
    return Totals(
        cost_bdt=sum(slot_cost),
        co2_kg=sum(slot_co2),
        grid_kwh=grid_kwh,
        solar_kwh=sum(schedule.solar_used_kw) * h,
        generator_kwh=gen_kwh,
        generator_hours=gen_hours,
        diesel_litres=diesel_l,
        slot_cost_bdt=slot_cost,
        slot_co2_kg=slot_co2,
    )
