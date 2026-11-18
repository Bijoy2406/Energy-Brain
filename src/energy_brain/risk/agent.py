"""P10 Risk agent (#18): implements ``energy_brain.interfaces.RiskAgent``.

``P10RiskAgent(solar="p10", load="p50", advisory_load="p90").check(plan, forecast, signals, assets)``:

1. An unsafe plan (``planner.is_safe`` False) fails at once with ``check=unsafe_plan``, quoting the Planner's
   ``NOT SAFE TO EXECUTE`` note, and keeps the plan's reserve (``fixable=no``): more reserve cannot repair a
   day that is infeasible even at P50.
2. Gating scenario (default solar P10, load P50, issue #18 / FR-06): ``risk.replay.stress_replay`` runs the
   fixed plan with a reactive operator. Every contiguous block of slots with unserved energy is one
   ``RISK FAIL`` reason (``check=unserved_energy``) and makes ``ok`` False.
3. Reserve margin: ``plan.reserve_margin_kwh`` + the largest fixable block's unserved kWh / eta (stored
   energy the next plan must hold back), capped at ``soc_max - soc_min``. A block is fixable only if it is
   purely ``energy``-limited and the battery energy any dispatch needs over its run fits in
   ``soc_max - soc_min``. ``power`` (battery and generator at full power) and ``capacity`` (the run needs
   more than the whole battery window) blocks add nothing (``fixable=no``). The margin never decreases, so the
   orchestrator's re-plan loop is monotone.
4. Notes (``RISK NOTE``, never change ``ok``): the advisory replay at ``advisory_load`` (default P90) when it
   leaves energy unserved, and a battery that ends the stressed day below its starting charge.

Pure function of its inputs (nothing is modified or cached), so #22 can call it with an "actual" forecast.
Never raises for a bad plan: an unexpected error becomes a ``check=replay_error`` failure.
"""

from __future__ import annotations

import logging

from energy_brain.planner import UNSAFE_PREFIX, is_safe
from energy_brain.risk.reasons import (
    CHECK_ADVISORY,
    CHECK_END_SOC,
    CHECK_ERROR,
    CHECK_UNSAFE,
    CHECK_UNSERVED,
    format_reason,
    slot_ranges,
)
from energy_brain.risk.replay import QUANTILES, TOL, StressReplay, stress_replay
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, RiskReport, SignalTable
from energy_brain.timeaxis import slot_hours

logger = logging.getLogger(__name__)


