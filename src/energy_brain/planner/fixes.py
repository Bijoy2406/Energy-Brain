"""Immutable partial decisions for operator overrides and historical replay prefixes.

Only controllable powers are fixed. Grid, solar dispatch and SoC remain model variables. Invalid
powers are argument errors; valid powers that conflict with dynamics or balance produce an unsafe plan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

from energy_brain.planner import rules
from energy_brain.schemas import AssetConstraints, Schedule
from energy_brain.timeaxis import n_slots, slot_hours, slot_minutes


@dataclass(frozen=True)
class SlotFix:
    """Optional powers in kW for one slot; None leaves the decision free."""

    slot: int
    battery_charge_kw: float | None = None
    battery_discharge_kw: float | None = None
    generator_kw: float | None = None
    ev_charge_kw: float | None = None
    hvac_kw: float | None = None

    def values(self) -> dict[str, float]:
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name != "slot" and getattr(self, f.name) is not None
        }


@dataclass(frozen=True)
class SlotFixes:
    """Unique slot fixes, copied to a tuple so callers cannot mutate a running solve."""

    slots: tuple[SlotFix, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", tuple(self.slots))
        seen = set()
        for fix in self.slots:
            if type(fix.slot) is not int or not 0 <= fix.slot < n_slots():
                raise ValueError(f"fixed slot must be in [0, {n_slots() - 1}]")
            if fix.slot in seen:
                raise ValueError(f"duplicate fixed slot {fix.slot}")
            seen.add(fix.slot)
            for name, value in fix.values().items():
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{name} must be finite and nonnegative (slot {fix.slot})")

    def validate(self, assets: AssetConstraints) -> None:
        """Reject out-of-range arguments before building either the hard or elastic model."""
        limits = {
            "battery_charge_kw": assets.battery.max_charge_kw,
            "battery_discharge_kw": assets.battery.max_discharge_kw,
            "generator_kw": assets.generator.rated_kw,
            "ev_charge_kw": assets.ev.charger_kw,
            "hvac_kw": assets.hvac.rated_kw,
        }
        for fix in self.slots:
            for name, value in fix.values().items():
                # A frozen prefix comes from solver floats (e.g. 1.000000000000001 at a 1 kW bound).
                # Keep the equality value; tolerate only extraction noise when validating arguments.
                if value > limits[name] + 1e-7:
                    raise ValueError(f"{name} exceeds {limits[name]:g} kW in slot {fix.slot}")
            g = fix.generator_kw
            if g is not None and 0 < g < assets.generator.min_kw - 1e-7:
                raise ValueError(
                    f"generator_kw must be 0 or in [{assets.generator.min_kw:g}, "
                    f"{assets.generator.rated_kw:g}] kW (slot {fix.slot})"
                )

    @classmethod
    def prefix(cls, schedule: Schedule, end: int) -> SlotFixes:
        """Freeze controllable decisions before exclusive ``end``; SoC follows from dynamics."""
        names = [f.name for f in fields(SlotFix) if f.name != "slot"]
        return cls(tuple(SlotFix(t, **{k: getattr(schedule, k)[t] for k in names}) for t in range(end)))

    def infeasible_notes(self, assets: AssetConstraints, reserve: float) -> list[str]:
        """Name the retained fixes, plus any provable battery reachability conflict.

        This is not an irreducible conflict set: balance and EV conflicts may involve several slots.
        The elastic solve and independent checker supply the remaining diagnosis.
        """
        if not self.slots:
            return []
        indices = ", ".join(str(f.slot) for f in self.slots)
        notes = [
            f"Fixed slots {indices} are required; these decisions must "
            "also satisfy balance, battery SoC limits and EV departure energy."
        ]
        by_slot = {f.slot: f for f in self.slots}
        b, h = assets.battery, slot_hours()
        lo = hi = b.initial_soc_kwh
        floors = rules.soc_floor_kwh(b, reserve)
        for t in range(n_slots()):
            f = by_slot.get(t, SlotFix(t))
            c, d = f.battery_charge_kw, f.battery_discharge_kw
            max_c = c if c is not None else (0 if d and d > 0 else b.max_charge_kw)
            max_d = d if d is not None else (0 if c and c > 0 else b.max_discharge_kw)
            lo += b.one_way_efficiency * (c or 0) * h - max_d * h / b.one_way_efficiency
            hi += b.one_way_efficiency * max_c * h - (d or 0) * h / b.one_way_efficiency
            clock = f"{t * slot_minutes() // 60:02d}:{t * slot_minutes() % 60:02d}"
            if hi < floors[t] - 1e-4:
                notes.append(
                    f"Fixed slots: battery cannot discharge {d or 0:g} kW at {clock} "
                    f"(slot {t}): SoC floor {floors[t]:g} kWh cannot be met."
                )
                break
            if lo > b.soc_max_kwh + 1e-4:
                notes.append(f"Fixed slots: battery charge at {clock} (slot {t}) exceeds the SoC ceiling.")
                break
            lo, hi = max(lo, floors[t]), min(hi, b.soc_max_kwh)
        return notes
