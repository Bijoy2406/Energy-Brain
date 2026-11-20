"""Backtest report (#20): summary, ``reports/backtest.json`` and ``reports/backtest.csv``.

Every headline number in the pitch comes from here. Savings are always given per weight and with the baseline
named (PRD section 8, fairness of claims); headline figures use the ADJUSTED cost and CO2 (end-of-day battery
shortfall refilled at the off-peak tariff, see ``evaluator.settle``); raw figures are kept beside them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import statistics
import subprocess
from collections.abc import Iterable
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

import energy_brain
from energy_brain.evaluator.backtest import FORECAST_FIELDS, OPTIMISER, ROW_FIELDS
from energy_brain.evaluator.baselines import BASELINES, SOLAR_FIRST, TIME_OF_USE

SCHEMA_VERSION = 1
#: Rounding of every float written (keeps reruns byte-identical; well below any reported precision).
DECIMALS = 4

FORECAST_LABEL = (
    "Forecast MAE and P10-P90 coverage are measured with observed weather as forecast input (tier-0), NOT a "
    "true day-ahead weather forecast, so real-world error would be larger."
)
CAVEATS = (
    "Out-of-sample: test days are in 2024; the forecast models were trained on 2022-2023 "
    "(reports/forecast_metrics.json).",
    "Synthetic load: the factory base load is synthetic (data.synthetic_load, #7); no real meter data.",
    "Simulated PV: solar output is simulated with pvlib from NASA POWER / Open-Meteo weather (#6), not "
    "metered.",
    "Synthetic outages: the load-shedding calendar is a seeded synthetic calendar (#9), not a utility "
    "record.",
    FORECAST_LABEL,
    "Because the forecast is near-perfect here, settlement against actuals changes little; this backtest "
    "does not measure robustness to real forecast error.",
    "Baselines are built on ACTUAL solar and load (real-time rules, perfect information) and settled with "
    "the same recourse as the optimiser: conservative for the optimiser.",
    "EV: both baselines charge the vans from 00:00 (off-peak) because today's required energy is due before "
    "07:00; a literal 'charge on arrival' at 18:00 would pay the peak tariff. This understates the savings "
    "by about 144 kWh x 3.29 BDT (about 474 BDT) per day, so the comparison is conservative.",
    "Headline cost and CO2 are ADJUSTED: an end-of-day battery shortfall (end SoC below the start) is "
    "charged at the off-peak tariff / one-way efficiency and the grid CO2 factor; raw figures are reported "
    "too.",
    "HVAC runs at its occupied minimum in every method (no thermal model), so HVAC is not a source of "
    "savings.",
    "Settlement reuses the Risk agent's shortfall recourse: when actual solar beats the plan with no gap to "
    "close, the extra solar is curtailed instead of replacing grid import. This only penalises the optimiser "
    "(the baselines are built on actuals).",
    "The grid CO2 factor is flat (no hourly grid-carbon data), so shifting grid use in time cannot cut CO2; "
    "CO2 changes come only from diesel vs grid and battery round-trip losses.",
)

#: Numeric row fields summed per method in the summary.
SUM_FIELDS = (
    "cost_bdt",
    "co2_kg",
    "adjusted_cost_bdt",
    "adjusted_co2_kg",
    "grid_kwh",
    "solar_kwh",
    "generator_kwh",
    "generator_hours",
    "diesel_litres",
    "unserved_kwh",
    "dumped_kwh",
    "end_soc_shortfall_kwh",
    "recourse_grid_kwh",
    "recourse_battery_kwh",
    "recourse_generator_kwh",
    "recourse_charge_cut_kwh",
)
DAY_FIELDS = ("workday", "outage_slots", "included")


def _round(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        r = round(value, DECIMALS)
        return 0.0 if r == 0 else r
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_round(v) for v in value]
    return value


def _pct(base: float, value: float) -> float | None:
    return None if abs(base) < 1e-9 else (base - value) / base * 100.0


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def _method_rows(days: list[dict], method: str, weight: float | None) -> list[dict]:
    out = []
    for d in days:
        for r in d["methods"]:
            if r["method"] == method and (weight is None or r["weight"] == weight):
                out.append(r)
    return out


def _totals(rows: list[dict]) -> dict[str, float]:
    return {f: sum(r[f] for r in rows) for f in SUM_FIELDS}


def _compare(days: list[dict], weight: float, baseline: str) -> dict[str, Any]:
    """Optimiser at ``weight`` vs ``baseline`` over the included ``days``."""
    opt = _method_rows(days, OPTIMISER, weight)
    ref = _method_rows(days, baseline, None)
    o, b = _totals(opt), _totals(ref)

    def subset(pred) -> dict[str, Any]:
        pairs = [(x, y) for d, x, y in zip(days, opt, ref, strict=True) if pred(d)]
        oc = sum(x["adjusted_cost_bdt"] for x, _ in pairs)
        bc = sum(y["adjusted_cost_bdt"] for _, y in pairs)
        oe = sum(x["adjusted_co2_kg"] for x, _ in pairs)
        be = sum(y["adjusted_co2_kg"] for _, y in pairs)
        return {"days": len(pairs), "cost_saved_pct": _pct(bc, oc), "co2_saved_pct": _pct(be, oe)}

    cost_day = [x[f"cost_saved_vs_{baseline}_pct"] for x in opt]
    co2_day = [x[f"co2_saved_vs_{baseline}_pct"] for x in opt]
    return {
        "baseline": baseline,
        "cost_saved_pct": _pct(b["adjusted_cost_bdt"], o["adjusted_cost_bdt"]),
        "co2_saved_pct": _pct(b["adjusted_co2_kg"], o["adjusted_co2_kg"]),
        "cost_saved_bdt": b["adjusted_cost_bdt"] - o["adjusted_cost_bdt"],
        "co2_saved_kg": b["adjusted_co2_kg"] - o["adjusted_co2_kg"],
        "raw_cost_saved_pct": _pct(b["cost_bdt"], o["cost_bdt"]),
        "raw_co2_saved_pct": _pct(b["co2_kg"], o["co2_kg"]),
        "generator_hours_saved": b["generator_hours"] - o["generator_hours"],
        "diesel_litres_saved": b["diesel_litres"] - o["diesel_litres"],
        "days_cost_worse": sum(
            1 for x, y in zip(opt, ref, strict=True) if x["adjusted_cost_bdt"] > y["adjusted_cost_bdt"] + 1e-6
        ),
        "days_co2_worse": sum(
            1 for x, y in zip(opt, ref, strict=True) if x["adjusted_co2_kg"] > y["adjusted_co2_kg"] + 1e-6
        ),
        "per_day_cost_saved_pct": _stats([v for v in cost_day if v is not None]),
        "per_day_co2_saved_pct": _stats([v for v in co2_day if v is not None]),
        "outage_days": subset(lambda d: d["outage_slots"] > 0),
        "no_outage_days": subset(lambda d: d["outage_slots"] == 0),
        "workdays": subset(lambda d: d["workday"]),
        "off_days": subset(lambda d: not d["workday"]),
    }


def summarise(days: list[dict], weights: list[float], headline_weight: float) -> dict[str, Any]:
    """Summary over the included days: totals per method, savings vs each baseline, plan health."""
    inc = [d for d in days if d.get("included")]
    by_weight: dict[str, Any] = {}
    for w in weights:
        opt = _method_rows(inc, OPTIMISER, w)
        all_opt = _method_rows(days, OPTIMISER, w)
        by_weight[f"{w:g}"] = {
            "weight": w,
            "totals": {
                OPTIMISER: _totals(opt),
                **{b: _totals(_method_rows(inc, b, None)) for b in BASELINES},
            },
            "vs": {b: _compare(inc, w, b) for b in BASELINES},
            "plan_health": {
                "days_planned": len(all_opt),
                "not_optimal": sum(1 for r in all_opt if r["status"] != "optimal"),
                "unsafe": sum(1 for r in all_opt if r["plan_safe"] is False),
                "risk_not_ok": sum(1 for r in all_opt if r["risk_ok"] is False),
                "replanned": sum(1 for r in all_opt if (r["replans"] or 0) > 0),
                "fallback_used": sum(1 for r in all_opt if r["fallback_used"]),
                "risk_not_ok_dates": [r["date"] for r in all_opt if r["risk_ok"] is False],
                "unsafe_dates": [r["date"] for r in all_opt if r["plan_safe"] is False],
            },
        }

    fc = [d for d in days if "solar_mae_kw" in d]
    day_slots = sum(d["solar_daylight_slots"] for d in fc)

    def mean(key: str) -> float | None:
        vals = [d[key] for d in fc if d[key] is not None]
        return statistics.fmean(vals) if vals else None

    forecast = {
        "label": FORECAST_LABEL,
        "days": len(fc),
        "tier0_days": sum(1 for d in fc if not d["forecast_fallback_used"]),
        "fallback_dates": [d["date"] for d in fc if d["forecast_fallback_used"]],
        "solar_mae_kw": mean("solar_mae_kw"),
        "load_mae_kw": mean("load_mae_kw"),
        "solar_p10_p90_coverage_pct": mean("solar_p10_p90_coverage_pct"),
        "solar_daylight_p10_p90_coverage_pct": (
            100.0 * sum(d["solar_daylight_hits"] for d in fc) / day_slots if day_slots else None
        ),
        "load_p10_p90_coverage_pct": mean("load_p10_p90_coverage_pct"),
        "coverage_definition": "share of 15-min slots with p10 <= actual <= p90 (inclusive), pooled over "
        "all slots of the days, as in reports/forecast_metrics.json; daylight = actual > 0 or P90 > 0",
    }
    excluded = [d for d in days if not d.get("included")]
    return {
        "headline_weight": headline_weight,
        "days": {
            "requested": len(days),
            "included": len(inc),
            "excluded": len(excluded),
            "excluded_dates": [{"date": d["date"], "reason": d.get("excluded_reason")} for d in excluded],
            "outage_days": sum(1 for d in inc if d["outage_slots"] > 0),
            "workdays": sum(1 for d in inc if d["workday"]),
        },
        "by_weight": by_weight,
        "forecast": forecast,
    }


def headline(summary: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pitch-ready headline at the headline weight, with its caveats and every baseline named."""
    config = config or {}
    w = summary["headline_weight"]
    block = summary["by_weight"][f"{w:g}"]
    d = summary["days"]
    names = {SOLAR_FIRST: "solar-first / charge-on-arrival rule", TIME_OF_USE: "simple time-of-use rule"}
    sample = (
        f"out-of-sample days of {config['test_year']}"
        if config.get("out_of_sample") and "test_year" in config
        else "days (NOT all out-of-sample)"
    )
    lines = [
        f"Backtest over {d['included']} {sample} (synthetic load, simulated PV, synthetic outages), "
        f"{d['outage_days']} of them with an outage; weight w={w:g} (1 = cheapest, 0 = cleanest)."
    ]
    for b in BASELINES:
        v = block["vs"][b]
        lines.append(
            f"vs the {names[b]}: cost {_change(v['cost_saved_pct'])} "
            f"({_amount(v['cost_saved_bdt'], 'BDT')}), "
            f"CO2 {_change(v['co2_saved_pct'])} ({_amount(v['co2_saved_kg'], 'kg')}); "
            f"outage days cost {_change(v['outage_days']['cost_saved_pct'])}, other days "
            f"{_change(v['no_outage_days']['cost_saved_pct'])}; costlier than the baseline on "
            f"{v['days_cost_worse']} of {d['included']} days."
        )
    for k in (k for k in summary["by_weight"] if k != f"{w:g}"):
        vs = summary["by_weight"][k]["vs"]
        lines.append(
            f"At w={k}: vs solar-first cost {_change(vs[SOLAR_FIRST]['cost_saved_pct'])}, CO2 "
            f"{_change(vs[SOLAR_FIRST]['co2_saved_pct'])}; vs time-of-use cost "
            f"{_change(vs[TIME_OF_USE]['cost_saved_pct'])}, CO2 {_change(vs[TIME_OF_USE]['co2_saved_pct'])}."
        )
    lines.append(FORECAST_LABEL)
    return {"weight": w, "baseline_names": names, "lines": lines}