def _blocks(slots: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for t in slots:
        if out and t == out[-1][-1] + 1:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def _fmt_kwh(x: float) -> str:
    return f"{x:.1f}"


class P10RiskAgent:
    """Re-check a plan at pessimistic solar; ask for a larger battery reserve when it fails."""

    def __init__(self, solar: str = "p10", load: str = "p50", advisory_load: str | None = "p90") -> None:
        for name, q in (("solar", solar), ("load", load), ("advisory_load", advisory_load)):
            if q is not None and q not in QUANTILES:
                raise ValueError(f"{name} must be one of {QUANTILES}, got {q!r}")
        self.solar = solar
        self.load = load
        self.advisory_load = advisory_load if advisory_load != load else None

    # ------------------------------------------------------------------ protocol

    def check(
        self, plan: PlanResult, forecast: Forecast, signals: SignalTable, assets: AssetConstraints
    ) -> RiskReport:
        old = plan.reserve_margin_kwh
        scenario = f"solar_{self.solar},load_{self.load}"
        if not is_safe(plan):
            note = plan.notes[0] if plan.notes else f"{UNSAFE_PREFIX}: status={plan.status}"
            reason = format_reason(
                {
                    "check": CHECK_UNSAFE,
                    "scenario": scenario,
                    "slots": "none",
                    "shortfall_kwh": "0.0",
                    "limit": "none",
                    "at_risk": "none",
                    "reserve_kwh": f"{old:g}->{old:g}",
                    "fixable": "no",
                    "detail": f"planner marked the plan unsafe, a larger reserve cannot fix it: {note}",
                }
            )
            logger.warning("risk: plan is unsafe, not replayed")
            return RiskReport(ok=False, reasons=[reason], reserve_margin_kwh=old)
        try:
            return self._check(plan, forecast, signals, assets)
        except Exception as exc:  # never raise for a bad plan
            logger.exception("risk replay failed")
            reason = format_reason(
                {
                    "check": CHECK_ERROR,
                    "scenario": scenario,
                    "slots": "none",
                    "shortfall_kwh": "0.0",
                    "limit": "none",
                    "at_risk": "none",
                    "reserve_kwh": f"{old:g}->{old:g}",
                    "fixable": "no",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            return RiskReport(ok=False, reasons=[reason], reserve_margin_kwh=old)

    # ------------------------------------------------------------------ helpers

    def replay(
        self, plan: PlanResult, forecast: Forecast, signals: SignalTable, assets: AssetConstraints
    ) -> StressReplay:
        """The gating-scenario replay behind :meth:`check` (for tests, the Explainer and #22)."""
        return stress_replay(plan, forecast, signals, assets, self.solar, self.load)

    def _check(
        self, plan: PlanResult, forecast: Forecast, signals: SignalTable, assets: AssetConstraints
    ) -> RiskReport:
        b = assets.battery
        old = plan.reserve_margin_kwh
        rp = self.replay(plan, forecast, signals, assets)
        blocks = self._summarise(rp, signals, assets)

        need = max((blk["need_kwh"] for blk in blocks if blk["fixable"]), default=0.0)
        cap = max(old, b.soc_max_kwh - b.soc_min_kwh)
        new = min(cap, old + need) if need > TOL else old
        grows = new > old + TOL

        reasons = [
            format_reason(
                {
                    "check": CHECK_UNSERVED,
                    "scenario": rp.scenario,
                    "slots": slot_ranges(blk["slots"]),
                    "shortfall_kwh": _fmt_kwh(blk["kwh"]),
                    "limit": blk["limit"],
                    "at_risk": ",".join(blk["at_risk"]),
                    "reserve_kwh": f"{old:g}->{new:g}",
                    "fixable": "yes" if grows and blk["fixable"] else "no",
                    "detail": blk["detail"]
                    + ("" if new < cap - TOL or not need else "; reserve capped at soc_max - soc_min"),
                }
            )
            for blk in blocks
        ]
        ok = not reasons

        if rp.soc_kwh and rp.soc_kwh[-1] < b.initial_soc_kwh - 1e-3:
            reasons.append(
                format_reason(
                    {
                        "check": CHECK_END_SOC,
                        "scenario": rp.scenario,
                        "slots": str(len(rp.soc_kwh) - 1),
                        "shortfall_kwh": _fmt_kwh(b.initial_soc_kwh - rp.soc_kwh[-1]),
                        "limit": "none",
                        "at_risk": "none",
                        "reserve_kwh": f"{old:g}->{new:g}",
                        "fixable": "no",
                        "detail": (
                            f"note only, does not fail the plan: battery ends the stressed day at "
                            f"{rp.soc_kwh[-1]:.1f} kWh, below its starting {b.initial_soc_kwh:.1f} kWh "
                            "(planner end-of-day rule, not a safety limit)"
                        ),
                    },
                    fail=False,
                )
            )

        if self.advisory_load is not None:
            adv = stress_replay(plan, forecast, signals, assets, self.solar, self.advisory_load)
            adv_blocks = self._summarise(adv, signals, assets)
            if adv_blocks:
                kinds = sorted({k for blk in adv_blocks for k in blk["limit"].split(",")})
                risk = sorted({r for blk in adv_blocks for r in blk["at_risk"]})
                reasons.append(
                    format_reason(
                        {
                            "check": CHECK_ADVISORY,
                            "scenario": adv.scenario,
                            "slots": slot_ranges(adv.short_slots()),
                            "shortfall_kwh": _fmt_kwh(adv.unserved_kwh),
                            "limit": ",".join(kinds),
                            "at_risk": ",".join(risk),
                            "reserve_kwh": f"{old:g}->{new:g}",
                            "fixable": "no",
                            "detail": (
                                "advisory only, does not fail the plan and asks for no reserve: at load "
                                f"{self.advisory_load.upper()} {adv.unserved_kwh:.1f} kWh would be unserved "
                                f"in {len(adv_blocks)} block(s)"
                            ),
                        },
                        fail=False,
                    )
                )

        logger.info(
            "risk %s: ok=%s unserved=%.1f kWh reserve %.1f -> %.1f kWh",
            rp.scenario,
            ok,
            rp.unserved_kwh,
            old,
            new,
        )
        return RiskReport(ok=ok, reasons=reasons, reserve_margin_kwh=new)

    @staticmethod
    def _summarise(rp: StressReplay, signals: SignalTable, assets: AssetConstraints) -> list[dict]:
        """One dict per contiguous block of short slots (see the module docstring for ``limit``/fixable).

        Energy-limited shortfall in a run whose physical battery need (``StressReplay.min_battery_kw``, any
        dispatch) exceeds ``soc_max - soc_min`` is ``capacity``: no reserve can fix it.
        """
        h, b = slot_hours(), assets.battery
        out = []
        for slots in _blocks(rp.short_slots()):
            kwh = sum(rp.unserved_kw[t] for t in slots) * h
            energy_kwh = sum(rp.unserved_kw[t] for t in slots if rp.limit[t] == "energy") * h
            # Run of slots before and through the block where any plan must discharge the battery.
            r0 = slots[0]
            while r0 > 0 and rp.min_battery_kw[r0 - 1] > TOL:
                r0 -= 1
            must_kwh = sum(rp.min_battery_kw[r0 : slots[-1] + 1]) * h / b.one_way_efficiency
            window = b.soc_max_kwh - b.soc_min_kwh
            need_kwh = energy_kwh / b.one_way_efficiency
            capacity = energy_kwh > TOL and must_kwh > window + TOL
            limits = sorted(
                {
                    ("capacity" if capacity else "energy") if rp.limit[t] == "energy" else rp.limit[t]
                    for t in slots
                }
            )
            fixable = limits == ["energy"]
            at_risk = sorted({r for t in slots for r in rp.at_risk[t]})
            n_out = sum(1 for t in slots if signals.outage[t])
            grid = (
                "grid unavailable (outage)"
                if n_out == len(slots)
                else f"grid at its import limit in {len(slots) - n_out} slot(s)"
                + (f", outage in {n_out}" if n_out else "")
            )
            used = {
                "grid": sum(rp.grid_extra_kw[t] for t in slots) * h,
                "battery": sum(rp.discharge_extra_kw[t] for t in slots) * h,
                "generator": sum(rp.generator_extra_kw[t] for t in slots) * h,
                "charge cut": sum(rp.charge_cut_kw[t] for t in slots) * h,
            }
            recourse = ", ".join(f"{k} +{v:.1f} kWh" for k, v in used.items() if v > TOL) or "none available"
            detail = f"{kwh:.1f} kWh unserved, {grid}; operator recourse used: {recourse}"
            if capacity:
                detail += (
                    f"; slots {r0}-{slots[-1]} need at least {must_kwh:.1f} kWh from the battery with the "
                    f"generator at full power, more than its {window:.1f} kWh window: more reserve cannot "
                    "fix this"
                )
            if "power" in limits:
                detail += "; battery and generator at full power: more reserve cannot fix this"
            out.append(
                {
                    "slots": slots,
                    "kwh": kwh,
                    "need_kwh": need_kwh,
                    "fixable": fixable,
                    "limit": ",".join(limits),
                    "at_risk": at_risk,
                    "detail": detail,
                }
            )
        return out
