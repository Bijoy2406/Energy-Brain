"""MILP Planner agent (#17): implements ``energy_brain.interfaces.PlannerAgent``.

Flow of :meth:`MilpPlanner.plan`:

1. Reference schedule (``planner.reference``, no reserve) -> objective scales.
2. Up-front checks that can be diagnosed without a solver (EV energy due before departure vs charger
   capacity in that window).
3. Build (``planner.model``) and solve (``planner.solve``) the MILP with HiGHS, time limit ``TIME_LIMIT_S``.
4. Optimal / feasible: extract the schedule, run ``planner.checks.find_violations`` and
   ``Schedule.check_energy_balance``. Totals always come from ``accounting.compute_totals``.
5. Proven infeasible: re-solve in slack mode (unserved energy / EV shortfall, every other hard constraint
   kept) to locate the problem. Returned with ``status="infeasible"``.
6. Solver error / time-out without a solution: the rule-based reference schedule. If it passes every check:
   ``status="fallback"``; otherwise ``status="infeasible"``.

Unsafe results are machine-detectable: ``status == "infeasible"``, ``fallback_used is True`` and
``notes[0]`` starts with :data:`UNSAFE_PREFIX`. Hard constraints are never relaxed to make a plan "work".
"""

from __future__ import annotations

import time

from energy_brain.accounting import compute_totals
from energy_brain.planner import rules
from energy_brain.planner.checks import TOL, find_violations
from energy_brain.planner.fixes import SlotFixes
from energy_brain.planner.model import Scales, build_model
from energy_brain.planner.reference import rule_based_schedule
from energy_brain.planner.solve import TIME_LIMIT_S, solve_model
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, PlanStatus, Schedule, SignalTable
from energy_brain.timeaxis import slot_hours, slot_minutes

#: Every unsafe / infeasible PlanResult has ``notes[0]`` starting with this.
UNSAFE_PREFIX = "NOT SAFE TO EXECUTE"


def is_safe(plan: PlanResult) -> bool:
    """True unless the plan is flagged unsafe (infeasible).

    Explainer, Risk and API should refuse to present or act on an unsafe plan.
    """
    return plan.status != "infeasible" and not (plan.notes and plan.notes[0].startswith(UNSAFE_PREFIX))


def _ranges(slots: list[int]) -> str:
    """'3-5, 9' style summary of sorted slot indices."""
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
    return ", ".join(parts)


