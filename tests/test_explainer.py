"""Explainer agent tests (#19). No API key, no network, no data/processed or models: stub agents only."""

from __future__ import annotations

import copy
import json

import pytest

from energy_brain.explainer.context import PlanContext
from energy_brain.explainer.tools import (
    TOOL_DECLARATIONS,
    TOOL_NAMES,
    ExplainerTools,
    ToolArgumentError,
    parse_clock,
)
from energy_brain.planner import UNSAFE_PREFIX, MilpPlanner
from energy_brain.risk import P10RiskAgent
from energy_brain.stubs import StubAssetProvider, StubForecastAgent, StubSignalAgent
from energy_brain.timeaxis import slot_hours

H = slot_hours()
OUTAGE_SLOT = 52  # stub outage 13:00-14:00 = slots 52-55

ALL_CALLS = [
    ("get_plan_summary", {}),
    ("get_slot", {"time": "13:00"}),
    ("get_slot_range", {"start": "12:00", "end": "15:00"}),
    ("get_slot_range", {"start": "00:00", "end": "24:00"}),
    *[
        ("find_slots", {"condition": c})
        for c in ("generator_on", "battery_charging", "outage", "peak_tariff")
    ],
    ("get_forecast", {"start": "06:00", "end": "18:00"}),
    ("get_signals", {}),
    ("get_assets", {}),
    ("get_risk_log", {}),
    ("get_forecast_note", {}),
    ("get_backtest_totals", {}),
]


def _backtest(date: str) -> dict:
    """Minimal backtest report in the #20 format (placeholder numbers)."""
    return {
        "headline": {
            "baseline_names": {"baseline_solar_first": "solar-first rule", "baseline_time_of_use": "ToU"}
        },
        "config": {"first_day": "2024-01-01", "last_day": "2024-12-31"},
        "summary": {
            "days": {"included": 366},
            "by_weight": {
                "0.5": {
                    "vs": {
                        "baseline_solar_first": {
                            "cost_saved_pct": 4.7236,
                            "cost_saved_bdt": 2075147.8029,
                            "co2_saved_pct": -0.3173,
                            "co2_saved_kg": -8388.1019,
                            "days_cost_worse": 0,
                        }
                    }
                }
            },
        },
        "days": [
            {
                "date": date,
                "methods": [
                    {
                        "method": "optimiser",
                        "weight": 0.5,
                        "adjusted_cost_bdt": 153157.6373,
                        "adjusted_co2_kg": 9111.5024,
                        "cost_saved_vs_baseline_solar_first_pct": 9.4523,
                        "co2_saved_vs_baseline_solar_first_pct": 0.5482,
                        "cost_saved_vs_baseline_time_of_use_pct": 5.2555,
                        "co2_saved_vs_baseline_time_of_use_pct": -0.5229,
                    }
                ],
            }
        ],
    }


@pytest.fixture(scope="module")
def ctx() -> PlanContext:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    start = datetime(2026, 11, 17, tzinfo=ZoneInfo("Asia/Dhaka"))
    return PlanContext.from_agents(
        start,
        0.5,
        forecast_agent=StubForecastAgent(),
        signal_agent=StubSignalAgent(),
        assets=StubAssetProvider(),
        planner=MilpPlanner(),
        risk=P10RiskAgent(),
        backtest=_backtest("2026-11-17"),
    )


@pytest.fixture(scope="module")
def tools(ctx) -> ExplainerTools:
    return ExplainerTools(ctx)


# --------------------------------------------------------------------------- context and tools


def test_context_keeps_agent_outputs(ctx):
    assert ctx.plan.start == ctx.forecast.start == ctx.signals.start == ctx.assets.start
    assert ctx.risk_scenario == "solar_p10,load_p50"
    assert ctx.signal_note is None  # stub signal agent has no run_with_note


