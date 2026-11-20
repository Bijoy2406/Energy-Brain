"""Request and response models of the dashboard API (#21). These shapes are the contract with the frontend
(#27-#33): ``docs/api.md`` describes them and ``docs/openapi.json`` is exported from them.

The agent outputs are reused unchanged from ``energy_brain.schemas`` (``PlanResult``, ``Forecast``,
``SignalTable``, ``AssetConstraints``) and ``energy_brain.explainer`` (``ExplainerAnswer``); nothing here
changes them.

Normal request validation keeps FastAPI's standard 422 body. What-if validation and service errors
use :class:`ErrorBody`:
``{"detail": {"code": "...", "message": "...", "retry_after_s": null}}``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import date as Date
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from energy_brain.explainer import ExplainerAnswer
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, SignalTable, Totals
from energy_brain.timeaxis import site_tz, slot_minutes

#: First day the API plans: the start of the data the models and the outage calendar cover.
FIRST_PLAN_DAY = date(2022, 1, 1)
#: Longest operator question accepted by POST /explain (characters, after trimming).
MAX_QUESTION_CHARS = 500
#: Weights are rounded to this many decimals before planning (cache key; a slider sends many nearby values).
WEIGHT_DECIMALS = 2


def today_local() -> date:
    """Today in the site time zone (Asia/Dhaka)."""
    return datetime.now(site_tz()).date()


def last_plan_day() -> date:
    """Last day the API plans: tomorrow (day-ahead), site time."""
    return today_local() + timedelta(days=1)


def weight_key(w: float) -> float:
    """``w`` rounded to 0.01 and kept in [0, 1] (no ``-0.0``)."""
    return min(1.0, max(0.0, round(float(w), WEIGHT_DECIMALS))) + 0.0


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------------------------- errors


class ErrorDetail(_Model):
    code: str = Field(description="stable machine-readable code, e.g. rate_limited, backtest_unavailable")
    message: str = Field(description="human-readable explanation (no stack trace, no secrets)")
    retry_after_s: float | None = Field(default=None, description="for rate_limited / busy: seconds to wait")


class ErrorBody(_Model):
    detail: ErrorDetail


# ---------------------------------------------------------------------------------------------- requests


class _DayRequest(_Model):
    """A day and a cost/carbon weight."""

    date: Date = Field(description="planning day, YYYY-MM-DD, from 2022-01-01 to tomorrow (site time)")
    w: float = Field(
        ge=0.0, le=1.0, description="weight in [0, 1]: 1 = cheapest, 0 = cleanest; rounded to 0.01"
    )

    @field_validator("date")
    @classmethod
    def _in_range(cls, v: Date) -> Date:
        last = last_plan_day()
        if not FIRST_PLAN_DAY <= v <= last:
            raise ValueError(
                f"date must be between {FIRST_PLAN_DAY.isoformat()} and {last.isoformat()} (tomorrow); "
                "the trained models cover 2022-01-01..2024-12-31, later days use a fallback forecast"
            )
        return v

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"date": "2024-07-02", "w": 0.5}]}
    )


def time_slot(value: str) -> int:
    """Strict HH:MM on the site's slot grid; 24:00 is not a slot start."""
    import re

    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("time must be HH:MM in site time (00:00..23:45)")
    hour, minute = map(int, value.split(":"))
    minutes = hour * 60 + minute
    if minutes % slot_minutes():
        raise ValueError(f"time must be on the {slot_minutes()}-minute grid (site time)")
    return minutes // slot_minutes()


def _aligned_time(value: str) -> str:
    time_slot(value)
    return value


SlotTime = Annotated[str, AfterValidator(_aligned_time)]


