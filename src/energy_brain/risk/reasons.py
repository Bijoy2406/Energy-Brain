"""Stable one-line reason format of the Risk agent (#18), same key=value style as the #14/#15 notes.

Every string in ``RiskReport.reasons`` is one line::

    RISK FAIL: check=<code>; scenario=solar_<q>,load_<q>; slots=<ranges|none>; shortfall_kwh=<x.x>;
    limit=<energy|power|none>; at_risk=<comfort,ev,base_load|none>; reserve_kwh=<old>-><new>;
    fixable=<yes|no>; detail=<free text>

(one line in practice). ``RISK FAIL`` lines make ``ok`` False; ``RISK NOTE`` lines are advisory and never do.
``detail`` is free text (``;`` replaced by ``,``) and always last. The Explainer (#19) may quote these lines;
use :func:`parse_reason` instead of string matching.
"""

from __future__ import annotations

FAIL_PREFIX = "RISK FAIL:"
NOTE_PREFIX = "RISK NOTE:"

#: Field order of every reason line (``detail`` is always last).
REASON_KEYS = (
    "check",
    "scenario",
    "slots",
    "shortfall_kwh",
    "limit",
    "at_risk",
    "reserve_kwh",
    "fixable",
    "detail",
)

#: Check codes. ``unserved_energy`` and ``unsafe_plan`` are failures; the others are notes.
CHECK_UNSERVED = "unserved_energy"
CHECK_UNSAFE = "unsafe_plan"
CHECK_END_SOC = "end_soc_short"
CHECK_ADVISORY = "advisory_unserved"


def format_reason(fields: dict[str, object], *, fail: bool = True) -> str:
    """Build one reason line from ``fields`` (keys from ``REASON_KEYS``) in the stable order."""
    parts = []
    for key in REASON_KEYS:
        value = str(fields.get(key, "")).replace(";", ",").replace("\n", " ").strip()
        parts.append(f"{key}={value}")
    return f"{FAIL_PREFIX if fail else NOTE_PREFIX} " + "; ".join(parts)


def parse_reason(reason: str | None) -> dict[str, str] | None:
    """Inverse of :func:`format_reason`; adds ``kind`` = ``fail`` / ``note``. None if not a risk reason."""
    if not reason:
        return None
    if reason.startswith(FAIL_PREFIX):
        prefix, kind = FAIL_PREFIX, "fail"
    elif reason.startswith(NOTE_PREFIX):
        prefix, kind = NOTE_PREFIX, "note"
    else:
        return None
    out: dict[str, str] = {"kind": kind}
    head, sep, detail = reason[len(prefix) :].strip().partition("; detail=")
    for part in head.split("; "):
        key, _, value = part.partition("=")
        if key.strip():
            out[key.strip()] = value.strip()
    if sep:
        out["detail"] = detail.strip()
    return out


def slot_ranges(slots: list[int]) -> str:
    """'3-5,9' style summary of sorted slot indices; 'none' when empty (no spaces, safe inside a field)."""
    if not slots:
        return "none"
    parts, a, prev = [], slots[0], slots[0]
    for s in [*slots[1:], None]:
        if s is not None and s == prev + 1:
            prev = s
            continue
        parts.append(f"{a}" if a == prev else f"{a}-{prev}")
        if s is not None:
            a = prev = s
    return ",".join(parts)
