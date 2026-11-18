"""Service layer of the dashboard API (#21). Routes stay thin; everything stateful lives here.

**Plans.** A plan is a ``PlanContext`` (``explainer.PlanContext.from_agents``: the plan plus the Forecast,
Signal and Asset outputs it was solved on), so POST /plan and POST /explain describe the same object. Plans
are cached in an LRU keyed by ``(date, w rounded to 0.01)`` (``ApiSettings.cache_size`` entries).

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
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
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
    CacheStats,
    DayInfo,
    DaysResponse,
    ExplainRequest,
    ExplainResponse,
    FallbackInfo,
    HealthResponse,
    PlanResponse,
    WarmupStatus,
    last_plan_day,
    weight_key,
)
from energy_brain.api.settings import ApiSettings
from energy_brain.explainer import ExplainerAgent, ExplainerAnswer, GroqClient, LLMError, PlanContext
from energy_brain.explainer.agent import banners
from energy_brain.planner import is_safe
from energy_brain.timeaxis import site_tz

logger = logging.getLogger(__name__)

PROCESSED_FILES = ("load.parquet", "pv.parquet", "weather.parquet")
SAMPLE_FILES = ("load_sample.parquet", "pv_sample.parquet", "weather_sample.parquet")
MODEL_FILES = tuple(f"{k}_p{q}.txt" for k in ("solar", "load") for q in (10, 50, 90))
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

_UNSET = object()


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
        from energy_brain.forecast import parse_note as parse_forecast_note
        from energy_brain.signals import parse_note as parse_signal_note

        entry, cached = self.get_plan(day, w)
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
            llm_model=model,
            explain_cache=self.settings.explain_cache,
            cache=self.cache_stats(),
            warmup=WarmupStatus(enabled=self.settings.warmup, **self._warmup),
        )


__all__ = ["CachedPlan", "PlanService", "RateLimited", "RateLimiter", "ServiceBusy"]