class SlotOverride(_Model):
    """Operator decisions for one slot; site asset limits are validated by the service."""

    time: SlotTime = Field(description="HH:MM on the 15-minute grid, site time (Asia/Dhaka)")
    battery_kw: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="signed kW: positive charges, negative discharges, 0 idles; bounded by site limits",
    )
    generator_kw: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="kW: 0 turns off, otherwise within the site's [min_kw, rated_kw]",
    )

    @model_validator(mode="after")
    def _has_decision(self) -> SlotOverride:
        if self.battery_kw is None and self.generator_kw is None:
            raise ValueError("override needs at least one of battery_kw or generator_kw")
        return self


class PlanRequest(_DayRequest):
    """A normal plan or a re-solve with operator decisions fixed for selected slots."""

    overrides: list[SlotOverride] | None = Field(
        default=None,
        max_length=96,
        description="optional unique slot overrides (at most 96)",
    )

    @field_validator("overrides")
    @classmethod
    def _unique_slots(cls, value: list[SlotOverride] | None) -> list[SlotOverride] | None:
        if value and len({v.time for v in value}) != len(value):
            raise ValueError("duplicate override time; use one override per slot")
        return value


class CloudRequest(_DayRequest):
    """Simulated cloud event on a historical replay forecast; this is not live weather data."""

    time: SlotTime = Field(description="event start HH:MM, on the 15-minute grid, site time")
    drop_pct: float = Field(ge=1, le=100, description="simulated reduction of all solar quantiles, percent")
    duration_minutes: int = Field(
        default=120,
        ge=15,
        le=720,
        description="simulated duration; affected slots round up to whole slots and clip at end of day",
    )


class ExplainRequest(_DayRequest):
    """A question about the plan for (date, w)."""

    question: str = Field(description=f"operator question in English, 1-{MAX_QUESTION_CHARS} characters")

    @field_validator("question")
    @classmethod
    def _question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        if len(v) > MAX_QUESTION_CHARS:
            raise ValueError(f"question is too long ({len(v)} characters, at most {MAX_QUESTION_CHARS})")
        return v

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"date": "2024-07-02", "w": 0.5, "question": "Why did you run the generator at 7 PM?"}
            ]
        },
    )


# ---------------------------------------------------------------------------------------------- /days


class OutageBlock(_Model):
    start: str = Field(description="HH:MM, site time")
    end: str = Field(description="HH:MM (exclusive; 24:00 = end of day)")


class DayInfo(_Model):
    date: Date
    weekday: str = Field(description="Mon .. Sun")
    workday: bool = Field(description="false on the weekly off-day and public holidays (site closed)")
    weekly_off: bool
    holiday: bool = Field(description="Bangladesh public holiday")
    outage: bool = Field(description="the outage calendar has load-shedding on this day")
    outage_slots: int
    outage_blocks: list[OutageBlock]
    label: str
    demo: bool = Field(description="the day of the Explainer demo questions")
    dynamic: bool = Field(description="added by the API (tomorrow), not from config/demo_days.yaml")
    forecast_tier: Literal["model", "fallback"] = Field(
        description="model = trained models (tier 0); fallback = persistence / analog day"
    )
    forecast_tier_source: Literal["predicted", "planned"] = Field(
        description="predicted from data/model files and the date until the day has been planned"
    )
    signal_fallback: bool = Field(description="outages copied from an analog day (date outside the calendar)")
    in_backtest: bool = Field(description="the day is in reports/backtest.json")


class DaysResponse(_Model):
    first_day: date = Field(description="earliest date POST /plan accepts")
    last_day: date = Field(description="latest date POST /plan accepts (tomorrow)")
    days: list[DayInfo]


# ---------------------------------------------------------------------------------------------- /plan


class FallbackInfo(_Model):
    used: bool
    note: str | None = Field(description="the stable one-line FALLBACK note, or null")
    fields: dict[str, str] | None = Field(description="the note parsed into key=value fields, or null")


