"""Read-only Explainer tools (#19): plain functions over a :class:`PlanContext`, typed JSON results.

Rules every tool follows:

* **Read only.** No tool plans, writes or changes anything; inputs are never modified.
* **Times, not slot indices.** Every slot or block carries ``time`` / ``end`` (or ``from`` / ``to``) as local
  ``HH:MM`` (Asia/Dhaka); the day end is ``24:00``. Slot numbers in planner notes are rewritten as times.
* **Units in keys** (``_kw``, ``_kwh``, ``_bdt``, ``_kg``, ``_l``, ``_pct``, ``_h``, ``_bdt_per_kwh`` ...),
  the same convention as ``schemas.py``. The verifier reads the unit of every number from its key.
* **The tools do the arithmetic.** Sums, energies, differences, percentages and hourly means are computed
  here, so the LLM never converts or adds anything. Floats are rounded (``ROUND``) to keep results compact.

``TOOL_DECLARATIONS`` holds the OpenAI function-calling declarations; :meth:`ExplainerTools.call` validates
the arguments against them before running a tool.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from energy_brain.explainer.context import PlanContext
from energy_brain.forecast import parse_note as parse_forecast_note
from energy_brain.planner import is_safe, rules
from energy_brain.risk import parse_reason
from energy_brain.signals import parse_note as parse_signal_note
from energy_brain.timeaxis import n_slots, slot_hours, slot_minutes, slot_times

#: Decimal places of every rounded float (kW, kWh, BDT, kg, L, %); hours use 2.
ROUND = 1
#: Ranges longer than this many slots are returned as hourly means instead of per-slot rows.
MAX_ROWS = 12
#: Most blocks returned by ``find_slots`` (the total always covers all of them).
MAX_BLOCKS = 12
#: Tolerance (kW / kWh) for "at a limit" flags and "on" tests.
TOL = 1e-3

FLOWS = (
    "grid_import",
    "solar_used",
    "solar_curtailed",
    "battery_charge",
    "battery_discharge",
    "generator",
    "base_load",
    "hvac",
    "ev_charge",
)

CONDITIONS = (
    "generator_on",
    "battery_charging",
    "battery_discharging",
    "grid_import",
    "solar_curtailed",
    "ev_charging",
    "outage",
    "peak_tariff",
    "off_peak_tariff",
    "flat_tariff",
)


class ToolArgumentError(ValueError):
    """The model called a tool with missing, unknown or invalid arguments."""


def _r(x: float, nd: int = ROUND) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


_TIME = {"type": "string", "description": "local time HH:MM (24 h), e.g. 19:00"}
_RANGE = {
    "type": "object",
    "properties": {
        "start": {"type": "string", "description": "range start HH:MM (inclusive)"},
        "end": {"type": "string", "description": "range end HH:MM (exclusive; 24:00 = end of day)"},
    },
    "required": ["start", "end"],
    "additionalProperties": False,
}
_NONE = {"type": "object", "properties": {}, "additionalProperties": False}


def _decl(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    fn = {"name": name, "description": description, "parameters": parameters}
    return {"type": "function", "function": fn}


TOOL_DECLARATIONS: list[dict[str, Any]] = [
    _decl(
        "get_plan_summary",
        "Day overview: date, weight, status, safe flag, Risk result, fallbacks, day totals, notes.",
        _NONE,
    ),
    _decl(
        "get_slot",
        "One 15-minute slot at a time: every power flow, battery SoC, tariff, outage, forecast and the "
        "limits that are binding in that slot.",
        {
            "type": "object",
            "properties": {"time": _TIME},
            "required": ["time"],
            "additionalProperties": False,
        },
    ),
    _decl(
        "get_slot_range",
        "A time range: energy per flow, cost, CO2, battery SoC start/end/min/max, outages, and rows "
        "(15-minute rows up to 3 h, else hourly means).",
        _RANGE,
    ),
    _decl(
        "find_slots",
        "Time blocks where a condition holds, with duration and energy per block and in total.",
        {
            "type": "object",
            "properties": {"condition": {"type": "string", "enum": list(CONDITIONS)}},
            "required": ["condition"],
            "additionalProperties": False,
        },
    ),
    _decl("get_forecast", "Solar and base-load forecast P10/P50/P90 over a time range.", _RANGE),
    _decl("get_signals", "Tariff windows, outage blocks, grid CO2 factor, diesel price and CO2.", _NONE),
    _decl("get_assets", "Asset limits: battery, generator, grid, HVAC, EV fleet.", _NONE),
    _decl("get_risk_log", "Risk agent result: ok, re-plans, reserve, stress scenario, findings.", _NONE),
    _decl("get_forecast_note", "Whether a forecast or signal fallback is in use, and why.", _NONE),
    _decl(
        "get_backtest_totals",
        "Backtest of 2024 vs the two named baselines: year totals and this date's row, if present.",
        _NONE,
    ),
]

TOOL_NAMES = tuple(d["function"]["name"] for d in TOOL_DECLARATIONS)


def validate_arguments(name: str, args: Any) -> dict[str, Any]:
    """Check ``args`` against the declared JSON schema of tool ``name``; raise ``ToolArgumentError``."""
    decl = next((d["function"] for d in TOOL_DECLARATIONS if d["function"]["name"] == name), None)
    if decl is None:
        raise ToolArgumentError(f"unknown tool {name!r}; available: {', '.join(TOOL_NAMES)}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolArgumentError(f"{name}: arguments must be a JSON object")
    params = decl["parameters"]
    props = params.get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolArgumentError(f"{name}: unknown argument(s) {unknown}")
    missing = [k for k in params.get("required", []) if k not in args]
    if missing:
        raise ToolArgumentError(f"{name}: missing argument(s) {missing}")
    for key, value in args.items():
        spec = props[key]
        if spec.get("type") == "string" and not isinstance(value, str):
            raise ToolArgumentError(f"{name}: {key} must be a string")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolArgumentError(f"{name}: {key} must be one of {spec['enum']}")
    return dict(args)


_CLOCK = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*m?\.?\s*$", re.IGNORECASE)


def parse_clock(text: str) -> int:
    """Minutes after midnight for ``HH:MM`` / ``H`` / ``7 PM`` / ``7:30pm`` / ``24:00``."""
    m = _CLOCK.match(text or "")
    if not m:
        raise ToolArgumentError(f"time {text!r} is not HH:MM")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ampm:
        if not 1 <= hour <= 12:
            raise ToolArgumentError(f"time {text!r} is not a valid 12-hour time")
        hour = hour % 12 + (12 if ampm == "p" else 0)
    if minute > 59 or hour > 24 or (hour == 24 and minute):
        raise ToolArgumentError(f"time {text!r} is out of range")
    return hour * 60 + minute


class ExplainerTools:
    """The read-only tool set over one :class:`PlanContext`."""

    def __init__(self, context: PlanContext) -> None:
        self.ctx = context
        self.n = n_slots()
        self.h = slot_hours()
        self.step = slot_minutes()
        self.times: list[datetime] = slot_times(context.plan.start)
        tariff = context.signals.tariff_bdt_per_kwh
        self._t_min, self._t_max = min(tariff), max(tariff)
        self._dispatch: dict[str, Callable[..., dict[str, Any]]] = {
            "get_plan_summary": self.get_plan_summary,
            "get_slot": self.get_slot,
            "get_slot_range": self.get_slot_range,
            "find_slots": self.find_slots,
            "get_forecast": self.get_forecast,
            "get_signals": self.get_signals,
            "get_assets": self.get_assets,
            "get_risk_log": self.get_risk_log,
            "get_forecast_note": self.get_forecast_note,
            "get_backtest_totals": self.get_backtest_totals,
        }

    # ------------------------------------------------------------------ dispatch

    def call(self, name: str, args: Any = None) -> dict[str, Any]:
        """Validate ``args`` and run tool ``name``. Raises ``ToolArgumentError`` for bad calls."""
        clean = validate_arguments(name, args)
        return self._dispatch[name](**clean)

    # ------------------------------------------------------------------ time helpers

    def label(self, t: int) -> str:
        """``HH:MM`` start of slot ``t``; ``t == n`` is the end of the day (``24:00``)."""
        if t >= self.n:
            end = (self.times[-1] + timedelta(minutes=self.step)).strftime("%H:%M")
            return "24:00" if end == "00:00" else end
        return self.times[t].strftime("%H:%M")

    def slot_at(self, text: str) -> int:
        """Slot containing local clock time ``text``."""
        minutes = parse_clock(text) % (24 * 60)
        s0 = self.times[0]
        return ((minutes - (s0.hour * 60 + s0.minute)) % (24 * 60)) // self.step

    def span(self, start: str, end: str) -> tuple[int, int]:
        """Slot range ``[a, b)`` for clock times ``start`` (inclusive) and ``end`` (exclusive)."""
        a = self.slot_at(start)
        s0 = self.times[0]
        off = (parse_clock(end) - (s0.hour * 60 + s0.minute)) % (24 * 60)
        b = self.n if off == 0 and parse_clock(end) != parse_clock(start) else -(-off // self.step)
        if b <= a:
            raise ToolArgumentError(f"empty range {start}-{end}; end must be after start within the day")
        return a, b

    def blocks(self, mask: list[bool]) -> list[tuple[int, int]]:
        """Contiguous ``[a, b)`` runs of True in ``mask``."""
        out, a = [], None
        for t, v in enumerate([*mask, False]):
            if v and a is None:
                a = t
            elif not v and a is not None:
                out.append((a, t))
                a = None
        return out

    def block(self, a: int, b: int) -> dict[str, Any]:
        return {"from": self.label(a), "to": self.label(b), "duration_h": _r((b - a) * self.h, 2)}

    _SLOT_REF = re.compile(r"\bslots? (\d+)(?:\s*-\s*(\d+))?")

    def detime(self, text: str) -> str:
        """Rewrite ``slot 28`` / ``slots 72-95`` in free text as local times."""

        def repl(m: re.Match[str]) -> str:
            a = int(m.group(1))
            if a >= self.n:
                return m.group(0)
            if m.group(2) is None:
                return f"{self.label(a)}-{self.label(a + 1)}"
            b = min(int(m.group(2)), self.n - 1)
            return f"{self.label(a)}-{self.label(b + 1)}"

        return self._SLOT_REF.sub(repl, text)

    def tariff_period(self, t: int) -> str:
        v = self.ctx.signals.tariff_bdt_per_kwh[t]
        if self._t_max - self._t_min <= 1e-9:
            return "flat"
        return "peak" if v >= self._t_max - 1e-9 else "off_peak" if v <= self._t_min + 1e-9 else "flat"

    # ------------------------------------------------------------------ series helpers

    def flow(self, name: str) -> list[float]:
        return getattr(self.ctx.plan.schedule, f"{name}_kw")

    def soc_start(self, t: int) -> float:
        if t == 0:
            return self.ctx.assets.battery.initial_soc_kwh
        return self.ctx.plan.schedule.battery_soc_kwh[t - 1]

    def load_total(self, t: int) -> float:
        s = self.ctx.plan.schedule
        return s.base_load_kw[t] + s.hvac_kw[t] + s.ev_charge_kw[t]

    # ------------------------------------------------------------------ tools

    def get_plan_summary(self) -> dict[str, Any]:
        p, ctx = self.ctx.plan, self.ctx
        tot = p.totals
        return {
            "date": p.start.date().isoformat(),
            "day_window": {"from": self.label(0), "to": self.label(self.n)},
            "weight": p.weight,
            "weight_meaning": "objective w*cost + (1-w)*CO2; 1 = cheapest, 0 = cleanest",
            "status": p.status,
            "safe": is_safe(p),
            "risk_ok": p.risk.ok if p.risk else None,
            "replans": p.risk.replans if p.risk else 0,
            "reserve_margin_kwh": _r(p.reserve_margin_kwh),
            "forecast_fallback": ctx.forecast.fallback_used,
            "signal_fallback": ctx.signal_note is not None,
            "totals": {
                "cost_bdt": _r(tot.cost_bdt),
                "co2_kg": _r(tot.co2_kg),
                "grid_kwh": _r(tot.grid_kwh),
                "solar_used_kwh": _r(tot.solar_kwh),
                "generator_kwh": _r(tot.generator_kwh),
                "generator_h": _r(tot.generator_hours, 2),
                "diesel_l": _r(tot.diesel_litres),
            },
            "battery_start_soc_kwh": _r(ctx.assets.battery.initial_soc_kwh),
            "battery_end_soc_kwh": _r(p.schedule.battery_soc_kwh[-1]),
            "notes": [self.detime(n) for n in p.notes if not n.startswith(("RISK FAIL:", "RISK NOTE:"))],
        }

    def _binding(self, t: int) -> list[dict[str, Any]]:
        s, a, sig = self.ctx.plan.schedule, self.ctx.assets, self.ctx.signals
        b, gen, hv, ev = a.battery, a.generator, a.hvac, a.ev
        out: list[dict[str, Any]] = []
        if sig.outage[t]:
            out.append({"flag": "outage_grid_unavailable", "grid_import_kw": 0.0})
        elif s.grid_import_kw[t] >= a.grid.max_import_kw - TOL:
            out.append({"flag": "grid_at_import_limit", "max_import_kw": _r(a.grid.max_import_kw)})
        soc = s.battery_soc_kwh[t]
        floor = rules.soc_floor_kwh(b, self.ctx.plan.reserve_margin_kwh)[t]
        if soc >= b.soc_max_kwh - TOL:
            out.append({"flag": "battery_full", "soc_max_kwh": _r(b.soc_max_kwh)})
        elif soc <= floor + TOL:
            out.append({"flag": "battery_at_min_soc", "soc_floor_kwh": _r(floor)})
        if s.battery_charge_kw[t] >= b.max_charge_kw - TOL:
            out.append({"flag": "battery_charge_at_max", "max_charge_kw": _r(b.max_charge_kw)})
        if s.battery_discharge_kw[t] >= b.max_discharge_kw - TOL:
            out.append({"flag": "battery_discharge_at_max", "max_discharge_kw": _r(b.max_discharge_kw)})
        g = s.generator_kw[t]
        if g >= gen.rated_kw - TOL:
            out.append({"flag": "generator_at_rated", "rated_kw": _r(gen.rated_kw)})
        elif g > TOL and g <= gen.min_kw + TOL:
            out.append({"flag": "generator_at_min_load", "min_kw": _r(gen.min_kw)})
        if not rules.hvac_allowed(hv)[t]:
            out.append({"flag": "hvac_off_unoccupied"})
        elif hv.occupied[t] and abs(s.hvac_kw[t] - hv.min_kw_when_occupied) <= TOL:
            out.append(
                {"flag": "hvac_at_occupied_minimum", "min_kw_when_occupied": _r(hv.min_kw_when_occupied)}
            )
        if not ev.plugged_in[t]:
            out.append({"flag": "ev_not_plugged_in"})
        elif s.ev_charge_kw[t] >= ev.charger_kw - TOL:
            out.append({"flag": "ev_charge_at_max", "charger_kw": _r(ev.charger_kw)})
        if s.solar_curtailed_kw[t] > TOL:
            out.append({"flag": "solar_curtailed", "solar_curtailed_kw": _r(s.solar_curtailed_kw[t])})
        return out

    def get_slot(self, time: str) -> dict[str, Any]:
        t = self.slot_at(time)
        ctx = self.ctx
        sig, fc = ctx.signals, ctx.forecast
        row: dict[str, Any] = {"time": self.label(t), "end": self.label(t + 1)}
        row.update({f"{f}_kw": _r(self.flow(f)[t]) for f in FLOWS})
        row.update(
            {
                "load_total_kw": _r(self.load_total(t)),
                "battery_soc_start_kwh": _r(self.soc_start(t)),
                "battery_soc_end_kwh": _r(ctx.plan.schedule.battery_soc_kwh[t]),
                "tariff_bdt_per_kwh": sig.tariff_bdt_per_kwh[t],
                "tariff_period": self.tariff_period(t),
                "outage": sig.outage[t],
                "slot_cost_bdt": _r(ctx.plan.totals.slot_cost_bdt[t]),
                "solar_forecast_kw": {q: _r(getattr(fc.solar, f"{q}_kw")[t]) for q in ("p10", "p50", "p90")},
                "load_forecast_kw": {q: _r(getattr(fc.load, f"{q}_kw")[t]) for q in ("p10", "p50", "p90")},
                "binding": self._binding(t),
            }
        )
        return row

    def _rows(self, a: int, b: int, series: dict[str, list[float]]) -> tuple[str, list[dict[str, Any]]]:
        """Per-slot rows if the range is short, else hourly means (``_kw`` keys are means over the row)."""
        if b - a <= MAX_ROWS:
            rows = [{"time": self.label(t), **{k: _r(v[t]) for k, v in series.items()}} for t in range(a, b)]
            return "15min", rows
        per_hour = max(1, 60 // self.step)
        rows = []
        for u in range(a, b, per_hour):
            w = range(u, min(b, u + per_hour))
            rows.append(
                {
                    "time": self.label(u),
                    "end": self.label(w[-1] + 1),
                    **{
                        k: _r(v[w[-1]] if k.endswith("_end_kwh") else sum(v[i] for i in w) / len(w))
                        for k, v in series.items()
                    },
                }
            )
        return "1h_mean", rows

    def get_slot_range(self, start: str, end: str) -> dict[str, Any]:
        a, b = self.span(start, end)
        s, tot = self.ctx.plan.schedule, self.ctx.plan.totals
        soc = s.battery_soc_kwh[a:b]
        series = {
            "grid_kw": s.grid_import_kw,
            "solar_used_kw": s.solar_used_kw,
            "charge_kw": s.battery_charge_kw,
            "discharge_kw": s.battery_discharge_kw,
            "generator_kw": s.generator_kw,
            "base_load_kw": s.base_load_kw,
            "hvac_kw": s.hvac_kw,
            "ev_kw": s.ev_charge_kw,
            "soc_end_kwh": s.battery_soc_kwh,
        }
        resolution, rows = self._rows(a, b, series)
        outage = [self.block(x + a, y + a) for x, y in self.blocks(self.ctx.signals.outage[a:b])]
        return {
            **self.block(a, b),
            "energy_kwh": {f: _r(sum(self.flow(f)[a:b]) * self.h) for f in FLOWS},
            "cost_bdt": _r(sum(tot.slot_cost_bdt[a:b])),
            "co2_kg": _r(sum(tot.slot_co2_kg[a:b])),
            "battery_soc_start_kwh": _r(self.soc_start(a)),
            "battery_soc_end_kwh": _r(soc[-1]),
            "battery_soc_min_kwh": _r(min(soc)),
            "battery_soc_max_kwh": _r(max(soc)),
            "outage_blocks": outage,
            "resolution": resolution,
            "rows": rows,
        }

    def find_slots(self, condition: str) -> dict[str, Any]:
        s, sig = self.ctx.plan.schedule, self.ctx.signals
        flows = {
            "generator_on": s.generator_kw,
            "battery_charging": s.battery_charge_kw,
            "battery_discharging": s.battery_discharge_kw,
            "grid_import": s.grid_import_kw,
            "solar_curtailed": s.solar_curtailed_kw,
            "ev_charging": s.ev_charge_kw,
        }
        periods = {"peak_tariff": "peak", "off_peak_tariff": "off_peak", "flat_tariff": "flat"}
        if condition in flows:
            series = flows[condition]
            mask = [v > TOL for v in series]
        elif condition == "outage":
            series, mask = None, list(sig.outage)
        else:
            series, mask = None, [self.tariff_period(t) == periods[condition] for t in range(self.n)]
        runs = self.blocks(mask)
        out_blocks = []
        for a, b in runs[:MAX_BLOCKS]:
            blk = self.block(a, b)
            if series is not None:
                vals = series[a:b]
                blk.update(
                    energy_kwh=_r(sum(vals) * self.h), avg_kw=_r(sum(vals) / len(vals)), max_kw=_r(max(vals))
                )
            elif condition != "outage":
                blk["tariff_bdt_per_kwh"] = sig.tariff_bdt_per_kwh[a]
            out_blocks.append(blk)
        result: dict[str, Any] = {
            "condition": condition,
            "block_count": len(runs),
            "total_h": _r(sum(mask) * self.h, 2),
            "blocks": out_blocks,
        }
        if series is not None:
            result["total_energy_kwh"] = _r(sum(v for v, m in zip(series, mask, strict=True) if m) * self.h)
        if len(runs) > MAX_BLOCKS:
            result["blocks_truncated"] = True
        return result

    def get_forecast(self, start: str, end: str) -> dict[str, Any]:
        a, b = self.span(start, end)
        fc = self.ctx.forecast
        series = {
            f"{name}_{q}_kw": getattr(getattr(fc, name), f"{q}_kw")
            for name in ("solar", "load")
            for q in ("p10", "p50", "p90")
        }
        resolution, rows = self._rows(a, b, series)
        return {
            **self.block(a, b),
            "load_meaning": "inflexible base load (excludes HVAC and EV charging)",
            "energy_p50_kwh": {
                "solar": _r(sum(fc.solar.p50_kw[a:b]) * self.h),
                "load": _r(sum(fc.load.p50_kw[a:b]) * self.h),
            },
            "fallback_used": fc.fallback_used,
            "resolution": resolution,
            "rows": rows,
        }

    def get_signals(self) -> dict[str, Any]:
        sig = self.ctx.signals
        tariff_blocks = []
        a = 0
        for t in range(1, self.n + 1):
            if t == self.n or sig.tariff_bdt_per_kwh[t] != sig.tariff_bdt_per_kwh[a]:
                tariff_blocks.append(
                    {
                        **self.block(a, t),
                        "tariff_period": self.tariff_period(a),
                        "tariff_bdt_per_kwh": sig.tariff_bdt_per_kwh[a],
                    }
                )
                a = t

        def const(values: list[float], key: str) -> dict[str, Any]:
            lo, hi = min(values), max(values)
            return {key: lo} if hi - lo <= 1e-12 else {f"{key}_min": lo, f"{key}_max": hi}

        return {
            "tariff_blocks": tariff_blocks,
            "outage_blocks": [self.block(x, y) for x, y in self.blocks(list(sig.outage))],
            **const(sig.grid_co2_kg_per_kwh, "grid_co2_kg_per_kwh"),
            **const(sig.diesel_price_bdt_per_l, "diesel_price_bdt_per_l"),
            **const(sig.diesel_co2_kg_per_l, "diesel_co2_kg_per_l"),
            "signal_fallback": self.ctx.signal_note is not None,
        }

    def get_assets(self) -> dict[str, Any]:
        a = self.ctx.assets
        b, g, hv, ev = a.battery, a.generator, a.hvac, a.ev
        req = rules.ev_required_slots(ev)
        return {
            "battery": {
                "capacity_kwh": _r(b.capacity_kwh),
                "soc_min_kwh": _r(b.soc_min_kwh),
                "soc_max_kwh": _r(b.soc_max_kwh),
                "initial_soc_kwh": _r(b.initial_soc_kwh),
                "max_charge_kw": _r(b.max_charge_kw),
                "max_discharge_kw": _r(b.max_discharge_kw),
                "round_trip_efficiency_pct": _r(b.round_trip_efficiency * 100),
                "end_of_day_rule": "battery must end the day at or above initial_soc_kwh",
            },
            "generator": {
                "rated_kw": _r(g.rated_kw),
                "min_kw": _r(g.min_kw),
                "fuel_l_per_kwh": g.fuel_l_per_kwh,
                "rule": "off, or between min_kw and rated_kw",
            },
            "grid": {"max_import_kw": _r(a.grid.max_import_kw), "export": "not allowed"},
            "hvac": {
                "rated_kw": _r(hv.rated_kw),
                "min_kw_when_occupied": _r(hv.min_kw_when_occupied),
                "comfort_min_c": hv.comfort_min_c,
                "comfort_max_c": hv.comfort_max_c,
                "occupied_blocks": [self.block(x, y) for x, y in self.blocks(list(hv.occupied))],
            },
            "ev_fleet": {
                "charger_kw": _r(ev.charger_kw),
                "energy_required_kwh": _r(ev.energy_required_kwh),
                "plugged_in_blocks": [self.block(x, y) for x, y in self.blocks(list(ev.plugged_in))],
                "energy_due_by": self.label(req[-1] + 1) if req else None,
            },
        }

    def _times_of(self, slots: str) -> list[dict[str, Any]]:
        out = []
        for part in (slots or "").split(","):
            part = part.strip()
            if not part or part == "none":
                continue
            lo, _, hi = part.partition("-")
            try:
                a, b = int(lo), int(hi or lo)
            except ValueError:
                continue
            if 0 <= a <= b < self.n:
                out.append(self.block(a, b + 1))
        return out

    def get_risk_log(self) -> dict[str, Any]:
        risk = self.ctx.plan.risk
        findings: list[dict[str, Any]] = []
        for line in risk.reasons if risk else []:
            f = parse_reason(line)
            if f is None:
                findings.append({"text": self.detime(line)})
                continue
            old, _, new = f.get("reserve_kwh", "").partition("->")
            item: dict[str, Any] = {
                "kind": f["kind"],
                "check": f.get("check"),
                "scenario": f.get("scenario"),
                "times": self._times_of(f.get("slots", "")),
                "limit": f.get("limit"),
                "at_risk": f.get("at_risk"),
                "fixable": f.get("fixable"),
                "detail": self.detime(f.get("detail", "")),
            }
            for key, raw in (
                ("shortfall_kwh", f.get("shortfall_kwh")),
                ("reserve_from_kwh", old),
                ("reserve_to_kwh", new),
            ):
                try:
                    item[key] = _r(float(raw))
                except (TypeError, ValueError):
                    pass
            findings.append(item)
        return {
            "ok": risk.ok if risk else None,
            "replans": risk.replans if risk else 0,
            "reserve_margin_kwh": _r(self.ctx.plan.reserve_margin_kwh),
            "stress_scenario": self.ctx.risk_scenario,
            "finding_count": len(findings),
            "findings": findings,
        }

    def get_forecast_note(self) -> dict[str, Any]:
        fc, note = self.ctx.forecast, self.ctx.signal_note
        return {
            "forecast": {
                "fallback_used": fc.fallback_used,
                "note": parse_forecast_note(fc.note) or ({"text": fc.note} if fc.note else None),
            },
            "signal": {
                "fallback_used": note is not None,
                "note": parse_signal_note(note) or ({"text": note} if note else None),
            },
        }

    def get_backtest_totals(self) -> dict[str, Any]:
        bt = self.ctx.backtest
        if not bt:
            return {"available": False, "reason": "no backtest report loaded; do not quote backtest numbers"}
        names: dict[str, str] = bt.get("headline", {}).get("baseline_names", {})
        by_weight: dict[str, Any] = bt.get("summary", {}).get("by_weight", {})
        if not by_weight:
            return {"available": False, "reason": "backtest report has no summary"}
        w = self.ctx.plan.weight
        key = min(by_weight, key=lambda k: abs(float(k) - w))
        summary = by_weight[key]

        def direction(pct: float, what: str) -> str:
            return f"{what} {'lower' if pct >= 0 else 'higher'} than the baseline"

        year = {}
        for base, vs in summary.get("vs", {}).items():
            label = names.get(base, base)
            year[label] = {
                "cost_saved_pct": _r(vs["cost_saved_pct"]),
                "cost_saved_bdt": _r(vs["cost_saved_bdt"]),
                "cost_result": direction(vs["cost_saved_pct"], "cost"),
                "co2_saved_pct": _r(vs["co2_saved_pct"]),
                "co2_saved_kg": _r(vs["co2_saved_kg"]),
                "co2_result": direction(vs["co2_saved_pct"], "CO2"),
                "days_cost_worse": vs.get("days_cost_worse"),
            }
        days = bt.get("summary", {}).get("days", {})
        cfg = bt.get("config", {})
        date = self.ctx.plan.start.date().isoformat()
        day_rec = next((d for d in bt.get("days", []) if d.get("date") == date), None)
        rows = [m for m in (day_rec or {}).get("methods", []) if m.get("method") == "optimiser"]
        if not rows:
            reason = f"date {date} not in backtest; do not quote it"
            day: dict[str, Any] = {"available": False, "reason": reason}
        else:
            row = min(rows, key=lambda m: abs(float(m.get("weight") or 0) - w))
            vs_day = {}
            for base in names:
                cost = row.get(f"cost_saved_vs_{base}_pct")
                co2 = row.get(f"co2_saved_vs_{base}_pct")
                if cost is None or co2 is None:
                    continue
                vs_day[names[base]] = {
                    "cost_saved_pct": _r(cost),
                    "cost_result": direction(cost, "cost"),
                    "co2_saved_pct": _r(co2),
                    "co2_result": direction(co2, "CO2"),
                }
            day = {
                "available": True,
                "date": date,
                "weight": row.get("weight"),
                "settled_cost_bdt": _r(row["adjusted_cost_bdt"]),
                "settled_co2_kg": _r(row["adjusted_co2_kg"]),
                "settled_meaning": "plan settled against the day's actual solar and load (not the plan)",
                "vs_baselines": vs_day,
            }
        return {
            "available": True,
            "period": {"from": cfg.get("first_day"), "to": cfg.get("last_day")},
            "days_included": days.get("included"),
            "weight": float(key),
            "year_vs_baselines": year,
            "day": day,
            "caveat": "synthetic load, simulated PV, synthetic outages; forecasts used observed weather "
            "(tier-0); baselines run on actual solar and load",
        }