def _change(saved_pct: float | None) -> str:
    """'4.7 % lower' / '0.3 % higher' for a saving percentage (positive = lower than the baseline)."""
    if saved_pct is None:
        return "n/a"
    if abs(saved_pct) < 0.05:
        return "about the same (within 0.05 %)"
    return f"{abs(saved_pct):.1f} % {'lower' if saved_pct > 0 else 'higher'}"


def _amount(saved: float, unit: str) -> str:
    return f"{abs(saved):,.0f} {unit} {'less' if saved >= 0 else 'more'}"


# ---------------------------------------------------------------------------------------------- config


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip()

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "dirty": bool(status) if status is not None else None}


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def collect_config(
    *,
    days: Iterable[date],
    weights: list[float],
    headline_weight: float,
    root: Path,
    data_files: dict[str, Path],
    models_dir: Path | None,
    signal_provenance: str | None,
    test_year: int,
    train_years: list[int],
    data_source: str,
) -> dict[str, Any]:
    """Everything needed to rerun and audit the backtest (dates, weights, file hashes, git, provenance)."""
    days = sorted(days)
    models = {}
    if models_dir is not None and models_dir.is_dir():
        models = {p.name: sha256(p) for p in sorted(models_dir.glob("*.txt"))}
    return {
        "first_day": days[0].isoformat() if days else None,
        "last_day": days[-1].isoformat() if days else None,
        "n_days": len(days),
        "weights": weights,
        "headline_weight": headline_weight,
        "out_of_sample": all(d.year == test_year for d in days),
        "test_year": test_year,
        "train_years": train_years,
        "data_source": data_source,
        "data_sha256": {k: sha256(p) for k, p in sorted(data_files.items())},
        "models_sha256": models,
        "config_sha256": {name: sha256(root / "config" / name) for name in ("site.yaml", "signals.yaml")},
        "git": git_commit(root),
        "versions": {
            "energy_brain": energy_brain.__version__,
            "python": platform.python_version(),
            **{p: _version(p) for p in ("pandas", "pyomo", "highspy", "lightgbm", "pvlib")},
        },
        "provenance": {
            "signals": signal_provenance,
            "load": "synthetic factory load (data.synthetic_load, #7), engineering assumptions in "
            "config/site.yaml + real ambient temperature",
            "pv": "simulated with pvlib (data.simulate_pv, #6) from NASA POWER weather, Open-Meteo for gaps",
            "weather": "NASA POWER (MERRA-2 / GEOS-FP) hourly, Open-Meteo backup (#5)",
            "outages": "synthetic seeded load-shedding calendar (data.outages, #9)",
            "tariff": "BERC MT-3 retail tariff (2024-02)",
            "grid_co2": "DoE / UNFCCC BUR1 flat 0.62 kg/kWh (2022)",
            "forecast": "LightGBM quantile models (#11, #12) fed observed weather (tier 0)",
        },
    }