class TotalsDelta(_Model):
    """Signed planned totals, after minus before; no settlement against actuals."""

    cost_bdt: float = Field(description="change in planned cost, BDT")
    co2_kg: float = Field(description="change in planned emissions, kg CO2")
    generator_hours: float = Field(description="change in generator running hours")
    diesel_litres: float = Field(description="change in planned diesel consumption, litres")

    @classmethod
    def between(cls, before: Totals, after: Totals) -> TotalsDelta:
        return cls(**{k: getattr(after, k) - getattr(before, k) for k in cls.model_fields})


class OverrideImpact(_Model):
    applied: list[SlotOverride] = Field(description="operator decisions used as hard constraints")
    baseline: Totals = Field(description="normal plan totals for the same date and rounded weight")
    delta: TotalsDelta = Field(description="override totals minus baseline totals")
    feasible: bool = Field(description="all planner hard constraints and overrides met; also check risk_ok")


class PlanResponse(_Model):
    """One plan plus the agent outputs behind it (what the dashboard charts need)."""

    date: Date
    weight: float = Field(description="weight the plan was solved with (w rounded to 0.01)")
    weight_requested: float
    safe: bool = Field(
        description="false = NOT SAFE TO EXECUTE: show the banner, never present the schedule as runnable. "
        "Normal plans use planner.is_safe; what-if responses also require Risk to pass."
    )
    risk_ok: bool = Field(description="the Risk agent's verdict (plan.risk.ok)")
    banners: list[str] = Field(
        description="warnings to show above the plan (unsafe plan, forecast fallback, signal fallback)"
    )
    forecast_fallback: FallbackInfo
    signal_fallback: FallbackInfo
    forecast_source: str = Field(
        description="model (tier 0), or the fallback's data source: processed / sample / injected"
    )
    plan: PlanResult
    forecast: Forecast
    signals: SignalTable
    assets: AssetConstraints
    cached: bool = Field(description="served from the plan cache (not solved for this request)")
    computed_at: datetime = Field(description="when this plan was solved (site time)")
    override: OverrideImpact | None = Field(
        default=None, description="operator override impact, otherwise null"
    )


class CloudEvent(CloudRequest):
    kind: Literal["simulated cloud event"] = Field(
        default="simulated cloud event",
        description="forecast what-if on a historical replay, not live data",
    )


class AffectedSlots(_Model):
    start: int = Field(description="first affected slot, inclusive (zero based)")
    end: int = Field(description="last affected slot, exclusive; clipped to the day")


class CloudResponse(_Model):
    before: PlanResponse = Field(description="normal plan before the simulated cloud event")
    after: PlanResponse = Field(description="re-plan with reduced solar and the executed prefix frozen")
    event: CloudEvent = Field(description="echo of the simulated cloud event")
    affected_slots: AffectedSlots = Field(description="range affected by the simulated cloud event")
    delta: TotalsDelta = Field(description="after minus before planned totals")
    replan_seconds: float = Field(
        description="wall time of re-planning only, excluding baseline and lock wait"
    )
    safe: bool = Field(description="after plan meets hard constraints and passes Risk")
    risk_ok: bool = Field(description="after plan Risk verdict")
    banners: list[str] = Field(description="simulation, fallback, infeasibility and Risk warnings")


# ---------------------------------------------------------------------------------------------- /pareto


class ParetoPoint(_Model):
    weight: float
    cost_bdt: float
    co2_kg: float
    generator_hours: float
    diesel_litres: float
    status: str
    safe: bool
    risk_ok: bool
    replans: int
    efficient: bool
    group: int
    same_as_weight: float | None
    plan_cached: bool = Field(description="plan cache hit when this Pareto body was computed")


class ParetoResponse(_Model):
    date: Date
    weights_meaning: Literal["1 = cheapest, 0 = cleanest"] = "1 = cheapest, 0 = cleanest"
    basis: Literal["planned totals at the P50 forecast, before settlement against actuals"] = (
        "planned totals at the P50 forecast, before settlement against actuals"
    )
    points: list[ParetoPoint]
    frontier: list[ParetoPoint] = Field(
        description="lowest-weight representative of each efficient group, sorted by cost ascending"
    )
    forecast_fallback: FallbackInfo
    signal_fallback: FallbackInfo
    banners: list[str]
    forecast_source: str
    cached: bool
    computed_at: datetime
    solve_seconds: float = Field(description="total wall time of this request, including cache waits")
    distinct_points: int = Field(description="distinct cost/CO2 groups among safe plans")


