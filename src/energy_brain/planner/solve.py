"""Solve a planner model with HiGHS and extract a ``Schedule`` (#17)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

import pyomo.environ as pe
from pyomo.contrib.appsi.base import TerminationCondition
from pyomo.contrib.appsi.solvers import Highs

from energy_brain.schemas import Schedule

#: Wall-clock limit per solve (s). The PRD target is < 10 s per plan on a laptop.
TIME_LIMIT_S = 5.0
#: Relative MIP gap. The problem is small, so a tight gap is cheap and keeps the w-sweep monotone.
MIP_REL_GAP = 1e-6
#: Solver noise below this (kW / kWh) is snapped to 0.
SNAP = 1e-7

SolveStatus = Literal["optimal", "feasible", "infeasible", "error"]


@dataclass
class SolveOutcome:
    """What the solver returned. ``schedule`` is set when ``status`` is optimal or feasible.

    ``unserved_kw`` / ``ev_short_kwh`` are filled for slack-mode models only.
    """

    status: SolveStatus
    seconds: float
    schedule: Schedule | None = None
    message: str = ""
    unserved_kw: list[float] = field(default_factory=list)
    ev_short_kwh: float = 0.0


def _vals(var) -> list[float]:
    out = []
    for t in sorted(var.keys()):
        v = pe.value(var[t], exception=False)
        v = 0.0 if v is None or abs(v) < SNAP else float(v)
        out.append(max(0.0, v))
    return out


def extract_schedule(m: pe.ConcreteModel, base_load_kw: list[float]) -> Schedule:
    """Build a ``Schedule`` from a solved model (values snapped and clipped at 0)."""
    return Schedule(
        grid_import_kw=_vals(m.grid),
        solar_used_kw=_vals(m.solar_used),
        solar_curtailed_kw=_vals(m.solar_curt),
        battery_charge_kw=_vals(m.charge),
        battery_discharge_kw=_vals(m.discharge),
        battery_soc_kwh=_vals(m.soc),
        hvac_kw=_vals(m.hvac),
        ev_charge_kw=_vals(m.ev),
        generator_kw=_vals(m.gen),
        base_load_kw=list(base_load_kw),
    )


def solve_model(
    m: pe.ConcreteModel, base_load_kw: list[float], time_limit_s: float = TIME_LIMIT_S
) -> SolveOutcome:
    """Solve ``m`` with HiGHS (APPSI). Never raises: solver errors become ``status="error"``."""
    t0 = time.perf_counter()
    try:
        solver = Highs()
        if not solver.available():
            return SolveOutcome("error", time.perf_counter() - t0, message="HiGHS solver not available")
        solver.config.load_solution = False
        solver.config.time_limit = time_limit_s
        solver.config.mip_gap = MIP_REL_GAP
        res = solver.solve(m)
        tc = res.termination_condition
        if tc in (TerminationCondition.infeasible, TerminationCondition.infeasibleOrUnbounded):
            return SolveOutcome("infeasible", time.perf_counter() - t0, message=f"solver: {tc.name}")
        if res.best_feasible_objective is None:
            return SolveOutcome(
                "error", time.perf_counter() - t0, message=f"no feasible solution found (solver: {tc.name})"
            )
        res.solution_loader.load_vars()
        status: SolveStatus = "optimal" if tc == TerminationCondition.optimal else "feasible"
        msg = "" if status == "optimal" else f"solver stopped early ({tc.name}); best solution found is used"
        out = SolveOutcome(status, time.perf_counter() - t0, extract_schedule(m, base_load_kw), msg)
        if hasattr(m, "unserved"):
            out.unserved_kw = _vals(m.unserved)
            out.ev_short_kwh = _vals_scalar(m.ev_short)
        return out
    except Exception as exc:  # noqa: BLE001 - the planner must never crash on a solver failure
        return SolveOutcome("error", time.perf_counter() - t0, message=f"solver error: {exc!r}")


def _vals_scalar(var) -> float:
    v = pe.value(var, exception=False)
    return 0.0 if v is None or abs(v) < SNAP else max(0.0, float(v))
