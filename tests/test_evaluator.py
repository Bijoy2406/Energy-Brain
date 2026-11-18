"""Tests for the Evaluator (#20). No dependency on data/processed or models/ (CI has neither).

Real-agent days use the committed one-week ``data/sample`` (2024-06-01..07): 06-03 and 06-05 have outages
in the generated calendar, 06-07 is a Friday (HVAC unoccupied). The Forecast agent falls back to persistence
there (no models), which is fine for checking plumbing, constraints and accounting; never for pitch numbers.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from energy_brain.accounting import compute_totals
from energy_brain.assets import AssetAgent
from energy_brain.evaluator import backtest as bt
from energy_brain.evaluator import report as rp
from energy_brain.evaluator.baselines import (
    BASELINE_FUNCS,
    BASELINES,
    SOLAR_FIRST,
    TIME_OF_USE,
    outage_backup_kwh,
    tariff_periods,
)
from energy_brain.evaluator.settle import actual_forecast, settle
from energy_brain.forecast import ModelForecastAgent
from energy_brain.planner import MilpPlanner, find_violations, rules
from energy_brain.risk import P10RiskAgent
from energy_brain.signals import CalendarSignalAgent
from energy_brain.stubs import (
    StubAssetProvider,
    StubCloudyForecastAgent,
    StubForecastAgent,
    StubPlanner,
    StubSignalAgent,
)
from energy_brain.timeaxis import n_slots, slot_hours

DHAKA = ZoneInfo("Asia/Dhaka")
STUB_START = datetime(2026, 11, 17, tzinfo=DHAKA)
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"
OUTAGE_DAY, NORMAL_DAY, FRIDAY = date(2024, 6, 5), date(2024, 6, 6), date(2024, 6, 7)


def _start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=DHAKA)


@pytest.fixture(scope="module")
def sample_dir(tmp_path_factory) -> Path:
    """Copy of data/sample in a data dir without processed/ (as in CI) and an empty models dir."""
    out = tmp_path_factory.mktemp("data")
    shutil.copytree(SAMPLE_DIR, out / "sample")
    (out / "models").mkdir()
    return out


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    return {k: pd.read_parquet(SAMPLE_DIR / f"{k}_sample.parquet") for k in ("pv", "load", "weather")}


@pytest.fixture(scope="module")
def agents(sample_dir, frames) -> dict:
    return {
        "forecast_agent": ModelForecastAgent(
            weather_df=frames["weather"],
            pv_df=frames["pv"],
            load_df=frames["load"],
            models_dir=sample_dir / "models",
            data_dir=sample_dir,
        ),
        "signal_agent": CalendarSignalAgent(data_dir=sample_dir),
        "assets": AssetAgent(),
        "planner": MilpPlanner(),
        "risk": P10RiskAgent(),
    }


@pytest.fixture(scope="module")
def actuals(frames) -> bt.Actuals:
    return bt.Actuals.from_frames(frames["pv"], frames["load"])


def _stub_inputs(forecast_agent=None):
    fa = forecast_agent or StubForecastAgent()
    return fa.run(STUB_START), StubSignalAgent().run(STUB_START), StubAssetProvider().run(STUB_START)


def _real_inputs(agents, actuals, d: date):
    s = _start(d)
    signals, assets = agents["signal_agent"].run(s), agents["assets"].run(s)
    return actual_forecast(s, *actuals.day(s)), signals, assets


# ---------------------------------------------------------------------------------------------- baselines


@pytest.mark.parametrize("name", BASELINES)
@pytest.mark.parametrize("forecast_agent", [StubForecastAgent, StubCloudyForecastAgent])
def test_baselines_pass_hard_constraints_on_stub_days(name, forecast_agent):
    forecast, signals, assets = _stub_inputs(forecast_agent())
    schedule = BASELINE_FUNCS[name](forecast, signals, assets)
    assert find_violations(schedule, forecast, signals, assets) == []


@pytest.mark.parametrize("name", BASELINES)
@pytest.mark.parametrize("day", [date(2024, 6, 3), OUTAGE_DAY, NORMAL_DAY, FRIDAY])
def test_baselines_pass_hard_constraints_on_real_asset_days(agents, actuals, name, day):
    actual, signals, assets = _real_inputs(agents, actuals, day)
    if day in (date(2024, 6, 3), OUTAGE_DAY):
        assert any(signals.outage)
    if day == FRIDAY:
        assert not any(assets.hvac.occupied)
    schedule = BASELINE_FUNCS[name](actual, signals, assets)
    assert find_violations(schedule, actual, signals, assets) == []
    assert schedule.battery_soc_kwh[-1] >= assets.battery.initial_soc_kwh - 1e-6  # end-of-day rule


def test_baselines_follow_their_rules(agents, actuals):
    actual, signals, assets = _real_inputs(agents, actuals, NORMAL_DAY)
    off, peak = tariff_periods(signals)
    b1 = BASELINE_FUNCS[SOLAR_FIRST](actual, signals, assets)
    b2 = BASELINE_FUNCS[TIME_OF_USE](actual, signals, assets)
    assert any(peak) and any(off)
    # B2 only discharges in the peak.
    assert all(peak[t] for t in range(n_slots()) if b2.battery_discharge_kw[t] > 1e-6)
    # EV: both start at the first required slot (00:00) and deliver exactly the required energy.
    req = rules.ev_required_slots(assets.ev)
    for s in (b1, b2):
        assert s.ev_charge_kw[req[0]] == pytest.approx(assets.ev.charger_kw)
        delivered = sum(s.ev_charge_kw[t] for t in req) * slot_hours()
        assert delivered == pytest.approx(assets.ev.energy_required_kwh)
        assert s.hvac_kw == rules.hvac_min_kw(assets.hvac)
    # Solar first: no solar curtailed while load is unmet from the grid.
    for t in range(n_slots()):
        if b1.solar_curtailed_kw[t] > 1e-6:
            assert b1.grid_import_kw[t] <= b1.battery_charge_kw[t] + 1e-6


def test_outage_backup_keeps_energy_for_load_above_generator_rating():
    forecast, signals, assets = _stub_inputs(StubCloudyForecastAgent())
    hvac = rules.hvac_min_kw(assets.hvac)
    demand = [forecast.load.p50_kw[t] + hvac[t] for t in range(n_slots())]
    backup = outage_backup_kwh(demand, forecast.solar.p50_kw, signals, assets)
    first = signals.outage.index(True)
    assert backup[first - 1] > assets.battery.soc_min_kwh  # energy held before the outage
    assert backup[-1] == assets.battery.soc_min_kwh


def test_optimiser_never_costlier_than_best_baseline_at_w1(agents, actuals):
    """On P50 inputs equal to the actuals the MILP must be at least as cheap as either feasible baseline.

    Both baselines satisfy every constraint the MILP has (checked above), so each is a feasible MILP
    solution; the optimum cannot cost more (up to the solver's relative MIP gap).
    """
    cases = [_stub_inputs(), _stub_inputs(StubCloudyForecastAgent())]
    cases += [_real_inputs(agents, actuals, d) for d in (date(2024, 6, 3), OUTAGE_DAY, NORMAL_DAY, FRIDAY)]
    for forecast, signals, assets in cases:
        plan = MilpPlanner().plan(forecast, signals, assets, 1.0)
        best = min(
            compute_totals(f(forecast, signals, assets), signals, assets).cost_bdt
            for f in BASELINE_FUNCS.values()
        )
        assert plan.totals.cost_bdt <= best * (1 + 1e-4) + 1e-6


# ---------------------------------------------------------------------------------------------- settle


def test_settle_balances_and_uses_compute_totals(agents, actuals):
    actual, signals, assets = _real_inputs(agents, actuals, OUTAGE_DAY)
    plan = MilpPlanner().plan(agents["forecast_agent"].run(_start(OUTAGE_DAY)), signals, assets, 0.5)
    st = settle(plan.schedule, actual, signals, assets)
    assert st.schedule.check_energy_balance(tol=1e-3) <= 1e-3
    assert st.totals == compute_totals(st.schedule, signals, assets)
    assert all(st.schedule.grid_import_kw[t] <= 1e-9 for t in range(n_slots()) if signals.outage[t])
    sun = actual.solar.p50_kw
    assert all(
        st.schedule.solar_used_kw[t] + st.schedule.solar_curtailed_kw[t] == pytest.approx(sun[t])
        for t in range(n_slots())
    )


def test_settle_baseline_on_actuals_needs_no_recourse(agents, actuals):
    actual, signals, assets = _real_inputs(agents, actuals, OUTAGE_DAY)
    for f in BASELINE_FUNCS.values():
        schedule = f(actual, signals, assets)
        st = settle(schedule, actual, signals, assets)
        assert st.unserved_kwh == 0 and st.dumped_kwh == pytest.approx(0, abs=1e-6)
        assert st.totals.cost_bdt == pytest.approx(compute_totals(schedule, signals, assets).cost_bdt)
        assert st.adjusted_cost_bdt == pytest.approx(st.totals.cost_bdt)


def test_settle_reports_unserved_energy_never_drops_it():
    """Stub plan settled against a much darker, busier day during an outage leaves energy unserved."""
    forecast, signals, assets = _stub_inputs()
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5)
    outage = [t for t in range(n_slots()) if signals.outage[t]]
    load = list(forecast.load.p90_kw)
    for t in outage:
        load[t] += 1000.0  # beyond grid-less supply: battery 500 kW + generator 800 kW
    actual = actual_forecast(STUB_START, [0.0] * n_slots(), load)
    st = settle(plan.schedule, actual, signals, assets)
    assert st.unserved_kwh > 0
    assert sum(st.unserved_kw[t] for t in outage) * slot_hours() == pytest.approx(st.unserved_kwh)
    assert st.schedule.check_energy_balance(tol=1e-3) <= 1e-3
    # Served load + unserved = actual demand (base load first absorbs the shortfall).
    for t in outage:
        assert st.unserved_kw[t] <= load[t]
        served = st.schedule.base_load_kw[t] - st.dumped_kw[t]
        assert served + st.unserved_kw[t] == pytest.approx(load[t], abs=1e-6)


def test_settle_adjusts_end_of_day_shortfall(agents, actuals):
    """A plan that ends the day low is charged the off-peak refill in the adjusted figures."""
    actual, signals, assets = _real_inputs(agents, actuals, NORMAL_DAY)
    forecast = agents["forecast_agent"].run(_start(NORMAL_DAY))
    plan = MilpPlanner().plan(forecast, signals, assets, 1.0)
    # Make the actual evening load higher so the battery ends lower than planned.
    load = [v + (300.0 if t >= 80 else 0.0) for t, v in enumerate(actual.load.p50_kw)]
    dark = actual_forecast(actual.start, actual.solar.p50_kw, load)
    st = settle(plan.schedule, dark, signals, assets)
    eta = assets.battery.one_way_efficiency
    assert st.end_soc_delta_kwh == pytest.approx(
        st.schedule.battery_soc_kwh[-1] - assets.battery.initial_soc_kwh
    )
    assert st.end_soc_shortfall_kwh == pytest.approx(max(0.0, -st.end_soc_delta_kwh))
    expected = st.totals.cost_bdt + st.end_soc_shortfall_kwh / eta * min(signals.tariff_bdt_per_kwh)
    assert st.adjusted_cost_bdt == pytest.approx(expected)


def test_settle_refuses_unbalanced_schedule():
    forecast, signals, assets = _stub_inputs()
    plan = MilpPlanner().plan(forecast, signals, assets, 0.5)
    broken = plan.schedule.model_copy(update={"grid_import_kw": [0.0] * n_slots()})
    with pytest.raises(ValueError):
        settle(
            broken, actual_forecast(STUB_START, forecast.solar.p50_kw, forecast.load.p50_kw), signals, assets
        )


# ---------------------------------------------------------------------------------------------- backtest


def test_backtest_refuses_stub_agents(agents, actuals):
    stubbed = {**agents, "planner": StubPlanner()}
    with pytest.raises(ValueError, match="stub"):
        bt.run_backtest([OUTAGE_DAY], [0.5], stubbed, actuals)


def test_backtest_day_rows_and_percentages(agents, actuals):
    rec = bt.backtest_day(_start(OUTAGE_DAY), [0.5, 1.0], agents, actuals)
    assert rec["included"], rec["excluded_reason"]
    methods = [(r["method"], r["weight"]) for r in rec["methods"]]
    assert methods == [("optimiser", 0.5), ("optimiser", 1.0), (SOLAR_FIRST, None), (TIME_OF_USE, None)]
    by = {(r["method"], r["weight"]): r for r in rec["methods"]}
    opt = by[("optimiser", 1.0)]
    for b in BASELINES:
        ref = by[(b, None)]
        expected = (ref["adjusted_cost_bdt"] - opt["adjusted_cost_bdt"]) / ref["adjusted_cost_bdt"] * 100
        assert opt[f"cost_saved_vs_{b}_pct"] == pytest.approx(expected)
        assert ref[f"cost_saved_vs_{b}_pct"] == pytest.approx(0.0)
    assert set(rec["methods"][0]) == set(bt.ROW_FIELDS)
    assert rec["outage_slots"] > 0 and rec["forecast_fallback_used"]


def test_backtest_excludes_day_without_actuals(agents, actuals):
    rec = bt.backtest_day(_start(date(2024, 6, 20)), [0.5], agents, actuals)
    assert not rec["included"] and rec["methods"] == []
    assert "incomplete" in rec["excluded_reason"]


def test_select_days():
    assert len(bt.select_days(date(2024, 1, 1), date(2024, 12, 31))) == 366
    picked = bt.select_days(date(2024, 1, 1), date(2024, 12, 31), 5)
    assert picked[0] == date(2024, 1, 1) and picked[-1] == date(2024, 12, 31) and len(picked) == 5
    with pytest.raises(ValueError):
        bt.select_days(date(2024, 2, 1), date(2024, 1, 1))


def test_end_to_end_three_days_writes_report(tmp_path, agents, actuals):
    days = [OUTAGE_DAY, NORMAL_DAY, FRIDAY]
    weights = [0.5, 1.0, 0.0]
    records = bt.run_backtest(days, weights, agents, actuals)
    config = rp.collect_config(
        days=days,
        weights=weights,
        headline_weight=0.5,
        root=Path(__file__).resolve().parents[1],
        data_files={"pv": SAMPLE_DIR / "pv_sample.parquet", "load": SAMPLE_DIR / "load_sample.parquet"},
        models_dir=None,
        signal_provenance=agents["signal_agent"].provenance(),
        test_year=2024,
        train_years=[2022, 2023],
        data_source="sample (test)",
    )
    report = rp.build_report(records, weights, 0.5, config)
    rp.write_json(report, tmp_path / "backtest.json")
    rp.write_csv(report, tmp_path / "backtest.csv")

    loaded = json.loads((tmp_path / "backtest.json").read_text())
    assert loaded == json.loads(json.dumps(report))  # round-trips
    assert loaded["schema_version"] == rp.SCHEMA_VERSION
    assert loaded["summary"]["days"]["included"] == 3
    assert set(loaded["summary"]["by_weight"]) == {"0.5", "1", "0"}
    assert loaded["config"]["out_of_sample"] is True
    assert "SIGNAL SOURCES" in loaded["config"]["provenance"]["signals"]
    text = " ".join(loaded["caveats"] + loaded["headline"]["lines"])
    for phrase in (
        "out-of-sample",
        "synthetic load",
        "simulated PV",
        "synthetic outages",
        "NOT a true day-ahead",
    ):
        assert phrase.lower() in text.lower()
    assert loaded["summary"]["forecast"]["label"] == rp.FORECAST_LABEL

    # CSV: caveat comment lines, then one row per day and per method.
    raw = (tmp_path / "backtest.csv").read_text().splitlines()
    assert raw[0].startswith("# ") and any(
        "NOT a true day-ahead" in line for line in raw if line.startswith("#")
    )
    df = pd.read_csv(tmp_path / "backtest.csv", comment="#")
    assert len(df) == len(days) * (len(weights) + len(BASELINES))
    assert list(df.columns) == list(rp.CSV_COLUMNS)
    assert df.groupby(["date", "method", "weight"], dropna=False).size().max() == 1
    rows = list(csv.DictReader(line for line in raw if not line.startswith("#")))
    assert {r["method"] for r in rows} == {"optimiser", *BASELINES}

    # Summary percentages are computed against the named baseline over the included days.
    for b in BASELINES:
        opt = df[(df.method == "optimiser") & (df.weight == 0.5)].adjusted_cost_bdt.sum()
        ref = df[df.method == b].adjusted_cost_bdt.sum()
        got = loaded["summary"]["by_weight"]["0.5"]["vs"][b]["cost_saved_pct"]
        assert got == pytest.approx((ref - opt) / ref * 100, abs=1e-3)

    # Deterministic: a rerun gives identical numbers.
    again = rp.build_report(bt.run_backtest(days, weights, agents, actuals), weights, 0.5, config)
    assert again["days"] == report["days"] and again["summary"] == report["summary"]