# ---------------------------------------------------------------------------------------------- /backtest


class BacktestHeadline(_Model):
    weight: float
    baseline_names: dict[str, str]
    lines: list[str]


class BacktestSummary(_Model):
    """The summary of ``reports/backtest.json`` (schema_version 1, see docs/interfaces.md, Evaluator)."""

    report: str
    schema_version: int
    caveats: list[str] = Field(description="show these next to every number")
    headline: BacktestHeadline
    summary: dict[str, Any] = Field(description="days, by_weight, forecast: as in reports/backtest.json")
    config: dict[str, Any] = Field(description="provenance; sha256 maps only with include_sha=true")
    run: dict[str, Any]


class BacktestMethodPoint(_Model):
    adjusted_cost_bdt: float | None
    adjusted_co2_kg: float | None
    cost_bdt: float | None
    co2_kg: float | None


class BacktestDayRow(_Model):
    date: Date
    weekday: str
    workday: bool
    outage_slots: int
    included: bool
    excluded_reason: str | None
    forecast_fallback_used: bool
    methods: dict[str, BacktestMethodPoint] = Field(
        description="optimiser (at the requested weight), baseline_solar_first, baseline_time_of_use"
    )


class BacktestDaysResponse(_Model):
    weight: float
    headline_weight: float
    weights: list[float]
    baseline_names: dict[str, str]
    caveats: list[str]
    days: list[BacktestDayRow]


# ---------------------------------------------------------------------------------------------- /explain


class ExplainResponse(ExplainerAnswer):
    """An ``ExplainerAnswer`` plus where it came from."""

    date: Date
    weight: float
    weight_requested: float
    cached: bool = Field(
        default=False,
        description="true = a stored verified demo answer, re-checked against this plan's tools before "
        "serving",
    )
    cached_recorded_at: str | None = Field(default=None, description="when the stored answer was recorded")
    cached_model: str | None = Field(default=None, description="the LLM that produced the stored answer")
    live_error: str | None = Field(
        default=None, description="the live call's error when a stored answer was served instead"
    )


# ---------------------------------------------------------------------------------------------- /health


class CacheStats(_Model):
    size: int
    max_size: int
    hits: int
    misses: int
    inflight: int


class WarmupStatus(_Model):
    enabled: bool
    state: Literal["off", "pending", "running", "done", "stopped"]
    planned: int
    total: int
    errors: int


class TranscribeResponse(_Model):
    """Speech turned into text for the chat input box. Audio and text are not stored."""

    text: str = Field(description="what was said (at most 500 characters); empty when no speech was found")
    language: str = Field(description="en (the requested language, else what Whisper detected)")
    duration_s: float = Field(description="length of the audio in seconds, as reported by the speech service")
    model: str = Field(description="speech model used")
    no_speech: bool = Field(description="true when nothing but silence or noise was found (text is empty)")


class HealthResponse(_Model):
    status: Literal["ok"]
    version: str
    processed_data: bool = Field(description="data/processed has load, pv and weather")
    models: bool = Field(description="all six trained models exist")
    sample_data: bool
    backtest: bool = Field(description="reports/backtest.json exists")
    demo_answers: int = Field(description="stored verified demo answers loaded")
    llm_key_configured: bool = Field(description="an LLM key is set (the key itself is never returned)")
    transcription_available: bool = Field(description="POST /transcribe can work (the same key is set)")
    llm_model: str
    explain_cache: str = Field(description="fallback / prefer / off (EB_EXPLAIN_CACHE)")
    cache: CacheStats
    warmup: WarmupStatus