# ---------------------------------------------------------------------------------------------- writers


def build_report(
    days: list[dict], weights: list[float], headline_weight: float, config: dict, run: dict | None = None
) -> dict[str, Any]:
    summary = summarise(days, weights, headline_weight)
    return _round(
        {
            "report": "Gridpilot AI backtest (#20)",
            "schema_version": SCHEMA_VERSION,
            "caveats": list(CAVEATS),
            "headline": headline(summary, config),
            "summary": summary,
            "config": config,
            "run": run or {},
            "days": days,
        }
    )


def write_json(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


CSV_COLUMNS = (*ROW_FIELDS, *DAY_FIELDS, *FORECAST_FIELDS)


def csv_rows(report: dict) -> list[dict[str, Any]]:
    """One row per day and per method (days without any method row, e.g. no actuals, are omitted)."""
    out = []
    for d in report["days"]:
        for r in d["methods"]:
            row = {k: r.get(k) for k in ROW_FIELDS}
            row["violations"] = " | ".join(r.get("violations") or [])
            for k in (*DAY_FIELDS, *FORECAST_FIELDS):
                row[k] = d.get(k)
            out.append(row)
    return out


def write_csv(report: dict, path: Path) -> None:
    """CSV with ``#`` comment lines first (caveats); read with ``pandas.read_csv(path, comment="#")``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        for line in report["caveats"]:
            fh.write(f"# {line}\n")
        fh.write(
            "# Percentages: (baseline - method) / baseline * 100 on ADJUSTED cost / CO2, per named "
            "baseline. Weight is blank for baselines.\n"
        )
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in csv_rows(report):
            writer.writerow({k: "" if v is None else v for k, v in row.items()})
