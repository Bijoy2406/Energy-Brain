"""Service layer of the dashboard API (#21). Routes stay thin; everything stateful lives here.

**Plans.** A plan is a ``PlanContext`` (``explainer.PlanContext.from_agents``: the plan plus the Forecast,
Signal and Asset outputs it was solved on), so POST /plan and POST /explain describe the same object. Plans
are cached in an LRU keyed by ``(date, w rounded to 0.01)`` (``ApiSettings.cache_size`` entries).

**What-ifs.** Operator overrides and simulated cloud events use a separate bounded LRU and single-flight
registry. Only their normal baseline comes from the default cache. The Explainer never reads what-ifs.

**Thread safety.** Endpoints are sync, so FastAPI runs them in its thread pool and the event loop never
blocks. The agents cache data frames and models lazily and HiGHS is not re-entrant, so one lock
(``_plan_lock``) serialises every agent and solver call (a solve takes about 0.1-0.3 s). Requests for a key
that is already being solved wait for that result instead of solving again (single-flight). LLM calls run
outside the lock on the immutable cached context, at most ``explain_concurrency`` at a time.

**Warm-up.** ``start_warmup()`` (called by the app's lifespan unless ``EB_WARMUP=0``) plans every demo day at
the warm-up weights in a daemon thread, so the demo is fast. A cache miss is always solved on demand.

Nothing is read at import or construction: data, models, the demo-day file, the backtest and the stored
answers are loaded on first use.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from energy_brain import __version__
from energy_brain.api.days import (
    TOMORROW_LABEL,
    DemoDay,
    load_demo_days,
    outage_blocks,
    predicted_tier,
)
from energy_brain.api.demo_answers import StoredAnswer, find_stored, load_demo_answers, serve_stored
from energy_brain.api.models import (
    FIRST_PLAN_DAY,
    AffectedSlots,
    CacheStats,
    CloudEvent,
    CloudRequest,
    CloudResponse,
    DayInfo,
    DaysResponse,
    ExplainRequest,
    ExplainResponse,
    FallbackInfo,
    HealthResponse,
    OverrideImpact,
    ParetoPoint,
    ParetoResponse,
    PlanResponse,
    SlotOverride,
    TotalsDelta,
    WarmupStatus,
    last_plan_day,
    time_slot,
    weight_key,
)
from energy_brain.api.settings import ApiSettings
from energy_brain.explainer import ExplainerAgent, ExplainerAnswer, GroqClient, LLMError, PlanContext
from energy_brain.explainer.agent import banners
from energy_brain.planner import is_safe
from energy_brain.planner.fixes import SlotFix, SlotFixes
from energy_brain.schemas import Forecast
from energy_brain.timeaxis import n_slots, site_tz, slot_minutes

logger = logging.getLogger(__name__)

PROCESSED_FILES = ("load.parquet", "pv.parquet", "weather.parquet")
SAMPLE_FILES = ("load_sample.parquet", "pv_sample.parquet", "weather_sample.parquet")
MODEL_FILES = tuple(f"{k}_p{q}.txt" for k in ("solar", "load") for q in (10, 50, 90))
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

PARETO_WEIGHTS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0)
PARETO_CACHE_SIZE = 64
REPORT_CACHE_SIZE = 32
PARETO_REL_TOL = 1e-6

_UNSET = object()
WHATIF_CACHE_SIZE = 64
SIMULATED_CLOUD = (
    "This is a simulated cloud event: a solar forecast what-if on a historical replay day, "
    "not live weather data."
)


class InvalidOverrides(ValueError):
    """An operator decision is outside this site's asset limits."""


@dataclass(frozen=True)
class _ReplayInput:
    """Return an existing input without re-reading or mutating historical data."""

    value: Any

    def run(self, start: datetime) -> Any:
        return self.value


class ServiceBusy(Exception):
    """No free LLM slot within ``explain_wait_s``."""


class RateLimited(Exception):
    def __init__(self, retry_after_s: float, scope: str) -> None:
        super().__init__(f"rate limited ({scope})")
        self.retry_after_s = retry_after_s
        self.scope = scope


