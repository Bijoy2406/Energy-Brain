"""Deterministic CFO reports from adjusted evaluator rows. No agents or solver calls."""

from __future__ import annotations

import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from math import fsum
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from energy_brain.api.backtest_view import METHODS, days_view
from energy_brain.data.signals_lookup import load_signals_config

FIELDS = ("adjusted_cost_bdt", "adjusted_co2_kg", "generator_hours", "diesel_litres", "unserved_kwh")
SYNTHETIC = (
    "Synthetic load, simulated PV, synthetic outages; 2024 out-of-sample days. "
    "This is not a live deployment and not a guarantee of future savings."
)


class MonthNotInBacktest(ValueError):
    """The requested month does not overlap the backtest."""


@dataclass(frozen=True)
class ReportData:
    month: str | None
    period: str
    weight: float
    totals: dict[str, dict[str, float]]
    comparisons: dict[str, dict[str, float | None]]
    daily: list[dict[str, Any]]
    excluded: list[str]
    outage_days: int
    baseline_names: dict[str, str]
    caveats: list[str]
    config: dict[str, Any]


def report_data(report: dict[str, Any], month: str | None = None, weight: float | None = None) -> ReportData:
    """Sum included days; use evaluator full-period totals to retain its rounding precision.

    Weight membership (including the default headline) is shared with /backtest/days.
    CO2 change is optimiser MINUS baseline; cost savings are baseline MINUS optimiser.
    """
    view = days_view(report, weight)
    w = next(x for x in view.weights if abs(x - view.weight) < 1e-9)
    config = report["config"]
    first, last = config["first_day"][:7], config["last_day"][:7]
    if month is not None and (not first <= month <= last or not 1 <= int(month[5:]) <= 12):
        raise MonthNotInBacktest(f"month {month} is not in the backtest; available: {first} .. {last}")
    rows = [d for d in report["days"] if month is None or d["date"].startswith(month)]
    included = [d for d in rows if d["included"]]
    daily = []
    for day in included:
        methods = {
            r["method"]: {field: float(r[field]) for field in FIELDS}
            for r in day["methods"]
            if r["method"] in METHODS and (r["method"] != "optimiser" or abs(float(r["weight"]) - w) < 1e-9)
        }
        daily.append({"date": day["date"], "methods": methods})
    totals = {
        method: {field: fsum(d["methods"][method][field] for d in daily) for field in FIELDS}
        for method in METHODS
    }
    if month is None:
        stored = report.get("summary", {}).get("by_weight", {}).get(f"{w:g}", {}).get("totals")
        if stored:
            totals = {method: {field: float(stored[method][field]) for field in FIELDS} for method in METHODS}
    comparisons = {}
    opt = totals["optimiser"]
    for method in METHODS[1:]:
        base = totals[method]
        saved = base["adjusted_cost_bdt"] - opt["adjusted_cost_bdt"]
        change = opt["adjusted_co2_kg"] - base["adjusted_co2_kg"]
        comparisons[method] = {
            "cost_saved_bdt": saved,
            "cost_saved_pct": 100 * saved / base["adjusted_cost_bdt"] if base["adjusted_cost_bdt"] else None,
            "co2_change_kg": change,
            "co2_change_pct": 100 * change / base["adjusted_co2_kg"] if base["adjusted_co2_kg"] else None,
        }
        if month is None:
            stored_vs = report.get("summary", {}).get("by_weight", {}).get(f"{w:g}", {}).get("vs", {})
            if method in stored_vs:
                comparison = stored_vs[method]
                comparisons[method] = {
                    "cost_saved_bdt": comparison["cost_saved_bdt"],
                    "cost_saved_pct": comparison["cost_saved_pct"],
                    "co2_change_kg": -comparison["co2_saved_kg"],
                    "co2_change_pct": -comparison["co2_saved_pct"],
                }
    full_year = config["first_day"].endswith("-01-01") and config["last_day"] == f"{first[:4]}-12-31"
    period = month or (
        f"{first[:4]} full year" if full_year else f"{config['first_day']} to {config['last_day']}"
    )
    return ReportData(
        month,
        period,
        w,
        totals,
        comparisons,
        daily,
        [
            f"{d['date']}: {d.get('excluded_reason') or 'reason not recorded'}"
            for d in rows
            if not d["included"]
        ],
        sum(d["outage_slots"] > 0 for d in included),
        report["headline"]["baseline_names"],
        list(report["caveats"]),
        config,
    )


