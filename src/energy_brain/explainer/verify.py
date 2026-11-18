"""Traceability check (#19, PRD section 8: 0 untraceable numbers).

Every number in an Explainer answer must appear in a tool output of the same conversation.

**Extracted from the answer:** plain numbers (``1,234.5``), numbers with a unit (``500 kW``, ``4.7 %``,
``BDT 152,703``), clock times (``19:00``, ``7 PM``, ``7:30 p.m.``) and dates (``2024-07-02``,
``2 July 2024``).
Not extracted: digits inside identifiers (``P10``, ``P90``, ``CO2``), markdown list markers (``1.`` at the
start of a line), and numbers written as words.

**Facts:** every numeric leaf of every tool result, with the unit read from its JSON key (``cost_bdt`` ->
BDT; a nested key inherits its parent's unit, e.g. ``energy_kwh.generator``), plus the numbers, times and
dates inside string values (e.g. a Risk ``detail`` such as ``grid +34.6 kWh``).

**Match rule.** An answer number ``x`` written with ``d`` decimals matches fact ``v`` when
``| |x| - |v| | <= 0.5 * 10**-d`` (rounding to the shown precision only: ``152,703`` matches ``152702.7``,
``153,000`` does not; the sign is wording, e.g. "0.3 % higher" for ``co2_saved_pct = -0.3``). If the answer
gives a unit, the fact must have the same unit (so ``732.9 kW`` does not match ``732.9 kWh``, and ``0.7 MWh``
never matches anything because no tool returns MWh). A unitless answer number may match any fact. Times match
``HH:MM`` facts (``00:00`` and ``24:00`` are the same instant), dates match date facts. Numbers that appear
only in the operator's question never count, except in a refusal (``allow`` texts).

**Scope.** The check proves a number was *returned by a tool*, not that it is true: a number inside upstream
free text (a planner note, a Risk detail) counts. Prompt injection is handled elsewhere (data-only prompt,
read-only tools, unknown tools never executed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

#: JSON key suffix -> unit, longest first.
KEY_UNITS = (
    ("_bdt_per_kwh", "bdt/kwh"),
    ("_kg_per_kwh", "kg/kwh"),
    ("_bdt_per_l", "bdt/l"),
    ("_kg_per_l", "kg/l"),
    ("_l_per_kwh", "l/kwh"),
    ("_kwh", "kwh"),
    ("_kw", "kw"),
    ("_bdt", "bdt"),
    ("_kg", "kg"),
    ("_pct", "pct"),
    ("_min", "min"),
    ("_l", "l"),
    ("_h", "h"),
    ("_c", "c"),
)

#: Unit spellings in answer text -> unit (longest / most specific first).
_UNIT_WORDS = (
    (r"(?:BDT|Tk|taka)\s*/\s*kWh|(?:BDT|Tk|taka)\s+per\s+kWh", "bdt/kwh"),
    (r"kg(?:\s*CO2|\s*CO₂)?\s*/\s*kWh|kg(?:\s*CO2)?\s+per\s+kWh", "kg/kwh"),
    (r"(?:BDT|Tk|taka)\s*/\s*(?:L|litre|liter)\b|(?:BDT|Tk|taka)\s+per\s+(?:litre|liter)", "bdt/l"),
    (r"kg(?:\s*CO2)?\s*/\s*(?:L|litre|liter)\b|kg(?:\s*CO2)?\s+per\s+(?:litre|liter)", "kg/l"),
    (r"(?:L|litres?|liters?)\s*/\s*kWh|(?:litres?|liters?)\s+per\s+kWh", "l/kwh"),
    (r"MWh", "mwh"),
    (r"kWh", "kwh"),
    (r"MW\b", "mw"),
    (r"kW\b", "kw"),
    (r"%|percent\b|per\s*cent\b", "pct"),
    (r"BDT\b|Tk\b|taka\b|৳", "bdt"),
    (r"tonnes?\b|tons?\b|t\s*CO2\b", "t"),
    (r"kg\b|kilograms?\b", "kg"),
    (r"litres?\b|liters?\b|L\b", "l"),
    (r"hours?\b|hrs?\b|h\b", "h"),
    (r"minutes?\b|mins?\b", "min"),
    (r"°\s*C\b|degrees?\s+C(?:elsius)?\b", "c"),
)
_UNIT_RE = re.compile("|".join(f"(?P<u{i}>{p})" for i, (p, _) in enumerate(_UNIT_WORDS)), re.IGNORECASE)

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    )
}
_MON = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"

_DAY = r"(?:st|nd|rd|th)?"
_TOKEN_RE = re.compile(
    r"(?P<iso>\b\d{4}-\d{2}-\d{2}\b)"
    rf"|(?P<dmy>\b(?P<d1>\d{{1,2}}){_DAY}\s+{_MON.replace('mon', 'mon1')}(?:,?\s+(?P<y1>\d{{4}}))?)"
    rf"|(?P<mdy>\b{_MON.replace('mon', 'mon2')}\s+(?P<d2>\d{{1,2}}){_DAY}\b(?:,?\s+(?P<y2>\d{{4}}))?)"
    r"|(?P<clock>\b(?P<hh>[01]?\d|2[0-4]):(?P<mm>[0-5]\d)(?:\s*(?P<ap1>[ap])\.?\s*m\b\.?)?)"
    r"|(?P<ampm>\b(?P<h12>1[0-2]|0?[1-9])\s*(?P<ap2>[ap])\.?\s*m\b\.?)"
    r"|(?P<num>(?<![A-Za-z_\d.,])(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?(?![\d]))",
    re.IGNORECASE,
)
_PREFIX_BDT = re.compile(r"(?:BDT|Tk|৳)\s*$", re.IGNORECASE)
#: Gap between a number and its unit: any whitespace except a newline (incl. U+00A0, U+202F), or a hyphen.
_UNIT_GAP = re.compile(r"^[^\S\n]*-?[^\S\n]*")
_LIST_MARKER = re.compile(r"^(\s*)\d+[.)](\s+)", re.MULTILINE)


@dataclass(frozen=True)
class Token:
    """A number, time or date found in text. ``value`` is a float for numbers, else a normalised string."""

    text: str
    kind: str  # "number" | "time" | "date"
    value: float | str
    decimals: int = 0
    unit: str | None = None


@dataclass(frozen=True)
class Fact:
    """A value from a tool output: ``tool`` name, ``call`` index in the conversation, JSON ``field`` path."""

    kind: str
    value: float | str
    unit: str | None
    tool: str
    field: str
    call: int


class NumberSource(BaseModel):
    """Where one number of the answer came from (shown by the dashboard)."""

    text: str
    kind: str
    tool: str
    field: str
    call: int


class VerifyResult(BaseModel):
    ok: bool
    sources: list[NumberSource]
    unverified: list[str]


def _unit_of(word: str) -> str:
    m = _UNIT_RE.fullmatch(word.strip())
    if m is None:
        return word.lower()
    return next(_UNIT_WORDS[int(k[1:])][1] for k, v in m.groupdict().items() if v is not None)


def _time(h: int, m: int, ap: str | None) -> str:
    if ap:
        h = h % 12 + (12 if ap.lower() == "p" else 0)
    return f"{h:02d}:{m:02d}"


def _date(day: str, mon: str, year: str | None) -> str:
    md = f"{_MONTHS[mon.lower()[:3]]:02d}-{int(day):02d}"
    return f"{year}-{md}" if year else md


def extract_tokens(text: str) -> list[Token]:
    """All numbers, times and dates in ``text`` (see module docstring for what is skipped)."""
    text = _LIST_MARKER.sub(r"\1\2", text or "")
    out: list[Token] = []
    for m in _TOKEN_RE.finditer(text):
        g = m.groupdict()
        if g["iso"]:
            out.append(Token(m.group(0), "date", g["iso"]))
        elif g["dmy"]:
            out.append(Token(m.group(0), "date", _date(g["d1"], g["mon1"], g["y1"])))
        elif g["mdy"]:
            out.append(Token(m.group(0), "date", _date(g["d2"], g["mon2"], g["y2"])))
        elif g["clock"]:
            out.append(Token(m.group(0), "time", _time(int(g["hh"]), int(g["mm"]), g["ap1"])))
        elif g["ampm"]:
            out.append(Token(m.group(0), "time", _time(int(g["h12"]), 0, g["ap2"])))
        else:
            frac = g["frac"] or ""
            value = float(g["int"].replace(",", "") + ("." + frac if frac else ""))
            um = _UNIT_RE.match(_UNIT_GAP.sub("", text[m.end() :], count=1))
            unit = _unit_of(um.group(0)) if um else None
            label = f"{m.group(0)} {um.group(0).strip()}" if um else m.group(0)
            if unit is None and _PREFIX_BDT.search(text[: m.start()]):
                unit = "bdt"
            out.append(Token(label, "number", value, len(frac), unit))
    return out


#: Unit segments recognised inside a key when no suffix matches (``min_kw_when_occupied`` -> kW).
_SEGMENT_UNITS = {u: u for u in ("kw", "kwh", "bdt", "kg", "pct", "l", "h", "c")}


def _key_unit(key: str) -> str | None:
    """Unit of a JSON key: by suffix (``_kwh``, ``_bdt_per_kwh``...), else the last unit segment."""
    k = key.lower()
    unit = next((u for suffix, u in KEY_UNITS if k.endswith(suffix)), None)
    if unit is None:
        unit = next((_SEGMENT_UNITS[p] for p in reversed(k.split("_")) if p in _SEGMENT_UNITS), None)
    return unit


def collect_facts(calls: list[tuple[str, Any]]) -> list[Fact]:
    """Facts from ``[(tool_name, result_json), ...]`` in call order."""
    facts: list[Fact] = []

    def walk(node: Any, path: str, unit: str | None, tool: str, call: int) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k), _key_unit(str(k)) or unit, tool, call)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", unit, tool, call)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, int | float):
            facts.append(Fact("number", float(node), unit, tool, path, call))
        elif isinstance(node, str):
            for tok in extract_tokens(node):
                facts.append(
                    Fact(tok.kind, tok.value, tok.unit if tok.kind == "number" else None, tool, path, call)
                )

    for i, (name, result) in enumerate(calls):
        walk(result, "", None, name, i)
    return facts


def _same_time(a: str, b: str) -> bool:
    norm = {"24:00": "00:00"}
    return norm.get(a, a) == norm.get(b, b)


def _same_date(answer: str, fact: str) -> bool:
    return answer == fact or (len(answer) == 5 and fact.endswith(answer))


def _match(tok: Token, facts: list[Fact]) -> Fact | None:
    if tok.kind == "time":
        return next((f for f in facts if f.kind == "time" and _same_time(tok.value, f.value)), None)
    if tok.kind == "date":
        return next((f for f in facts if f.kind == "date" and _same_date(tok.value, f.value)), None)
    tol = 0.5 * 10 ** (-tok.decimals) + 1e-9
    best: tuple[float, Fact] | None = None
    for f in facts:
        if f.kind != "number" or (tok.unit is not None and f.unit != tok.unit):
            continue
        diff = abs(abs(tok.value) - abs(f.value))
        if diff <= tol and (best is None or diff < best[0]):
            best = (diff, f)
    return best[1] if best else None


def verify_answer(text: str, calls: list[tuple[str, Any]], allow: list[str] | None = None) -> VerifyResult:
    """Check every number in ``text`` against tool outputs ``calls`` (and ``allow`` texts, for refusals)."""
    facts = collect_facts(calls)
    for extra in allow or []:
        facts += [
            Fact(t.kind, t.value, t.unit if t.kind == "number" else None, "question", "text", -1)
            for t in extract_tokens(extra)
        ]
    sources: list[NumberSource] = []
    missing: list[str] = []
    for tok in extract_tokens(text):
        fact = _match(tok, facts)
        if fact is None:
            missing.append(tok.text)
        else:
            sources.append(
                NumberSource(text=tok.text, kind=tok.kind, tool=fact.tool, field=fact.field, call=fact.call)
            )
    return VerifyResult(ok=not missing, sources=sources, unverified=missing)
