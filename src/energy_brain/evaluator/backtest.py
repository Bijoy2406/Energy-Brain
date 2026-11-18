"""Backtest replay loop (#20): forecast, plan, settle against actuals, compare with two baselines.

For each test day:

1. The real agents run once: Forecast (``ModelForecastAgent``), Signal (``CalendarSignalAgent``) and Asset
   (``AssetAgent``) outputs are computed once and shared by every method.
2. The optimiser plans through ``orchestrator.run_plan`` (``MilpPlanner`` + ``P10RiskAgent``, re-plans
   included) for each weight.
3. The two baselines (``evaluator.baselines``) are built on the day's ACTUAL solar and base load and must pass
   ``planner.checks.find_violations`` (against the actuals).
4. Every schedule is settled against the actuals with the same :func:`evaluator.settle.settle`.

Stub agents are refused: every number in the report must come from the real agents. Nothing is read at
import; the caller passes the agents and the actual PV / load frames (see ``evaluator.__main__``).
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from energy_brain.evaluator.baselines import BASELINE_FUNCS, BASELINES
from energy_brain.evaluator.settle import Settlement, actual_forecast, settle
from energy_brain.orchestrator import run_plan
from energy_brain.planner import find_violations, is_safe
from energy_brain.schemas import AssetConstraints, Forecast, SignalTable
from energy_brain.timeaxis import site_tz, slot_times

logger = logging.getLogger(__name__)

OPTIMISER = "optimiser"
#: Fields of one settled method row (JSON ``methods`` entries and CSV columns), in order.
ROW_FIELDS = (
    "date",
    "method",
    "weight",
    "status",
    "plan_safe",
    "risk_ok",
    "replans",
    "reserve_margin_kwh",
    "fallback_used",
    "violations",
    "settled",
    "cost_bdt",
    "co2_kg",
    "adjusted_cost_bdt",
    "adjusted_co2_kg",
    "planned_cost_bdt",
    "planned_co2_kg",
    "grid_kwh",
    "solar_kwh",
    "generator_kwh",
    "generator_hours",
    "diesel_litres",
    "unserved_kwh",
    "dumped_kwh",
    "end_soc_delta_kwh",
    "end_soc_shortfall_kwh",
    "recourse_grid_kwh",
    "recourse_battery_kwh",
    "recourse_generator_kwh",
    "recourse_charge_cut_kwh",
    *(f"cost_saved_vs_{b}_pct" for b in BASELINES),
    *(f"co2_saved_vs_{b}_pct" for b in BASELINES),
)
#: Per-day forecast quality fields (repeated on each CSV row of the day).
FORECAST_FIELDS = (
    "forecast_fallback_used",
    "solar_mae_kw",
    "load_mae_kw",
    "solar_p10_p90_coverage_pct",
    "solar_daylight_p10_p90_coverage_pct",
    "load_p10_p90_coverage_pct",
)


class _Fixed:
    """Agent that returns one precomputed output (so every weight sees the same inputs)."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def run(self, start: datetime) -> Any:
        return self.value


@dataclass(frozen=True)
class Actuals:
    """Measured PV (``pv_kw``) and inflexible base load (``base_kw``) indexed by slot start (site tz)."""

    pv_kw: pd.Series
    base_kw: pd.Series

    @classmethod
    def from_frames(cls, pv_df: pd.DataFrame, load_df: pd.DataFrame) -> Actuals:
        def series(df: pd.DataFrame, col: str) -> pd.Series:
            s = df.set_index("timestamp")[col] if "timestamp" in df.columns else df[col]
            idx = pd.DatetimeIndex(s.index)
            idx = idx.tz_localize(site_tz()) if idx.tz is None else idx.tz_convert(site_tz())
            s = pd.Series(s.to_numpy(dtype=float), index=idx)
            return s[~s.index.duplicated(keep="first")].sort_index()

        return cls(pv_kw=series(pv_df, "pv_kw"), base_kw=series(load_df, "base_kw"))

    def day(self, start: datetime) -> tuple[list[float], list[float]] | None:
        """``(solar_kw, load_kw)`` for the day at ``start``, or None if any slot is missing or not finite."""
        stamps = pd.DatetimeIndex(slot_times(start))
        solar = self.pv_kw.reindex(stamps)
        load = self.base_kw.reindex(stamps)
        if solar.isna().any() or load.isna().any():
            return None
        out = solar.tolist(), load.tolist()
        if not all(math.isfinite(v) for v in (*out[0], *out[1])):
            return None
        return out