def _safe(text: str) -> str:
    """Built-in fonts: retain Latin-1, normalise common typographic punctuation."""
    for a, b in {
        "\u2013": "-",
        "\u2014": "-",
        "\u2011": "-",
        "\u2019": "'",
        "\u2018": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2082": "2",
    }.items():
        text = text.replace(a, b)
    return text.encode("latin-1", errors="replace").decode("latin-1")


class _Page:
    def __init__(self, canvas: Canvas):
        self.canvas = canvas
        self.y = 800.0

    def text(self, text: str, size: float = 10, bold: bool = False, gap: float = 5) -> None:
        font = "Helvetica-Bold" if bold else "Helvetica"
        self.canvas.setFont(font, size)
        for line in simpleSplit(_safe(text), font, size, 511):
            self.canvas.drawString(42, self.y, line)
            self.y -= size * 1.25
        self.y -= gap

    def heading(self, title: str) -> None:
        self.text(title, 14, True, 10)

    def table(self, rows: list[tuple[str, str, str]], headers: tuple[str, str, str]) -> None:
        for i, row in enumerate([headers, *rows]):
            if i % 2 == 0:
                self.canvas.setFillColorRGB(0.94, 0.96, 0.97)
                self.canvas.rect(42, self.y - 7, 511, 23, fill=1, stroke=0)
                self.canvas.setFillColorRGB(0.05, 0.12, 0.19)
            self.canvas.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 9)
            self.canvas.drawString(48, self.y, _safe(row[0]))
            self.canvas.drawRightString(420, self.y, _safe(row[1]))
            self.canvas.drawRightString(547, self.y, _safe(row[2]))
            self.y -= 23
        self.y -= 8

    def end(self, number: int, period: str) -> None:
        self.canvas.setFont("Helvetica", 8)
        self.canvas.drawString(42, 25, f"Gridpilot AI | {period} | Historical synthetic replay")
        self.canvas.drawRightString(553, 25, f"{number} / 3")
        self.canvas.showPage()


def _percent(value: float | None) -> str:
    return "n/a (zero baseline)" if value is None else f"{value:+.3f}%"