class MilpPlanner:
    """Deterministic MILP planner (Pyomo + HiGHS). See ``planner.model`` for the formulation.

    ``solve_seconds`` in the result is the wall time of the whole ``plan()`` call (model build, solve,
    extraction and checks), i.e. the latency the operator sees.
    """

    def __init__(self, time_limit_s: float = TIME_LIMIT_S) -> None:
        self.time_limit_s = time_limit_s

    def plan(
        self,
        forecast: Forecast,
        signals: SignalTable,
        assets: AssetConstraints,
        weight: float,
        reserve_margin_kwh: float = 0.0,
        fixes: SlotFixes | None = None,
    ) -> PlanResult:
        """Minimise ``w * cost + (1 - w) * CO2`` (normalised); never raises for an infeasible day.

        Raises ``ValueError`` only for invalid arguments (weight outside [0, 1], negative reserve,
        invalid slot fixes). Fixed decisions remain hard constraints during diagnosis and fallback
        checking; an infeasible set of otherwise valid fixes never raises.
        """
        if not 0.0 <= weight <= 1.0:
            raise ValueError(f"weight must be in [0, 1], got {weight}")
        if reserve_margin_kwh < 0:
            raise ValueError(f"reserve_margin_kwh must be >= 0, got {reserve_margin_kwh}")
        if fixes is not None:
            fixes.validate(assets)
        t0 = time.perf_counter()
        base = list(forecast.load.p50_kw)
        notes = self._context_notes(forecast, assets, reserve_margin_kwh)

        ref = rule_based_schedule(forecast, signals, assets, 0.0)
        ref_totals = compute_totals(ref, signals, assets)
        scales = Scales.from_totals(ref_totals.cost_bdt, ref_totals.co2_kg)
        problems = self._precheck(assets)

        def finish(schedule: Schedule, status: PlanStatus, extra: list[str], fallback: bool) -> PlanResult:
            return PlanResult(
                start=forecast.start,
                weight=weight,
                schedule=schedule,
                totals=compute_totals(schedule, signals, assets),
                status=status,
                solve_seconds=time.perf_counter() - t0,
                reserve_margin_kwh=reserve_margin_kwh,
                fallback_used=fallback or forecast.fallback_used,
                notes=[*extra, *notes],
            )

        if not problems:
            m = build_model(forecast, signals, assets, weight, reserve_margin_kwh, scales, fixes=fixes)
            out = solve_model(m, base, self.time_limit_s)
            if out.status in ("optimal", "feasible") and out.schedule is not None:
                violations = find_violations(
                    out.schedule, forecast, signals, assets, reserve_margin_kwh, fixes=fixes
                )
                if not violations:
                    out.schedule.check_energy_balance(TOL)
                    extra = [out.message] if out.message else []
                    return finish(out.schedule, out.status, extra, False)
                problems = [
                    f"solver returned a schedule that fails the constraint check: {v}" for v in violations
                ]
            elif out.status == "infeasible":
                problems = ["no schedule satisfies every hard constraint (solver proved infeasibility)"]
            else:
                return self._fallback(
                    forecast, signals, assets, reserve_margin_kwh, out.message, finish, fixes
                )

        return self._infeasible(
            forecast, signals, assets, weight, reserve_margin_kwh, scales, problems, finish, fixes
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _context_notes(forecast: Forecast, assets: AssetConstraints, reserve: float) -> list[str]:
        b = assets.battery
        notes = []
        if forecast.fallback_used:
            notes.append(f"Forecast fallback in use: {forecast.note or 'no reason given'}")
        notes.append(
            "Plan uses P50 solar and P50 base load; battery must end the day at or above its starting charge "
            f"({b.initial_soc_kwh:.1f} kWh)."
        )
        if reserve > 0:
            raised = min(b.soc_min_kwh + reserve, b.soc_max_kwh)
            msg = f"Battery reserve margin {reserve:.0f} kWh: SoC floor raised to {raised:.0f} kWh"
            if raised > b.initial_soc_kwh:
                msg += " (reached as fast as full-power charging allows from the starting charge)"
            notes.append(msg + ".")
        optional = rules.ev_optional_slots(assets.ev)
        if optional:
            notes.append(
                f"EV slots {_ranges(optional)} are an evening plug-in window for the next departure: "
                "charging there is optional; the departure energy is required only before "
                f"slot {assets.ev.departure_slot or 'end of day'}."
            )
        notes.append(
            "HVAC comfort is modelled as the occupied minimum power (no thermal model), so pre-cooling is "
            "not used."
        )
        return notes

    @staticmethod
    def _precheck(assets: AssetConstraints) -> list[str]:
        ev = assets.ev
        deliverable = rules.ev_deliverable_kwh(ev)
        if ev.energy_required_kwh > deliverable + TOL:
            return [
                f"EV fleet needs {ev.energy_required_kwh:.1f} kWh before departure "
                f"(slot {ev.departure_slot}) but the chargers can deliver at most {deliverable:.1f} kWh "
                "in the plugged-in slots before it"
            ]
        return []

    def _fallback(self, forecast, signals, assets, reserve, reason, finish, fixes=None) -> PlanResult:
        sched = rule_based_schedule(forecast, signals, assets, reserve)
        violations = find_violations(sched, forecast, signals, assets, reserve, fixes=fixes)
        if not violations:
            return finish(
                sched,
                "fallback",
                [
                    f"Optimiser unavailable ({reason}); using the rule-based fallback schedule, which "
                    "satisfies every hard constraint but is not optimised for cost or CO2."
                ],
                True,
            )
        return finish(
            sched,
            "infeasible",
            [
                f"{UNSAFE_PREFIX}: optimiser unavailable ({reason}) and the rule-based fallback breaks hard "
                "constraints.",
                *violations,
                *(fixes.infeasible_notes(assets, reserve) if fixes else []),
            ],
            True,
        )

    def _infeasible(
        self, forecast, signals, assets, weight, reserve, scales, problems, finish, fixes=None
    ) -> PlanResult:
        """Slack-mode solve to show where the day cannot be served; never relaxes anything silently."""
        h = slot_hours()
        m = build_model(forecast, signals, assets, weight, reserve, scales, elastic=True, fixes=fixes)
        out = solve_model(m, list(forecast.load.p50_kw), self.time_limit_s)
        if out.schedule is None:
            return self._fallback(
                forecast, signals, assets, reserve, f"diagnostic solve failed: {out.message}", finish, fixes
            )
        detail = []
        short = [t for t, v in enumerate(out.unserved_kw) if v * h > TOL]
        if short:
            kwh = sum(out.unserved_kw) * h
            detail.append(f"{kwh:.1f} kWh of demand cannot be served in slots {_ranges(short)}")
            for fix in fixes.slots if fixes else ():
                if fix.slot in short:
                    minutes = fix.slot * slot_minutes()
                    powers = ", ".join(f"{k}={v:g} kW" for k, v in fix.values().items())
                    detail.append(
                        f"Fixed slot {fix.slot} at {minutes // 60:02d}:{minutes % 60:02d} "
                        f"({powers}): {out.unserved_kw[fix.slot]:.1f} kW of demand remains unserved "
                        "with all permitted supply and battery SoC limits."
                    )
        if out.ev_short_kwh > TOL:
            detail.append(f"EV departure energy short by {out.ev_short_kwh:.1f} kWh")
        return finish(
            out.schedule,
            "infeasible",
            [
                f"{UNSAFE_PREFIX}: this day has no schedule that meets every hard constraint; the schedule "
                "shown is diagnostic only and does not balance supply and demand.",
                *problems,
                *detail,
                *(fixes.infeasible_notes(assets, reserve) if fixes else []),
            ],
            True,
        )