def forecast_quality(forecast: Forecast, actual: Forecast) -> dict[str, Any]:
    """Per-day P50 MAE and P10-P90 coverage (same definition as ``reports/forecast_metrics.json``).

    Coverage = share of slots with ``p10 <= actual <= p90`` (inclusive), over all slots. Solar daylight
    coverage uses only slots where the actual or the forecast P90 is above 0 (night zeros excluded).
    Measured with observed weather as forecast input (tier 0), NOT a true day-ahead weather forecast.
    """

    def mae(q, a) -> float:
        return statistics.fmean(abs(p - x) for p, x in zip(q.p50_kw, a, strict=True))

    def hits(q, a, keep) -> tuple[int, int]:
        inside = [lo <= x <= hi for lo, x, hi, k in zip(q.p10_kw, a, q.p90_kw, keep, strict=True) if k]
        return sum(inside), len(inside)

    def pct(pair: tuple[int, int]) -> float | None:
        return 100.0 * pair[0] / pair[1] if pair[1] else None

    sun, dem = actual.solar.p50_kw, actual.load.p50_kw
    everything = [True] * len(sun)
    daylight = [x > 0 or hi > 0 for x, hi in zip(sun, forecast.solar.p90_kw, strict=True)]
    day_hits = hits(forecast.solar, sun, daylight)
    return {
        "solar_mae_kw": mae(forecast.solar, sun),
        "load_mae_kw": mae(forecast.load, dem),
        "solar_p10_p90_coverage_pct": pct(hits(forecast.solar, sun, everything)),
        "solar_daylight_p10_p90_coverage_pct": pct(day_hits),
        "load_p10_p90_coverage_pct": pct(hits(forecast.load, dem, everything)),
        "solar_daylight_hits": day_hits[0],
        "solar_daylight_slots": day_hits[1],
    }


def _settled_row(st: Settlement) -> dict[str, Any]:
    t = st.totals
    return {
        "settled": True,
        "cost_bdt": t.cost_bdt,
        "co2_kg": t.co2_kg,
        "adjusted_cost_bdt": st.adjusted_cost_bdt,
        "adjusted_co2_kg": st.adjusted_co2_kg,
        "grid_kwh": t.grid_kwh,
        "solar_kwh": t.solar_kwh,
        "generator_kwh": t.generator_kwh,
        "generator_hours": t.generator_hours,
        "diesel_litres": t.diesel_litres,
        "unserved_kwh": st.unserved_kwh,
        "dumped_kwh": st.dumped_kwh,
        "end_soc_delta_kwh": st.end_soc_delta_kwh,
        "end_soc_shortfall_kwh": st.end_soc_shortfall_kwh,
        "recourse_grid_kwh": st.grid_extra_kwh,
        "recourse_battery_kwh": st.discharge_extra_kwh,
        "recourse_generator_kwh": st.generator_extra_kwh,
        "recourse_charge_cut_kwh": st.charge_cut_kwh,
    }


def _blank_row(day: date, method: str, weight: float | None) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(ROW_FIELDS)
    row.update(date=day.isoformat(), method=method, weight=weight, settled=False, violations=[])
    return row


def _settle_into(
    row: dict, schedule, actual: Forecast, signals: SignalTable, assets: AssetConstraints
) -> str | None:
    """Settle into ``row``; return why it could not be settled (or None)."""
    try:
        row.update(_settled_row(settle(schedule, actual, signals, assets)))
    except ValueError as exc:
        return str(exc)
    return None


def saved_pct(baseline: float, method: float) -> float | None:
    """``(baseline - method) / baseline * 100``; None when the baseline is 0."""
    return None if abs(baseline) < 1e-9 else (baseline - method) / baseline * 100.0