def _chart(page: _Page, data: ReportData, method: str) -> None:
    """Daily bars for months, seven-day buckets for the full period; both include negatives."""
    page.text(data.baseline_names[method], 10, True)
    if not data.daily:
        page.text("No included days; no daily savings to chart.")
        return
    daily = [
        (
            d["date"],
            d["methods"][method]["adjusted_cost_bdt"] - d["methods"]["optimiser"]["adjusted_cost_bdt"],
        )
        for d in data.daily
    ]
    buckets: dict[str, float] = defaultdict(float)
    origin = date.fromisoformat(data.config["first_day"])
    for day, value in daily:
        key = (
            day if data.month else (origin + timedelta(days=(date.fromisoformat(day) - origin).days // 7 * 7))
        )
        buckets[str(key)] += value
    values = list(buckets.values())
    low, high = min(0, min(values)), max(0, max(values))
    if low == high:
        high = low + 1
    left, bottom, width, height = 100, page.y - 99, 453, 85
    scale = height / (high - low)
    zero = bottom - low * scale
    c = page.canvas
    c.setFont("Helvetica", 8)
    c.drawString(42, page.y, "Savings (BDT)")
    for value in sorted({low, high} - {0.0}):
        y = bottom + (value - low) * scale
        c.drawRightString(left - 8, y - 3, f"{value:,.0f}")
    for i, value in enumerate(values):
        c.setFillColorRGB(*((0.08, 0.41, 0.47) if value >= 0 else (0.72, 0.23, 0.12)))
        c.rect(
            left + i * width / len(values),
            min(zero, zero + value * scale),
            width / len(values) * 0.8,
            abs(value * scale),
            fill=1,
            stroke=0,
        )
    c.setFillColorRGB(0.05, 0.12, 0.19)
    c.setStrokeColorRGB(0.3, 0.35, 0.4)
    c.line(left, bottom, left, bottom + height)
    c.line(left, zero, left + width, zero)
    # A small negative minimum can lie almost on zero. Label zero on the right so
    # its label never collides with the minimum tick on the left.
    c.drawString(left + width + 4, zero - 3, "0")
    c.drawString(left, bottom - 13, next(iter(buckets)))
    c.drawRightString(left + width, bottom - 13, next(reversed(buckets)))
    label = "Day" if data.month else "Week starting (7-day buckets from first backtest day)"
    c.drawCentredString(left + width / 2, bottom - 25, label)
    page.y = bottom - 42
    best, worst = max(daily, key=lambda d: d[1]), min(daily, key=lambda d: d[1])
    page.text(f"Best day: {best[0]} ({best[1]:+,.2f} BDT); worst day: {worst[0]} ({worst[1]:+,.2f} BDT).", 9)


def _provenance(config: dict[str, Any], prefix: str = "") -> list[str]:
    lines = []
    for key, value in config.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            if "sha256" in name:
                lines.append(f"{name}: " + "; ".join(f"{k}={str(v)[:12]}" for k, v in value.items()))
            else:
                lines.extend(_provenance(value, name))
        else:
            if name == "git.commit" and value:
                value = str(value)[:12]
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            lines.append(f"{name}: {value}")
    return lines


def render_report(data: ReportData) -> bytes:
    """Three A4 pages with invariant PDF metadata/IDs and only built-in Helvetica fonts."""
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=A4, invariant=1, pageCompression=1)
    canvas.setTitle("Gridpilot AI: savings and CO2 report")
    canvas.setAuthor("Gridpilot AI")
    canvas.setSubject("Historical replay on synthetic data")
    page = _Page(canvas)
    page.heading("Gridpilot AI: savings and CO2 report")
    page.text(f"Period: {data.period} | Optimiser weight: {data.weight:g} (1 = cheapest, 0 = cleanest)")
    canvas.setFillColorRGB(0.98, 0.95, 0.86)
    canvas.setStrokeColorRGB(0.65, 0.46, 0.13)
    canvas.rect(36, page.y - 62, 523, 78, fill=1, stroke=1)
    canvas.setFillColorRGB(0.05, 0.12, 0.19)
    page.text("Historical replay on synthetic data", 12, True)
    page.text(SYNTHETIC, 10)
    page.y -= 22
    page.text(
        f"Days: {len(data.daily)} included; {len(data.excluded)} excluded; "
        f"{data.outage_days} included days with an outage."
    )
    opt = data.totals["optimiser"]
    for method in METHODS[1:]:
        base, change = data.totals[method], data.comparisons[method]
        page.text(f"Versus {data.baseline_names[method]}", 11, True)
        page.table(
            [
                ("Optimiser", f"{opt['adjusted_cost_bdt']:,.2f}", f"{opt['adjusted_co2_kg']:,.2f}"),
                ("Baseline", f"{base['adjusted_cost_bdt']:,.2f}", f"{base['adjusted_co2_kg']:,.2f}"),
                (
                    "Savings / CO2 change",
                    f"{change['cost_saved_bdt']:+,.2f}",
                    f"{change['co2_change_kg']:+,.2f}",
                ),
                (
                    "Percent of baseline",
                    _percent(change["cost_saved_pct"]),
                    _percent(change["co2_change_pct"]),
                ),
            ],
            ("Adjusted period totals", "Cost (BDT)", "CO2 (kg)"),
        )
        direction = "increase" if change["co2_change_kg"] > 0 else "decrease"
        page.text(
            f"CO2 {direction}: {change['co2_change_kg']:+,.2f} kg "
            f"({_percent(change['co2_change_pct'])}). Positive CO2 change means more emissions.",
            9,
            True,
        )
        page.table(
            [
                ("Generator hours", f"{opt['generator_hours']:,.2f}", f"{base['generator_hours']:,.2f}"),
                ("Diesel litres", f"{opt['diesel_litres']:,.2f}", f"{base['diesel_litres']:,.2f}"),
                ("Unserved energy (kWh)", f"{opt['unserved_kwh']:,.2f}", f"{base['unserved_kwh']:,.2f}"),
            ],
            ("Reliability and fuel", "Optimiser", "Baseline"),
        )
    page.text(
        "Savings = baseline - optimiser. CO2 change = optimiser - baseline. "
        "Percentages use the corresponding baseline total. Unserved energy must be zero.",
        9,
    )
    if data.excluded:
        by_reason: dict[str, list[str]] = defaultdict(list)
        for excluded in data.excluded:
            day, _, reason = excluded.partition(": ")
            by_reason[reason].append(day)
        reasons = [
            f"{days[0]}: {reason}"
            if len(days) == 1
            else f"{len(days)} days, {days[0]} to {days[-1]}: {reason}"
            for reason, days in by_reason.items()
        ]
        page.text("Excluded days (grouped by reason): " + "; ".join(reasons), 8)
    page.end(1, data.period)

    page = _Page(canvas)
    page.heading("Savings through the period")
    page.text("Positive bars mean lower cost; negative bars mean higher cost. Included days only.", 9)
    for method in METHODS[1:]:
        _chart(page, data, method)
    page.heading("How to read these numbers")
    page.text(
        "Cost and CO2 are ADJUSTED: an end-of-day battery shortfall is charged using the "
        "lowest grid tariff, one-way battery efficiency and grid CO2 factor; a surplus earns no credit. "
        "Baselines are rule-based and use actual solar and load (perfect information). "
        "HVAC runs at its occupied minimum in every method (no pre-cooling modelled). "
        "The grid CO2 factor is flat (no hourly grid carbon data).",
        9,
    )
    cfg = load_signals_config()
    t, g, d = cfg.tariff, cfg.grid_emission, cfg.diesel
    page.text(
        f"Tariff: {t.regulation}, {t.customer_class}. Flat 06:00-17:00: "
        f"{t.rates.flat_bdt_per_kwh:g}; peak 17:00-23:00: {t.rates.peak_bdt_per_kwh:g}; "
        f"off-peak 23:00-06:00: {t.rates.off_peak_bdt_per_kwh:g} BDT/kWh. "
        f"Source: {t.source}; as of {t.as_of}; {t.source_url}. "
        "These are replay energy costs, not a complete utility bill.",
        9,
    )
    page.text(
        f"Grid: {g.factor_kg_per_kwh:g} kg CO2/kWh (flat). Source: {g.authority}; "
        f"{g.report}; as of {g.as_of}; {g.source_url}.",
        9,
    )
    page.text(
        f"Diesel: {d.price_bdt_per_l:g} BDT/litre. Source: {d.price_authority}; "
        f"as of {d.as_of}; {d.source_url}. Diesel CO2: {d.emission_factor_kg_per_l:g} kg/litre. "
        f"Source: {d.emission_source}.",
        9,
    )
    page.end(2, data.period)

    page = _Page(canvas)
    page.heading("Caveats and data/model provenance")
    paragraphs = [
        *data.caveats,
        "Provenance from the backtest config (hashes: first 12 characters):",
        *_provenance(data.config),
    ]
    size = 8.2
    while (
        size > 7
        and sum(len(simpleSplit(_safe(p), "Helvetica", size, 511)) * size * 1.25 + 4 for p in paragraphs)
        > page.y - 48
    ):
        size -= 0.2
    for paragraph in paragraphs:
        page.text(paragraph, size, gap=4)
    page.end(3, data.period)
    canvas.save()
    return output.getvalue()
