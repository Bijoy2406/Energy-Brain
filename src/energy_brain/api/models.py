"""Request and response models of the dashboard API (#21). These shapes are the contract with the frontend
(#27-#33): ``docs/api.md`` describes them and ``docs/openapi.json`` is exported from them.

The agent outputs are reused unchanged from ``energy_brain.schemas`` (``PlanResult``, ``Forecast``,
``SignalTable``, ``AssetConstraints``) and ``energy_brain.explainer`` (``ExplainerAnswer``); nothing here
changes them.

Errors other than request validation (422, FastAPI's standard body) use :class:`ErrorBody`:
``{"detail": {"code": "...", "message": "...", "retry_after_s": null}}``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import date as Date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from energy_brain.explainer import ExplainerAnswer
from energy_brain.schemas import AssetConstraints, Forecast, PlanResult, SignalTable
from energy_brain.timeaxis import site_tz

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


class PlanRequest(_Model):
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


class ExplainRequest(PlanRequest):
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


class PlanResponse(_Model):
    """One plan plus the agent outputs behind it (what the dashboard charts need)."""

    date: Date
    weight: float = Field(description="weight the plan was solved with (w rounded to 0.01)")
    weight_requested: float
    safe: bool = Field(
        description="false = NOT SAFE TO EXECUTE (planner.is_safe): show the banner, never present the "
        "schedule as runnable"
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


class HealthResponse(_Model):
    status: Literal["ok"]
    version: str
    processed_data: bool = Field(description="data/processed has load, pv and weather")
    models: bool = Field(description="all six trained models exist")
    sample_data: bool
    backtest: bool = Field(description="reports/backtest.json exists")
    demo_answers: int = Field(description="stored verified demo answers loaded")
    llm_key_configured: bool = Field(description="an LLM key is set (the key itself is never returned)")
    llm_model: str
    explain_cache: str = Field(description="fallback / prefer / off (EB_EXPLAIN_CACHE)")
    cache: CacheStats
    warmup: WarmupStatus
