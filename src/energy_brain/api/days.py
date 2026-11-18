"""Demo days for GET /days (#21): ``config/demo_days.yaml`` plus a dynamic "tomorrow" entry.

The YAML is read when the service first needs it (never at import) and validated: unique ISO dates inside the
planning range, a non-empty label, at most one ``demo: true``.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import date as Date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from energy_brain.api.models import FIRST_PLAN_DAY, OutageBlock
from energy_brain.timeaxis import slot_times

#: Days the trained models can forecast (tier 0): the processed data range minus the 7 lag days it needs.
#: Used only to *predict* the tier in GET /days before a day is planned; the plan itself reports the truth.
TIER0_FIRST_DAY = date(2022, 1, 8)
TIER0_LAST_DAY = date(2024, 12, 31)
TOMORROW_LABEL = "Tomorrow (fallback forecast: outside the data the models cover)"


class DemoDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    date: Date
    label: str = Field(min_length=1, max_length=120)
    demo: bool = False

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("label must not be empty")
        return v


class DemoDaysFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    days: list[DemoDay] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> DemoDaysFile:
        dates = [d.date for d in self.days]
        dupes = sorted({d.isoformat() for d in dates if dates.count(d) > 1})
        if dupes:
            raise ValueError(f"duplicate dates: {dupes}")
        early = [d.isoformat() for d in dates if d < FIRST_PLAN_DAY]
        if early:
            raise ValueError(f"dates before {FIRST_PLAN_DAY.isoformat()}: {early}")
        if sum(d.demo for d in self.days) > 1:
            raise ValueError("at most one day may have demo: true")
        return self


def load_demo_days(path: str | Path) -> list[DemoDay]:
    """Validated demo days from ``path``. Raises ``ValueError`` with the file name on a bad file."""
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        return list(DemoDaysFile.model_validate(raw).days)
    except FileNotFoundError:
        raise ValueError(f"demo days file not found: {p}") from None
    except Exception as exc:
        raise ValueError(f"invalid demo days file {p}: {exc}") from None


def predicted_tier(day: date, processed_data: bool, models: bool) -> str:
    """``model`` if tier 0 can run for ``day`` (files present, date in range), else ``fallback``."""
    ok = processed_data and models and TIER0_FIRST_DAY <= day <= TIER0_LAST_DAY
    return "model" if ok else "fallback"


def outage_blocks(start: datetime, outage: list[bool]) -> list[OutageBlock]:
    """Contiguous outage slots as HH:MM blocks (end exclusive; ``24:00`` = end of the day)."""
    times = slot_times(start)
    step = times[1] - times[0] if len(times) > 1 else None
    blocks: list[OutageBlock] = []
    i, n = 0, len(outage)
    while i < n:
        if not outage[i]:
            i += 1
            continue
        j = i
        while j < n and outage[j]:
            j += 1
        end = times[j - 1] + step if step is not None else times[j - 1]
        end_text = (
            "24:00"
            if end.date() != times[i].date() and end.hour == 0 and end.minute == 0
            else (end.strftime("%H:%M"))
        )
        blocks.append(OutageBlock(start=times[i].strftime("%H:%M"), end=end_text))
        i = j
    return blocks