class RateLimiter:
    """Sliding one-minute window: at most ``per_key`` requests per client and ``total`` overall."""

    def __init__(
        self, per_key: int, total: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.per_key, self.total, self.window_s, self.clock = per_key, total, window_s, clock
        self._lock = threading.Lock()
        self._by_key: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and q[0] <= now - self.window_s:
            q.popleft()

    def hit(self, key: str) -> None:
        """Record one request for ``key`` or raise :class:`RateLimited` (nothing recorded then)."""
        with self._lock:
            now = self.clock()
            mine = self._by_key.setdefault(key, deque())
            self._trim(mine, now)
            self._trim(self._all, now)
            if len(mine) >= self.per_key:
                raise RateLimited(max(0.0, mine[0] + self.window_s - now), "client")
            if len(self._all) >= self.total:
                raise RateLimited(max(0.0, self._all[0] + self.window_s - now), "total")
            mine.append(now)
            self._all.append(now)
            for k in [k for k, q in self._by_key.items() if not q]:  # forget idle clients
                del self._by_key[k]


@dataclass(frozen=True)
class CachedPlan:
    context: PlanContext
    weight: float
    computed_at: datetime
    seconds: float


def _files_present(folder, names) -> bool:
    return all((folder / n).exists() for n in names)


def _fallback(note: str | None, parse: Callable[[str | None], dict | None]) -> FallbackInfo:
    fields = parse(note) if note else None
    return FallbackInfo(used=bool(note), note=note, fields=fields)


def default_agent_factory(settings: ApiSettings) -> Callable[[], dict[str, Any]]:
    """Real agents (``build_default_agents``) reading ``settings.data_dir`` / ``settings.models_dir``."""

    def build() -> dict[str, Any]:
        from energy_brain.defaults import build_default_agents
        from energy_brain.forecast import ModelForecastAgent
        from energy_brain.signals import CalendarSignalAgent

        return build_default_agents(
            forecast_agent=ModelForecastAgent(data_dir=settings.data_dir, models_dir=settings.models_dir),
            signal_agent=CalendarSignalAgent(data_dir=settings.data_dir),
        )

    return build


class PlanService:
    """Plans, days, backtest, explanations and health for the API (one instance per app)."""

    def __init__(
        self,
        settings: ApiSettings | None = None,
        *,
        agent_factory: Callable[[], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings or ApiSettings()
        self._agent_factory = agent_factory or default_agent_factory(self.settings)
        self._agents: dict[str, Any] | None = None
        self._plan_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[tuple[date, float], CachedPlan] = OrderedDict()
        self._inflight: dict[tuple[date, float], Future] = {}
        self._pareto_cache: OrderedDict[date, ParetoResponse] = OrderedDict()
        self._pareto_inflight: dict[date, Future] = {}
        self.pareto_limiter = RateLimiter(6, 30, clock=clock)
        self._whatif_cache: OrderedDict[tuple, Any] = OrderedDict()
        self._whatif_inflight: dict[tuple, Future] = {}
        self.override_limiter = RateLimiter(6, 30, clock=clock)
        self.cloud_limiter = RateLimiter(6, 30, clock=clock)
        self.report_limiter = RateLimiter(12, 60, clock=clock)
        self._report_lock = threading.Lock()
        self._report_cache: OrderedDict[tuple[str | None, float], bytes] = OrderedDict()
        self._tier_by_date: dict[date, str] = {}
        self._signals_by_date: dict[date, tuple[Any, str | None]] = {}
        self.hits = 0
        self.misses = 0
        self._load_lock = threading.Lock()
        self._backtest: Any = _UNSET
        self._demo_days: list[DemoDay] | None = None
        self._stored: list[StoredAnswer] | None = None
        self.limiter = RateLimiter(
            self.settings.explain_per_ip_per_min, self.settings.explain_total_per_min, clock=clock
        )
        self._explain_slots = threading.BoundedSemaphore(self.settings.explain_concurrency)
        self.transcribe_limiter = RateLimiter(
            self.settings.transcribe_per_ip_per_min, self.settings.transcribe_total_per_min, clock=clock
        )
        self.transcribe_slots = threading.BoundedSemaphore(self.settings.transcribe_concurrency)
        self._stop = threading.Event()
        self._warmup_thread: threading.Thread | None = None
        self._warmup = {"state": "off", "planned": 0, "total": 0, "errors": 0}

    # ------------------------------------------------------------------ lazily loaded inputs

    def _agents_locked(self) -> dict[str, Any]:
        if self._agents is None:
            self._agents = self._agent_factory()
        return self._agents

    def backtest(self) -> dict[str, Any] | None:
        """Parsed ``reports/backtest.json`` (read once), or None if it does not exist."""
        with self._load_lock:
            if self._backtest is _UNSET:
                from energy_brain.explainer import load_backtest

                self._backtest = load_backtest(self.settings.backtest_path)
            return self._backtest

    def demo_days(self) -> list[DemoDay]:
        with self._load_lock:
            if self._demo_days is None:
                self._demo_days = load_demo_days(self.settings.demo_days_path)
            return self._demo_days

    def report_pdf(
        self, report: dict[str, Any], month: str | None, weight: float | None, client: str | None = None
    ) -> bytes:
        """Bounded PDF LRU. Its own lock serialises renders, independently of agents/HiGHS.

        Every download is limited, including cache hits. Inputs are validated before counting.
        The underlying backtest is immutable for this service's lifetime, as with backtest().
        """
        from energy_brain.api.report import render_report, report_data

        data = report_data(report, month, weight)
        key = (month, data.weight)
        if client is not None:
            self.report_limiter.hit(client)
        with self._report_lock:
            if key not in self._report_cache:
                self._report_cache[key] = render_report(data)
                while len(self._report_cache) > REPORT_CACHE_SIZE:
                    self._report_cache.popitem(last=False)
            self._report_cache.move_to_end(key)
            return self._report_cache[key]

    def stored_answers(self) -> list[StoredAnswer]:
        with self._load_lock:
            if self._stored is None:
                self._stored = load_demo_answers(self.settings.demo_answers_path)
            return self._stored

    def processed_data(self) -> bool:
        return _files_present(self.settings.data_dir / "processed", PROCESSED_FILES)

    def models_present(self) -> bool:
        return _files_present(self.settings.models_dir, MODEL_FILES)

    # ------------------------------------------------------------------ plans

    @staticmethod
    def start_of(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=site_tz())

    def get_plan(self, day: date, w: float) -> tuple[CachedPlan, bool]:
        """``(plan, cached)`` for ``day`` at ``w`` (rounded to 0.01); solves on a miss (single-flight)."""
        key = (day, weight_key(w))
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
                self.hits += 1
                return entry, True
            fut = self._inflight.get(key)
            owner = fut is None
            if owner:
                fut = Future()
                self._inflight[key] = fut
                self.misses += 1
            else:
                self.hits += 1
        if not owner:
            return fut.result(), True
        try:
            entry = self._solve(*key)
        except BaseException as exc:
            with self._cache_lock:
                self._inflight.pop(key, None)
            fut.set_exception(exc)
            raise
        with self._cache_lock:
            self._cache[key] = entry
            while len(self._cache) > self.settings.cache_size:
                self._cache.popitem(last=False)
            self._inflight.pop(key, None)
        fut.set_result(entry)
        return entry, False

    def _solve(self, day: date, w: float) -> CachedPlan:
        start = self.start_of(day)
        backtest = self.backtest()
        with self._plan_lock:
            t0 = time.perf_counter()
            ctx = PlanContext.from_agents(start, w, backtest=backtest, **self._agents_locked())
            seconds = time.perf_counter() - t0
        self._tier_by_date[day] = "fallback" if ctx.forecast.fallback_used else "model"
        logger.info("planned %s w=%.2f in %.2f s (status=%s)", day, w, seconds, ctx.plan.status)
        return CachedPlan(ctx, w, datetime.now(site_tz()), seconds)

    def plan_response(self, day: date, w: float) -> PlanResponse:
        entry, cached = self.get_plan(day, w)
        return self._response(day, w, entry, cached)

    @staticmethod
    def _response(day: date, w: float, entry: CachedPlan, cached: bool) -> PlanResponse:
        from energy_brain.forecast import parse_note as parse_forecast_note
        from energy_brain.signals import parse_note as parse_signal_note

        ctx = entry.context
        ff = _fallback(ctx.forecast.note if ctx.forecast.fallback_used else None, parse_forecast_note)
        if ctx.forecast.fallback_used and not ff.used:  # fallback without a note: still flag it
            ff = FallbackInfo(used=True, note=None, fields=None)
        source = "model" if not ctx.forecast.fallback_used else (ff.fields or {}).get("source", "unknown")
        return PlanResponse(
            date=day,
            weight=entry.weight,
            weight_requested=w,
            safe=is_safe(ctx.plan),
            risk_ok=ctx.plan.risk.ok,
            banners=banners(ctx),
            forecast_fallback=ff,
            signal_fallback=_fallback(ctx.signal_note, parse_signal_note),
            forecast_source=source,
            plan=ctx.plan,
            forecast=ctx.forecast,
            signals=ctx.signals,
            assets=ctx.assets,
            cached=cached,
            computed_at=entry.computed_at,
        )

    # ------------------------------------------------------------------ operator / simulated what-ifs

    def _whatif(
        self, key: tuple, compute: Callable[[], Any], limiter: RateLimiter, client: str | None
    ) -> tuple[Any, bool]:
        """Separate bounded LRU and single-flight; never wait or solve with the cache lock held.

        Identical requests share a result. Only a new computation consumes rate-limit capacity.
        These entries can never be read by the Explainer or replace a default plan.
        """
        with self._cache_lock:
            entry = self._whatif_cache.get(key)
            if entry is not None:
                self._whatif_cache.move_to_end(key)
                return entry, True
            fut = self._whatif_inflight.get(key)
            owner = fut is None
            if owner:
                if client is not None:
                    limiter.hit(client)
                fut = Future()
                self._whatif_inflight[key] = fut
        if not owner:
            return fut.result(), True
        try:
            entry = compute()
        except BaseException as exc:
            with self._cache_lock:
                self._whatif_inflight.pop(key, None)
            fut.set_exception(exc)
            raise
        with self._cache_lock:
            self._whatif_cache[key] = entry
            while len(self._whatif_cache) > WHATIF_CACHE_SIZE:
                self._whatif_cache.popitem(last=False)
            self._whatif_inflight.pop(key, None)
        fut.set_result(entry)
        return entry, False

    def _replan(self, before: CachedPlan, forecast: Forecast, fixes: SlotFixes) -> CachedPlan:
        """Run Planner and Risk on exact replay inputs under the shared HiGHS lock.

        Elapsed time covers the complete orchestration, excluding lock waits and the baseline solve.
        HVAC has no thermal model; fixing its prefix does not imply comfort shifting.
        """
        ctx = before.context
        with self._plan_lock:
            agents = self._agents_locked()
            t0 = time.perf_counter()
            after = PlanContext.from_agents(
                forecast.start,
                before.weight,
                forecast_agent=_ReplayInput(forecast),
                signal_agent=_ReplayInput(ctx.signals),
                assets=_ReplayInput(ctx.assets),
                planner=agents["planner"],
                risk=agents["risk"],
                fixes=fixes,
                backtest=ctx.backtest,
            )
            seconds = time.perf_counter() - t0
        after = replace(after, signal_note=ctx.signal_note)
        return CachedPlan(after, before.weight, datetime.now(site_tz()), seconds)

    @staticmethod
    def _whatif_warnings(response: PlanResponse) -> PlanResponse:
        """Expose diagnostic notes and never label a Risk-failing what-if as safe."""
        warnings = list(response.banners)
        if not is_safe(response.plan):
            warnings.extend(response.plan.notes)
        if not response.risk_ok:
            warnings.append("Risk check failed; this what-if must not be presented as safe to execute.")
            warnings.extend(response.plan.risk.reasons)
        return response.model_copy(
            update={
                "safe": response.safe and response.risk_ok,
                "banners": list(dict.fromkeys(warnings)),
            }
        )

    def override_response(
        self, day: date, w: float, overrides: list[SlotOverride], client: str | None = None
    ) -> PlanResponse:
        """Re-solve with operator decisions. Only the baseline uses the normal plan cache."""
        decisions = tuple(sorted((time_slot(o.time), o.battery_kw, o.generator_kw) for o in overrides))
        key = ("override", day, weight_key(w), decisions)

        def compute() -> tuple[CachedPlan, Any]:
            before, _ = self.get_plan(day, w)
            fixes = []
            b = before.context.assets.battery
            for t, battery, generator in decisions:
                if battery is not None and not -b.max_discharge_kw <= battery <= b.max_charge_kw:
                    raise InvalidOverrides(
                        f"battery_kw must be in [-{b.max_discharge_kw:g}, {b.max_charge_kw:g}] kW (slot {t})"
                    )
                gen = before.context.assets.generator
                if generator is not None and generator != 0 and not gen.min_kw <= generator <= gen.rated_kw:
                    raise InvalidOverrides(
                        f"generator_kw must be 0 or in [{gen.min_kw:g}, {gen.rated_kw:g}] kW (slot {t})"
                    )
                fixes.append(
                    SlotFix(
                        t,
                        generator_kw=generator,
                        battery_charge_kw=None if battery is None else max(0, battery),
                        battery_discharge_kw=None if battery is None else max(0, -battery),
                    )
                )
            fixed = SlotFixes(tuple(fixes))
            try:
                fixed.validate(before.context.assets)
            except ValueError as exc:
                raise InvalidOverrides(str(exc)) from None
            after = self._replan(before, before.context.forecast, fixed)
            return after, before.context.plan.totals

        (after, baseline), cached = self._whatif(key, compute, self.override_limiter, client)
        response = self._whatif_warnings(self._response(day, w, after, cached))
        return response.model_copy(
            update={
                "override": OverrideImpact(
                    applied=overrides,
                    baseline=baseline,
                    delta=TotalsDelta.between(baseline, after.context.plan.totals),
                    feasible=is_safe(after.context.plan),
                )
            }
        )

    def cloud_response(self, req: CloudRequest, client: str | None = None) -> CloudResponse:
        """Simulated cloud event on a historical replay day, not a change to observed data.

        All solar quantiles drop equally in affected slots (duration rounds up to full slots and clips
        at midnight). The executed controllable prefix is frozen, so battery SoC carries over through
        the original dynamics. Risk may request more reserve without releasing those decisions.
        """
        key = ("cloud", req.date, weight_key(req.w), req.time, req.drop_pct, req.duration_minutes)

        def compute() -> tuple[CachedPlan, bool, CachedPlan, int, int]:
            before, cached = self.get_plan(req.date, req.w)
            start = time_slot(req.time)
            end = min(n_slots(), start + math.ceil(req.duration_minutes / slot_minutes()))
            forecast = before.context.forecast
            solar = forecast.solar.model_copy(
                update={
                    q: [
                        v * (1 - req.drop_pct / 100) if start <= t < end else v
                        for t, v in enumerate(getattr(forecast.solar, q))
                    ]
                    for q in ("p10_kw", "p50_kw", "p90_kw")
                }
            )
            forecast = forecast.model_copy(update={"solar": solar})
            fixes = SlotFixes.prefix(before.context.plan.schedule, start)
            after = self._replan(before, forecast, fixes)
            plan = after.context.plan.model_copy(
                update={
                    "notes": [*after.context.plan.notes, SIMULATED_CLOUD],
                }
            )
            after = replace(after, context=replace(after.context, plan=plan))
            return before, cached, after, start, end

        (before, before_cached, after, start, end), cached = self._whatif(
            key, compute, self.cloud_limiter, client
        )
        old = self._whatif_warnings(self._response(req.date, req.w, before, before_cached))
        new = self._whatif_warnings(self._response(req.date, req.w, after, cached))
        old = old.model_copy(update={"banners": [*old.banners, SIMULATED_CLOUD]})
        new = new.model_copy(update={"banners": [*new.banners, SIMULATED_CLOUD]})
        return CloudResponse(
            before=old,
            after=new,
            event=CloudEvent(**req.model_dump()),
            affected_slots=AffectedSlots(start=start, end=end),
            delta=TotalsDelta.between(before.context.plan.totals, after.context.plan.totals),
            replan_seconds=after.seconds,
            safe=new.safe,
            risk_ok=new.risk_ok,
            banners=list(dict.fromkeys([*old.banners, *new.banners])),
        )

    # ------------------------------------------------------------------ Pareto

    def pareto_response(self, day: date, client: str | None = None) -> ParetoResponse:
        """Single-flight per day. Never hold the cache lock while waiting or calling get_plan.

        A client is supplied by the route; background warm-up bypasses the request limiter.
        Cache-hit point metadata describes the original computation, including plan_cached.
        """
        t0 = time.perf_counter()

        def served(body: ParetoResponse, cached: bool) -> ParetoResponse:
            return body.model_copy(
                deep=True,
                update={
                    "cached": cached,
                    "solve_seconds": time.perf_counter() - t0,
                },
            )

        with self._cache_lock:
            entry = self._pareto_cache.get(day)
            if entry is not None:
                self._pareto_cache.move_to_end(day)
                return served(entry, True)
            fut = self._pareto_inflight.get(day)
            owner = fut is None
            if owner:
                if client is not None:
                    self.pareto_limiter.hit(client)
                fut = Future()
                self._pareto_inflight[day] = fut
        if not owner:
            return served(fut.result(), True)
        try:
            body = self._compute_pareto(day)
        except BaseException as exc:
            with self._cache_lock:
                self._pareto_inflight.pop(day, None)
            fut.set_exception(exc)
            raise
        with self._cache_lock:
            self._pareto_cache[day] = body
            while len(self._pareto_cache) > PARETO_CACHE_SIZE:
                self._pareto_cache.popitem(last=False)
            self._pareto_inflight.pop(day, None)
        fut.set_result(body)
        return served(body, False)

    def _compute_pareto(self, day: date) -> ParetoResponse:
        points: list[ParetoPoint] = []
        representatives: list[ParetoPoint] = []
        warnings: list[str] = []
        first = None

        def close(a: float, b: float) -> bool:
            return math.isclose(a, b, rel_tol=PARETO_REL_TOL, abs_tol=0.0)

        for w in PARETO_WEIGHTS:
            # plan_response calls get_plan, sharing exactly the slider's plans and metadata.
            response = self.plan_response(day, w)
            if first is None:
                first = response
            warnings.extend(b for b in response.banners if b not in warnings)
            plan, totals = response.plan, response.plan.totals
            representative = next(
                (
                    p
                    for p in representatives
                    if close(p.cost_bdt, totals.cost_bdt) and close(p.co2_kg, totals.co2_kg)
                ),
                None,
            )
            point = ParetoPoint(
                weight=response.weight,
                cost_bdt=totals.cost_bdt,
                co2_kg=totals.co2_kg,
                generator_hours=totals.generator_hours,
                diesel_litres=totals.diesel_litres,
                status=plan.status,
                safe=response.safe,
                risk_ok=response.risk_ok,
                replans=plan.risk.replans,
                efficient=False,
                group=representative.group if representative is not None else len(representatives),
                same_as_weight=representative.weight if representative is not None else None,
                plan_cached=response.cached,
            )
            points.append(point)
            if representative is None:
                representatives.append(point)

        safe = [p for p in points if p.safe]
        for p in safe:
            p.efficient = not any(
                (q.cost_bdt <= p.cost_bdt or close(q.cost_bdt, p.cost_bdt))
                and (q.co2_kg <= p.co2_kg or close(q.co2_kg, p.co2_kg))
                and (
                    (q.cost_bdt < p.cost_bdt and not close(q.cost_bdt, p.cost_bdt))
                    or (q.co2_kg < p.co2_kg and not close(q.co2_kg, p.co2_kg))
                )
                for q in safe
            )
        efficient_groups: dict[int, ParetoPoint] = {}
        for p in points:
            if p.efficient:
                efficient_groups.setdefault(p.group, p)
        if not safe:
            warnings.append("No safe plans were found; the Pareto frontier is empty.")
        return ParetoResponse(
            date=day,
            points=points,
            frontier=sorted(efficient_groups.values(), key=lambda p: p.cost_bdt),
            forecast_fallback=first.forecast_fallback,
            signal_fallback=first.signal_fallback,
            banners=warnings,
            forecast_source=first.forecast_source,
            cached=False,
            computed_at=datetime.now(site_tz()),
            solve_seconds=0.0,
            distinct_points=len({p.group for p in safe}),
        )

    # ------------------------------------------------------------------ days

    def _day_signals(self, day: date) -> tuple[Any, str | None]:
        if day not in self._signals_by_date:
            with self._plan_lock:
                agent = self._agents_locked()["signal_agent"]
                start = self.start_of(day)
                if hasattr(agent, "run_with_note"):
                    result = agent.run_with_note(start)
                else:
                    result = (agent.run(start), None)
            self._signals_by_date[day] = result
        return self._signals_by_date[day]

    def day_list(self) -> list[tuple[date, str, bool, bool]]:
        """``(date, label, demo, dynamic)`` for every demo day plus tomorrow."""
        out = [(d.date, d.label, d.demo, False) for d in self.demo_days()]
        tomorrow = last_plan_day()
        if tomorrow not in {d for d, *_ in out}:
            out.append((tomorrow, TOMORROW_LABEL, False, True))
        return out

    def days(self) -> DaysResponse:
        from energy_brain.config import load_site_config
        from energy_brain.data.synthetic_load import is_bangladesh_holiday

        cfg = load_site_config()
        off = set(cfg.load.weekend_days)
        processed, models = self.processed_data(), self.models_present()
        bt = self.backtest()
        bt_dates = {d["date"] for d in bt.get("days", [])} if bt else set()
        infos = []
        for day, label, demo, dynamic in self.day_list():
            signals, note = self._day_signals(day)
            weekly_off = WEEKDAY_NAMES[day.weekday()] in off
            holiday = bool(is_bangladesh_holiday(day))
            planned = self._tier_by_date.get(day)
            infos.append(
                DayInfo(
                    date=day,
                    weekday=WEEKDAYS[day.weekday()],
                    workday=not (weekly_off or holiday),
                    weekly_off=weekly_off,
                    holiday=holiday,
                    outage=any(signals.outage),
                    outage_slots=sum(signals.outage),
                    outage_blocks=outage_blocks(signals.start, list(signals.outage)),
                    label=label,
                    demo=demo,
                    dynamic=dynamic,
                    forecast_tier=planned or predicted_tier(day, processed, models),
                    forecast_tier_source="planned" if planned else "predicted",
                    signal_fallback=bool(note),
                    in_backtest=day.isoformat() in bt_dates,
                )
            )
        return DaysResponse(first_day=FIRST_PLAN_DAY, last_day=last_plan_day(), days=infos)

    # ------------------------------------------------------------------ warm-up

    def warmup_keys(self) -> list[tuple[date, float]]:
        days = sorted(self.day_list(), key=lambda d: (not d[2], d[3]))  # demo day first, tomorrow last
        return [(d, w) for w in self.settings.warmup_weights for d, *_ in days]

    def start_warmup(self) -> None:
        if self._warmup_thread is not None:
            return
        self._warmup["state"] = "pending"
        self._warmup_thread = threading.Thread(target=self._run_warmup, name="eb-warmup", daemon=True)
        self._warmup_thread.start()

    def _run_warmup(self) -> None:
        try:
            keys = self.warmup_keys()
        except Exception:
            logger.exception("warm-up: could not read the demo days")
            self._warmup.update(state="stopped", errors=self._warmup["errors"] + 1)
            return
        self._warmup.update(state="running", total=len(keys))
        t0 = time.perf_counter()
        for day, w in keys:
            if self._stop.is_set():
                self._warmup["state"] = "stopped"
                return
            try:
                self.get_plan(day, w)
                self._warmup["planned"] += 1
            except Exception:
                self._warmup["errors"] += 1
                logger.exception("warm-up: planning %s w=%s failed", day, w)
        # Keep existing warm-up plan counters' meaning; Pareto failures still count as errors.
        for day in dict.fromkeys(day for day, _ in keys):
            if self._stop.is_set():
                self._warmup["state"] = "stopped"
                return
            try:
                self.pareto_response(day)
            except Exception:
                self._warmup["errors"] += 1
                logger.exception("warm-up: Pareto %s failed", day)
        self._warmup["state"] = "done"
        logger.info("warm-up: %d plans in %.1f s", self._warmup["planned"], time.perf_counter() - t0)

    def shutdown(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._warmup_thread is not None:
            self._warmup_thread.join(timeout_s)

    # ------------------------------------------------------------------ explain

    def explain(self, req: ExplainRequest, agent: ExplainerAgent) -> ExplainResponse:
        """Answer ``req.question`` about the plan for ``(req.date, req.w)``; see ``demo_answers``."""
        entry, _ = self.get_plan(req.date, req.w)
        ctx = entry.context
        mode = self.settings.explain_cache
        stored = None
        if mode != "off":
            stored = find_stored(self.stored_answers(), req.date, entry.weight, req.question)
        meta = {"date": req.date, "weight": entry.weight, "weight_requested": req.w}

        if stored is not None and mode == "prefer":
            served = serve_stored(stored, ctx)
            if served is not None:
                return self._cached_response(served, stored, meta, live_error=None)

        if not self._explain_slots.acquire(timeout=self.settings.explain_wait_s):
            raise ServiceBusy()
        try:
            live = agent.ask(req.question, ctx)
        finally:
            self._explain_slots.release()

        if live.error and stored is not None:
            served = serve_stored(stored, ctx)
            if served is not None:
                return self._cached_response(served, stored, meta, live_error=live.error)
        return ExplainResponse(**live.model_dump(), **meta)

    @staticmethod
    def _cached_response(
        served: ExplainerAnswer, stored: StoredAnswer, meta: dict, live_error: str | None
    ) -> ExplainResponse:
        return ExplainResponse(
            **served.model_dump(),
            **meta,
            cached=True,
            cached_recorded_at=stored.recorded_at,
            cached_model=stored.model,
            live_error=live_error,
        )

    # ------------------------------------------------------------------ health

    def cache_stats(self) -> CacheStats:
        with self._cache_lock:
            return CacheStats(
                size=len(self._cache),
                max_size=self.settings.cache_size,
                hits=self.hits,
                misses=self.misses,
                inflight=len(self._inflight),
            )

    def health(self) -> HealthResponse:
        client = GroqClient()
        try:
            model, key = client.settings()
            key_set = bool(key)
        except LLMError:
            model, key_set = client.model, False
        return HealthResponse(
            status="ok",
            version=__version__,
            processed_data=self.processed_data(),
            models=self.models_present(),
            sample_data=_files_present(self.settings.data_dir / "sample", SAMPLE_FILES),
            backtest=self.settings.backtest_path.exists(),
            demo_answers=len(self.stored_answers()),
            llm_key_configured=key_set,
            transcription_available=key_set,
            llm_model=model,
            explain_cache=self.settings.explain_cache,
            cache=self.cache_stats(),
            warmup=WarmupStatus(enabled=self.settings.warmup, **self._warmup),
        )


__all__ = ["CachedPlan", "PlanService", "RateLimited", "RateLimiter", "ServiceBusy"]