def test_declarations_match_tools(tools):
    assert set(TOOL_NAMES) == set(tools._dispatch)
    for decl in TOOL_DECLARATIONS:
        fn = decl["function"]
        assert decl["type"] == "function" and fn["parameters"]["type"] == "object"
        assert fn["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize(("name", "args"), ALL_CALLS)
def test_every_tool_is_json_and_compact(tools, name, args):
    result = tools.call(name, args)
    text = json.dumps(result, separators=(",", ":"))
    assert len(text) < 6000
    assert "slot " not in text.lower()  # times, never slot indices


def test_tools_do_not_modify_inputs(ctx, tools):
    before = [m.model_dump_json() for m in (ctx.plan, ctx.forecast, ctx.signals, ctx.assets)]
    backtest = copy.deepcopy(ctx.backtest)
    for name, args in ALL_CALLS:
        result = tools.call(name, args)
        if isinstance(result.get("rows"), list) and result["rows"]:
            result["rows"][0]["time"] = "mutated"  # results are fresh objects
    assert [m.model_dump_json() for m in (ctx.plan, ctx.forecast, ctx.signals, ctx.assets)] == before
    assert ctx.backtest == backtest


def test_get_slot_values_times_and_binding(ctx, tools):
    s, sig = ctx.plan.schedule, ctx.signals
    r = tools.call("get_slot", {"time": "1 PM"})
    assert (r["time"], r["end"]) == ("13:00", "13:15")
    t = OUTAGE_SLOT
    assert r["outage"] is True and r["grid_import_kw"] == 0.0
    assert r["generator_kw"] == round(s.generator_kw[t], 1)
    assert r["battery_soc_end_kwh"] == round(s.battery_soc_kwh[t], 1)
    assert r["battery_soc_start_kwh"] == round(s.battery_soc_kwh[t - 1], 1)
    assert r["load_total_kw"] == round(s.base_load_kw[t] + s.hvac_kw[t] + s.ev_charge_kw[t], 1)
    assert r["tariff_bdt_per_kwh"] == sig.tariff_bdt_per_kwh[t]
    assert r["tariff_period"] == "off_peak"
    assert {"flag": "outage_grid_unavailable", "grid_import_kw": 0.0} in r["binding"]
    assert tools.call("get_slot", {"time": "13:07"})["time"] == "13:00"  # slot containing the time
    assert tools.call("get_slot", {"time": "18:00"})["tariff_period"] == "peak"


def test_range_sums_are_computed_by_the_tool(ctx, tools):
    s, tot = ctx.plan.schedule, ctx.plan.totals
    day = tools.call("get_slot_range", {"start": "00:00", "end": "24:00"})
    assert (day["from"], day["to"], day["duration_h"]) == ("00:00", "24:00", 24.0)
    assert day["cost_bdt"] == round(tot.cost_bdt, 1)
    assert day["energy_kwh"]["generator"] == round(tot.generator_kwh, 1)
    assert day["resolution"] == "1h_mean" and len(day["rows"]) == 24
    short = tools.call("get_slot_range", {"start": "13:00", "end": "14:00"})
    assert short["resolution"] == "15min" and [r["time"] for r in short["rows"]] == [
        "13:00",
        "13:15",
        "13:30",
        "13:45",
    ]
    assert short["energy_kwh"]["battery_discharge"] == round(sum(s.battery_discharge_kw[52:56]) * H, 1)
    assert short["outage_blocks"] == [{"from": "13:00", "to": "14:00", "duration_h": 1.0}]
    with pytest.raises(ToolArgumentError):
        tools.call("get_slot_range", {"start": "14:00", "end": "13:00"})


def test_find_slots_blocks(ctx, tools):
    outage = tools.call("find_slots", {"condition": "outage"})
    assert outage["blocks"] == [{"from": "13:00", "to": "14:00", "duration_h": 1.0}]
    assert outage["total_h"] == 1.0
    peak = tools.call("find_slots", {"condition": "peak_tariff"})
    assert peak["blocks"] == [{"from": "17:00", "to": "23:00", "duration_h": 6.0, "tariff_bdt_per_kwh": 13.5}]
    gen = tools.call("find_slots", {"condition": "generator_on"})
    assert gen["total_energy_kwh"] == round(sum(ctx.plan.schedule.generator_kw) * H, 1)


def test_signals_assets_risk_notes(ctx, tools):
    sig = tools.call("get_signals", {})
    assert sig["grid_co2_kg_per_kwh"] == 0.62 and sig["signal_fallback"] is False
    assert [b["tariff_period"] for b in sig["tariff_blocks"]] == ["off_peak", "peak", "off_peak"]
    assets = tools.call("get_assets", {})
    assert assets["battery"]["soc_max_kwh"] == round(ctx.assets.battery.soc_max_kwh, 1)
    assert assets["battery"]["round_trip_efficiency_pct"] == round(
        ctx.assets.battery.round_trip_efficiency * 100, 1
    )
    risk = tools.call("get_risk_log", {})
    assert risk["ok"] == ctx.plan.risk.ok and risk["stress_scenario"] == "solar_p10,load_p50"
    note = tools.call("get_forecast_note", {})
    assert note["forecast"]["fallback_used"] is False and note["signal"] == {
        "fallback_used": False,
        "note": None,
    }


def test_summary_rewrites_slot_numbers_and_flags_unsafe(ctx):
    summary = ExplainerTools(ctx).call("get_plan_summary", {})
    assert summary["safe"] is True and summary["date"] == "2026-11-17"
    assert summary["totals"]["cost_bdt"] == round(ctx.plan.totals.cost_bdt, 1)
    assert not any("slot" in n for n in summary["notes"])
    unsafe = ctx.plan.model_copy(
        update={
            "status": "infeasible",
            "fallback_used": True,
            "notes": [f"{UNSAFE_PREFIX}: test", "slot 52 short"],
        }
    )
    bad = ExplainerTools(PlanContext(unsafe, ctx.forecast, ctx.signals, ctx.assets))
    out = bad.call("get_plan_summary", {})
    assert out["safe"] is False and out["notes"][1] == "13:00-13:15 short"


def test_risk_findings_use_times(ctx):
    line = (
        "RISK FAIL: check=unserved_energy; scenario=solar_p10,load_p50; slots=52-55; shortfall_kwh=8.9; "
        "limit=energy; at_risk=comfort; reserve_kwh=0->9.41648; fixable=yes; detail=8.9 kWh unserved"
    )
    plan = ctx.plan.model_copy(
        update={"risk": ctx.plan.risk.model_copy(update={"ok": False, "reasons": [line]})}
    )
    out = ExplainerTools(PlanContext(plan, ctx.forecast, ctx.signals, ctx.assets)).call("get_risk_log", {})
    f = out["findings"][0]
    assert f["times"] == [{"from": "13:00", "to": "14:00", "duration_h": 1.0}]
    assert (f["shortfall_kwh"], f["reserve_from_kwh"], f["reserve_to_kwh"]) == (8.9, 0.0, 9.4)
    assert f["fixable"] == "yes" and out["ok"] is False


def test_backtest_year_and_day(ctx, tools):
    out = tools.call("get_backtest_totals", {})
    assert out["available"] and out["weight"] == 0.5
    year = out["year_vs_baselines"]["solar-first rule"]
    assert (year["cost_saved_pct"], year["co2_saved_pct"]) == (4.7, -0.3)
    assert year["co2_result"] == "CO2 higher than the baseline"
    day = out["day"]
    assert day["available"] and day["settled_cost_bdt"] == 153157.6
    assert day["vs_baselines"]["ToU"]["co2_result"] == "CO2 higher than the baseline"


def test_backtest_missing_date_or_report(ctx):
    other = PlanContext(ctx.plan, ctx.forecast, ctx.signals, ctx.assets, backtest=_backtest("2024-07-02"))
    day = ExplainerTools(other).call("get_backtest_totals", {})["day"]
    assert day == {"available": False, "reason": "date 2026-11-17 not in backtest; do not quote it"}
    none = PlanContext(ctx.plan, ctx.forecast, ctx.signals, ctx.assets)
    assert ExplainerTools(none).call("get_backtest_totals", {})["available"] is False


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("set_generator", {}),
        ("get_slot", {}),
        ("get_slot", {"time": 19}),
        ("get_slot", {"time": "19:00", "extra": 1}),
        ("get_slot", {"time": "25:00"}),
        ("get_slot", {"time": "evening"}),
        ("find_slots", {"condition": "rm -rf"}),
        ("get_assets", ["not", "a", "dict"]),
    ],
)
def test_bad_arguments_raise(tools, name, args):
    with pytest.raises(ToolArgumentError):
        tools.call(name, args)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("19:00", 1140),
        ("7 PM", 1140),
        ("7:30pm", 1170),
        ("12 am", 0),
        ("12 PM", 720),
        ("24:00", 1440),
        ("9", 540),
    ],
)
def test_parse_clock(text, minutes):
    assert parse_clock(text) == minutes
