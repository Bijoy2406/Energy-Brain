"""Compact views of ``reports/backtest.json`` for the dashboard (#21): the summary and a per-day series.

The 2 MB report is never sent whole. The caveats travel with every view: synthetic load, simulated PV,
synthetic outages, out-of-sample 2024, observed weather as forecast input (tier 0).
"""

from __future__ import annotations

from typing import Any

from energy_brain.api.models import BacktestDaysResponse, BacktestSummary

SHA_KEYS = ("data_sha256", "models_sha256", "config_sha256")
METHODS = ("optimiser", "baseline_solar_first", "baseline_time_of_use")


class WeightNotInBacktest(ValueError):
    """The requested weight was not run in the backtest."""


def summary_view(report: dict[str, Any], include_sha: bool = False) -> BacktestSummary:
    config = {k: v for k, v in report.get("config", {}).items() if include_sha or k not in SHA_KEYS}
    return BacktestSummary(
        report=report.get("report", ""),
        schema_version=report.get("schema_version", 0),
        caveats=list(report.get("caveats", [])),
        headline=report["headline"],
        summary=report.get("summary", {}),
        config=config,
        run=report.get("run", {}),
    )


def _weights(report: dict[str, Any]) -> list[float]:
    return [float(w) for w in report.get("config", {}).get("weights", [])]


def days_view(report: dict[str, Any], weight: float | None = None) -> BacktestDaysResponse:
    """Per day: adjusted and raw cost/CO2 of the optimiser at ``weight`` (default: headline) and the
    baselines."""
    headline = float(report["headline"]["weight"])
    weights = _weights(report) or [headline]
    w = headline if weight is None else float(weight)
    if not any(abs(w - x) < 1e-9 for x in weights):
        available = ", ".join(f"{x:g}" for x in weights)
        raise WeightNotInBacktest(f"weight {w:g} is not in the backtest; available: {available}")
    rows = []
    for day in report.get("days", []):
        methods: dict[str, dict[str, float | None]] = {}
        for row in day.get("methods", []):
            name = row.get("method")
            if name == "optimiser" and (row.get("weight") is None or abs(float(row["weight"]) - w) > 1e-9):
                continue
            if name in METHODS:
                methods[name] = {
                    k: row.get(k) for k in ("adjusted_cost_bdt", "adjusted_co2_kg", "cost_bdt", "co2_kg")
                }
        rows.append(
            {
                "date": day["date"],
                "weekday": day.get("weekday", ""),
                "workday": bool(day.get("workday")),
                "outage_slots": int(day.get("outage_slots") or 0),
                "included": bool(day.get("included")),
                "excluded_reason": day.get("excluded_reason"),
                "forecast_fallback_used": bool(day.get("forecast_fallback_used")),
                "methods": methods,
            }
        )
    return BacktestDaysResponse(
        weight=w,
        headline_weight=headline,
        weights=weights,
        baseline_names=report["headline"].get("baseline_names", {}),
        caveats=list(report.get("caveats", [])),
        days=rows,
    )