def backtest_day(
    start: datetime,
    weights: Iterable[float],
    agents: dict[str, Any],
    actuals: Actuals,
) -> dict[str, Any]:
    """Run every method for one day; return the day record (``methods`` = one row per method)."""
    day = start.date()
    record: dict[str, Any] = {"date": day.isoformat(), "weekday": start.strftime("%a")}
    pair = actuals.day(start)
    if pair is None:
        record.update(included=False, excluded_reason="actual PV or load incomplete for this day", methods=[])
        return record

    forecast = agents["forecast_agent"].run(start)
    signal_agent = agents["signal_agent"]
    if hasattr(signal_agent, "run_with_note"):
        signals, signal_note = signal_agent.run_with_note(start)
    else:
        signals, signal_note = signal_agent.run(start), None
    assets = agents["assets"].run(start)
    actual = actual_forecast(start, *pair)
    quality = forecast_quality(forecast, actual)
    record.update(
        workday=any(assets.hvac.occupied),
        outage_slots=sum(signals.outage),
        forecast_fallback_used=forecast.fallback_used,
        forecast_note=forecast.note,
        signal_note=signal_note,
        **quality,
    )

    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    fixed = {
        "forecast_agent": _Fixed(forecast),
        "signal_agent": _Fixed(signals),
        "assets": _Fixed(assets),
    }
    for w in weights:
        row = _blank_row(day, OPTIMISER, w)
        plan = run_plan(start, w, planner=agents["planner"], risk=agents["risk"], **fixed)
        row.update(
            status=plan.status,
            plan_safe=is_safe(plan),
            risk_ok=bool(plan.risk and plan.risk.ok),
            replans=plan.risk.replans if plan.risk else 0,
            reserve_margin_kwh=plan.reserve_margin_kwh,
            fallback_used=plan.fallback_used,
            planned_cost_bdt=plan.totals.cost_bdt,
            planned_co2_kg=plan.totals.co2_kg,
        )
        why = _settle_into(row, plan.schedule, actual, signals, assets)
        if why:
            problems.append(f"optimiser w={w:g} not settled: {why}")
        rows.append(row)

    for name in BASELINES:
        row = _blank_row(day, name, None)
        schedule = BASELINE_FUNCS[name](actual, signals, assets)
        violations = find_violations(schedule, actual, signals, assets)
        row.update(status="rule", plan_safe=not violations, violations=violations)
        if violations:
            problems.append(f"{name} breaks hard constraints: {'; '.join(violations)}")
        else:
            why = _settle_into(row, schedule, actual, signals, assets)
            if why:
                problems.append(f"{name} not settled: {why}")
        row.update(planned_cost_bdt=row["cost_bdt"], planned_co2_kg=row["co2_kg"])  # built on actuals
        rows.append(row)

    base = {r["method"]: r for r in rows if r["method"] in BASELINES}
    for row in rows:
        for b, ref in base.items():
            ok = row["settled"] and ref["settled"]
            row[f"cost_saved_vs_{b}_pct"] = (
                saved_pct(ref["adjusted_cost_bdt"], row["adjusted_cost_bdt"]) if ok else None
            )
            row[f"co2_saved_vs_{b}_pct"] = (
                saved_pct(ref["adjusted_co2_kg"], row["adjusted_co2_kg"]) if ok else None
            )
    record.update(
        included=not problems,
        excluded_reason="; ".join(problems) or None,
        methods=rows,
    )
    return record


def select_days(first: date, last: date, days: int | None = None) -> list[date]:
    """Every day in ``[first, last]``, or ``days`` of them evenly spaced (first and last included)."""
    if last < first:
        raise ValueError(f"end {last} is before start {first}")
    span = (last - first).days + 1
    if days is None or days >= span:
        return [first + timedelta(days=i) for i in range(span)]
    if days < 1:
        raise ValueError("days must be >= 1")
    if days == 1:
        return [first]
    return sorted({first + timedelta(days=round(i * (span - 1) / (days - 1))) for i in range(days)})


def _refuse_stubs(agents: dict[str, Any]) -> None:
    stubs = [k for k, a in agents.items() if type(a).__module__ == "energy_brain.stubs"]
    if stubs:
        raise ValueError(f"stub agents are not allowed in the backtest (placeholder data): {sorted(stubs)}")


def run_backtest(
    days: Iterable[date],
    weights: Iterable[float],
    agents: dict[str, Any],
    actuals: Actuals,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Day records for ``days`` (midnight starts, site tz) at every weight. Refuses stub agents."""
    _refuse_stubs(agents)
    weights = list(weights)
    if not weights or any(not 0.0 <= w <= 1.0 for w in weights):
        raise ValueError(f"weights must be a non-empty list in [0, 1], got {weights}")
    days = list(days)
    out = []
    for i, d in enumerate(days):
        start = datetime.combine(d, time(), site_tz())
        out.append(backtest_day(start, weights, agents, actuals))
        if progress:
            progress(i + 1, len(days), d.isoformat())
    return out
