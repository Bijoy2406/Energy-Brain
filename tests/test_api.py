"""Dashboard API tests (#21). No API key, no network, no data/processed and no models: the agents read a copy
of the committed data/sample from a temporary directory (forecast fallback, generated outage calendar), the
backtest is a tiny injected report, and the Explainer uses FakeLLMClient through a dependency override."""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from energy_brain.api.app import create_app
from energy_brain.api.days import load_demo_days
from energy_brain.api.demo_answers import build_entry, write_demo_answers
from energy_brain.api.deps import get_explainer
from energy_brain.api.models import (
    BacktestDaysResponse,
    BacktestSummary,
    DaysResponse,
    ExplainResponse,
    HealthResponse,
    PlanResponse,
    last_plan_day,
)
from energy_brain.api.service import (
    PARETO_WEIGHTS,
    PlanService,
    RateLimited,
    RateLimiter,
    default_agent_factory,
)
from energy_brain.api.settings import ApiSettings
from energy_brain.explainer import ExplainerAgent, FakeLLMClient, LLMError, LLMResponse, ToolCall
from energy_brain.planner import UNSAFE_PREFIX
from energy_brain.stubs import StubPlanner

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
DEMO_DAY = "2024-07-02"  # outage 18:00-20:00 in the outage calendar
SECRET = "gsk_" + "APITESTSECRET0123456789"
QUESTION = "What will today cost?"


# ---------------------------------------------------------------------------------------------- helpers


def tool(name: str, args: dict | None = None, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(call_id, name, json.dumps(args or {}))])


def _last_tool_result(messages: list[dict]) -> dict:
    return json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])


def grounded_script() -> list:
    """get_plan_summary, then an answer quoting its cost (traceable)."""

    def answer(messages):
        cost = _last_tool_result(messages)["totals"]["cost_bdt"]
        return LLMResponse(content=f"The plan costs {cost} BDT for the day.")

    return [tool("get_plan_summary"), answer]


class AlwaysRefuse:
    model = "fake-refuse"

    def complete(self, messages, tools, *, timeout):
        return tool("refuse", {"reason": "off_topic"})


def agent_with(script) -> ExplainerAgent:
    return ExplainerAgent(FakeLLMClient(script), sleep=lambda s: None)


def tiny_backtest() -> dict:
    """Two days in the #20 report format (placeholder numbers)."""

    def methods(d):
        rows = [
            {
                "date": d,
                "method": "optimiser",
                "weight": w,
                "adjusted_cost_bdt": 100.0 + w,
                "adjusted_co2_kg": 10.0,
                "cost_bdt": 100.0 + w,
                "co2_kg": 10.0,
            }
            for w in (0.5, 1.0, 0.0)
        ]
        for name, cost in (("baseline_solar_first", 120.0), ("baseline_time_of_use", 110.0)):
            rows.append(
                {
                    "date": d,
                    "method": name,
                    "weight": None,
                    "adjusted_cost_bdt": cost,
                    "adjusted_co2_kg": 11.0,
                    "cost_bdt": cost,
                    "co2_kg": 11.0,
                }
            )
        return rows

    return {
        "report": "Energy Brain backtest (#20)",
        "schema_version": 1,
        "caveats": ["Synthetic load (placeholder caveat).", "Observed weather as forecast input (tier-0)."],
        "headline": {
            "weight": 0.5,
            "baseline_names": {
                "baseline_solar_first": "solar-first rule",
                "baseline_time_of_use": "ToU rule",
            },
            "lines": ["Placeholder headline line."],
        },
        "summary": {"headline_weight": 0.5, "days": {"requested": 2}, "by_weight": {}, "forecast": {}},
        "config": {
            "weights": [0.5, 1.0, 0.0],
            "data_sha256": {"x": "abc"},
            "models_sha256": {},
            "first_day": "x",
        },
        "run": {"runtime_seconds": 1.0},
        "days": [
            {
                "date": d,
                "weekday": wd,
                "workday": True,
                "outage_slots": n,
                "forecast_fallback_used": False,
                "included": True,
                "excluded_reason": None,
                "methods": methods(d),
            }
            for d, wd, n in (("2024-07-01", "Mon", 0), (DEMO_DAY, "Tue", 8))
        ],
    }


@pytest.fixture(scope="module")
def dirs(tmp_path_factory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("api")
    data = base / "data"
    shutil.copytree(SAMPLE_DIR, data / "sample")  # no data/processed
    (base / "models").mkdir()  # no trained models
    report = base / "backtest.json"
    report.write_text(json.dumps(tiny_backtest()), encoding="utf-8")
    days = base / "demo_days.yaml"
    days.write_text(
        f'days:\n  - {{date: "{DEMO_DAY}", label: "Demo day", demo: true}}\n'
        '  - {date: "2024-06-05", label: "Sample week day"}\n',
        encoding="utf-8",
    )
    return {"base": base, "data": data, "models": base / "models", "backtest": report, "days": days}


def settings_for(dirs, **kw) -> ApiSettings:
    values = {
        "warmup": False,
        "data_dir": dirs["data"],
        "models_dir": dirs["models"],
        "backtest_path": dirs["backtest"],
        "demo_days_path": dirs["days"],
        "demo_answers_path": dirs["base"] / "no_answers.json",
    }
    values.update(kw)
    return ApiSettings(**values)


def client_for(service: PlanService, explainer: ExplainerAgent | None = None, **kw) -> TestClient:
    app = create_app(service)
    # Guard: a test that reaches the LLM without scripting it fails (FakeLLMClient script exhausted).
    app.dependency_overrides[get_explainer] = lambda: explainer or agent_with([])
    return TestClient(app, **kw)


@pytest.fixture(scope="module")
def shared(dirs):
    """One service for read-only plan tests (its cache is reused across them); no /explain rate limit."""
    service = PlanService(settings_for(dirs, explain_per_ip_per_min=10_000, explain_total_per_min=10_000))
    with client_for(service) as c:
        yield service, c


def no_leak(text: str) -> None:
    assert "Traceback" not in text and 'File "' not in text
    assert SECRET not in text


# ---------------------------------------------------------------------------------------------- health, docs


def test_health_reports_files_and_never_the_key(shared, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    _, c = shared
    r = c.get("/health")
    assert r.status_code == 200
    h = HealthResponse.model_validate(r.json())
    assert h.processed_data is False and h.models is False and h.sample_data is True and h.backtest is True
    assert h.llm_key_configured is True and h.explain_cache == "fallback"
    assert h.warmup.enabled is False and h.warmup.state == "off"
    no_leak(r.text)


def test_app_creation_reads_nothing(tmp_path):
    missing = tmp_path / "nothing"
    s = ApiSettings(
        warmup=False,
        data_dir=missing,
        models_dir=missing,
        backtest_path=missing / "b.json",
        demo_days_path=missing / "d.yaml",
        demo_answers_path=missing / "a.json",
    )
    with client_for(PlanService(s)) as c:
        h = c.get("/health").json()
    assert h["processed_data"] is False and h["sample_data"] is False and h["demo_answers"] == 0


def test_docs_and_openapi_work(shared):
    _, c = shared
    assert c.get("/docs").status_code == 200
    r = c.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in ("/days", "/plan", "/backtest", "/backtest/days", "/explain", "/health"):
        assert p in paths


def test_exported_openapi_file_is_current():
    exported = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    current = create_app(settings=ApiSettings(warmup=False)).openapi()
    assert exported == current, "docs/openapi.json is stale: run python -m energy_brain.api export-openapi"


# ---------------------------------------------------------------------------------------------- days


def test_days_lists_demo_days_plus_tomorrow(shared):
    _, c = shared
    r = c.get("/days")
    assert r.status_code == 200
    body = DaysResponse.model_validate(r.json())
    assert DaysResponse.model_validate_json(body.model_dump_json()) == body
    assert body.first_day == date(2022, 1, 1) and body.last_day == last_plan_day()
    by_date = {d.date.isoformat(): d for d in body.days}
    demo = by_date[DEMO_DAY]
    assert demo.demo and demo.weekday == "Tue" and demo.workday
    assert [(b.start, b.end) for b in demo.outage_blocks] == [("18:00", "20:00")]
    assert demo.outage_slots == 8 and demo.forecast_tier == "fallback"  # no models here
    assert not demo.signal_fallback
    tomorrow = body.days[-1]
    assert tomorrow.dynamic and tomorrow.date == last_plan_day() and tomorrow.signal_fallback
    assert tomorrow.forecast_tier == "fallback"


def test_real_demo_days_file_is_valid():
    days = load_demo_days(ROOT / "config" / "demo_days.yaml")
    assert len(days) == 7 and sum(d.demo for d in days) == 1
    assert next(d for d in days if d.demo).date.isoformat() == DEMO_DAY


@pytest.mark.parametrize(
    "text, match",
    [
        ('days:\n  - {date: "2024-01-01", label: a}\n  - {date: "2024-01-01", label: b}\n', "duplicate"),
        (
            'days:\n  - {date: "2024-01-01", label: a, demo: true}\n'
            '  - {date: "2024-01-02", label: b, demo: true}\n',
            "at most one",
        ),
        ('days:\n  - {date: "2024-01-01", label: "  "}\n', "label"),
        ('days:\n  - {date: "2021-12-31", label: a}\n', "before"),
        ('days:\n  - {date: "2024-01-01", label: a, color: red}\n', "color"),
        ("days: []\n", "days"),
    ],
)
def test_demo_days_file_is_validated(tmp_path, text, match):
    p = tmp_path / "d.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_demo_days(p)


# ---------------------------------------------------------------------------------------------- plan


def test_plan_returns_plan_inputs_and_flags(shared):
    _, c = shared
    r = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5})
    assert r.status_code == 200
    body = PlanResponse.model_validate(r.json())
    assert PlanResponse.model_validate_json(body.model_dump_json()) == body
    assert body.weight == 0.5 and body.safe and body.plan.weight == 0.5
    assert body.forecast_fallback.used and body.forecast_source == "sample"
    assert body.forecast_fallback.fields["method"] in ("persistence", "analog_day")
    assert any("fallback" in b for b in body.banners)
    n = len(body.signals.outage)
    assert len(body.plan.schedule.grid_import_kw) == n == len(body.forecast.solar.p50_kw)
    assert body.plan.start == body.forecast.start == body.signals.start == body.assets.start


def test_plan_has_no_grid_import_during_outage(shared):
    _, c = shared
    body = PlanResponse.model_validate(c.post("/plan", json={"date": DEMO_DAY, "w": 0.5}).json())
    outage = body.signals.outage
    assert sum(outage) == 8
    assert all(g == 0.0 for g, o in zip(body.plan.schedule.grid_import_kw, outage, strict=True) if o)


def test_weight_extremes_trade_cost_against_co2(shared):
    _, c = shared
    cheap = PlanResponse.model_validate(c.post("/plan", json={"date": DEMO_DAY, "w": 1}).json())
    clean = PlanResponse.model_validate(c.post("/plan", json={"date": DEMO_DAY, "w": 0}).json())
    assert cheap.plan.totals.cost_bdt <= clean.plan.totals.cost_bdt + 1e-6
    assert clean.plan.totals.co2_kg <= cheap.plan.totals.co2_kg + 1e-6
    assert cheap.plan.schedule != clean.plan.schedule


def test_weight_is_rounded_for_solving(shared):
    _, c = shared
    body = c.post("/plan", json={"date": DEMO_DAY, "w": 0.503}).json()
    assert body["weight"] == 0.5 and body["weight_requested"] == 0.503 and body["plan"]["weight"] == 0.5


@pytest.mark.parametrize(
    "payload, needle",
    [
        ({"date": "2024-13-01", "w": 0.5}, "date"),
        ({"date": "07/02/2024", "w": 0.5}, "date"),
        ({"date": "2021-12-31", "w": 0.5}, "date must be between 2022-01-01"),
        ({"date": (last_plan_day() + timedelta(days=1)).isoformat(), "w": 0.5}, "(tomorrow)"),
        ({"date": DEMO_DAY, "w": -0.1}, "greater than or equal to 0"),
        ({"date": DEMO_DAY, "w": 1.5}, "less than or equal to 1"),
        ({"date": DEMO_DAY, "w": "abc"}, "valid number"),
        ({"date": DEMO_DAY}, "Field required"),
        ({"date": DEMO_DAY, "w": 0.5, "extra": 1}, "Extra inputs"),
    ],
)
def test_plan_rejects_bad_input_with_clear_422(shared, payload, needle):
    _, c = shared
    r = c.post("/plan", json=payload)
    assert r.status_code == 422
    assert needle in r.text
    no_leak(r.text)


def test_cache_hit_returns_same_plan_and_is_faster(dirs):
    service = PlanService(settings_for(dirs))
    with client_for(service) as c:
        t0 = time.perf_counter()
        first = c.post("/plan", json={"date": "2024-06-05", "w": 0.5}).json()
        t1 = time.perf_counter()
        second = c.post("/plan", json={"date": "2024-06-05", "w": 0.5}).json()
        t2 = time.perf_counter()
        stats = c.get("/health").json()["cache"]
    assert first["cached"] is False and second["cached"] is True
    assert first["plan"] == second["plan"] and first["computed_at"] == second["computed_at"]
    assert stats["hits"] == 1 and stats["misses"] == 1 and stats["size"] == 1
    assert t2 - t1 < t1 - t0


def test_cache_is_bounded_lru(dirs):
    service = PlanService(settings_for(dirs, cache_size=2))
    day = date(2024, 6, 5)
    for w in (0.1, 0.2, 0.3):
        service.get_plan(day, w)
    assert service.cache_stats().size == 2
    _, cached = service.get_plan(day, 0.1)  # evicted (least recently used)
    assert not cached
    _, cached = service.get_plan(day, 0.3)
    assert cached


def test_concurrent_requests_for_one_key_solve_once(dirs):
    service = PlanService(settings_for(dirs))
    results = []

    def go():
        results.append(service.get_plan(date(2024, 6, 6), 0.5)[0])

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats = service.cache_stats()
    assert stats.misses == 1 and stats.hits == 5 and stats.inflight == 0
    assert all(r is results[0] for r in results)


class UnsafePlanner(StubPlanner):
    def plan(self, *a, **k):
        r = super().plan(*a, **k)
        return r.model_copy(
            update={"status": "infeasible", "fallback_used": True, "notes": [f"{UNSAFE_PREFIX}: test day"]}
        )


def test_unsafe_plan_is_200_with_safe_false_and_banner(dirs):
    base = default_agent_factory(settings_for(dirs))

    def factory():
        return {**base(), "planner": UnsafePlanner()}

    service = PlanService(settings_for(dirs), agent_factory=factory)
    with client_for(service) as c:
        r = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5})
    assert r.status_code == 200
    body = PlanResponse.model_validate(r.json())
    assert body.safe is False and body.risk_ok is False and body.plan.status == "infeasible"
    assert body.banners[0].startswith("WARNING: this plan is NOT SAFE TO EXECUTE")


def test_no_forecast_possible_gives_503(dirs, tmp_path):
    service = PlanService(settings_for(dirs, data_dir=tmp_path / "empty"))
    with client_for(service) as c:
        r = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "forecast_unavailable"
    no_leak(r.text)


def test_internal_error_body_has_no_traceback_or_secret(dirs):
    def broken():
        raise RuntimeError(f"boom with {SECRET}")

    service = PlanService(settings_for(dirs), agent_factory=broken)
    with client_for(service, raise_server_exceptions=False) as c:
        r = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5})
    assert r.status_code == 500
    assert r.json() == {
        "detail": {
            "code": "internal_error",
            "message": "Internal server error; the details are in the server log.",
        }
    }
    no_leak(r.text)
    assert "boom" not in r.text


# ---------------------------------------------------------------------------------------------- explain


def ask(c: TestClient, question: str = QUESTION, w: float = 0.5):
    return c.post("/explain", json={"date": DEMO_DAY, "w": w, "question": question})


def test_explain_grounded_answer(shared, dirs):
    service, _ = shared
    with client_for(service, agent_with(grounded_script())) as c:
        r = ask(c)
    assert r.status_code == 200
    body = ExplainResponse.model_validate(r.json())
    assert ExplainResponse.model_validate_json(body.model_dump_json()) == body
    assert body.verified and body.error is None and body.cached is False
    assert body.weight == 0.5 and body.tool_calls[0].name == "get_plan_summary"
    assert body.provenance and body.banners  # fallback banners from the plan come first
    assert body.answer.startswith(body.banners[0])


def test_explain_refusal(shared):
    service, _ = shared
    with client_for(service, ExplainerAgent(AlwaysRefuse())) as c:
        r = ask(c, "Who won the cricket match?")
    assert r.status_code == 200
    assert r.json()["refusal_reason"] == "off_topic" and r.json()["verified"] is False


@pytest.mark.parametrize("kind", ["network", "no_key", "rate_limit", "timeout", "blocked_model"])
def test_explain_llm_errors_are_200_with_error_set(shared, kind):
    service, _ = shared
    # rate_limit is retried once by the Explainer (after retry-after), so script the error twice
    with client_for(service, agent_with([LLMError(kind, f"failure {SECRET}")] * 2)) as c:
        r = ask(c)
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == kind and body["verified"] is False and body["cached"] is False
    no_leak(r.text)


@pytest.mark.parametrize("question, needle", [("   ", "must not be empty"), ("x" * 501, "too long")])
def test_explain_rejects_bad_questions(shared, question, needle):
    _, c = shared
    r = ask(c, question)
    assert r.status_code == 422 and needle in r.text


def test_explain_rate_limit_is_429_with_clear_body(dirs):
    service = PlanService(settings_for(dirs, explain_per_ip_per_min=5))
    with client_for(service, ExplainerAgent(AlwaysRefuse())) as c:
        codes = [ask(c).status_code for _ in range(6)]
        r = ask(c)
    assert codes == [200] * 5 + [429]
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["code"] == "rate_limited" and "Too many questions" in detail["message"]
    assert detail["retry_after_s"] > 0 and int(r.headers["Retry-After"]) >= 1


def test_rate_limiter_per_client_total_and_window():
    now = [0.0]
    rl = RateLimiter(per_key=2, total=3, window_s=60, clock=lambda: now[0])
    rl.hit("a")
    rl.hit("a")
    with pytest.raises(RateLimited) as e:
        rl.hit("a")
    assert e.value.scope == "client" and e.value.retry_after_s == pytest.approx(60)
    rl.hit("b")
    with pytest.raises(RateLimited) as e:
        rl.hit("c")
    assert e.value.scope == "total"
    now[0] = 61.0
    rl.hit("a")
    rl.hit("c")


# ------------------------------------------------------------------------------------- stored demo answers


@pytest.fixture(scope="module")
def stored_file(shared, dirs) -> Path:
    """A stored answer recorded (with the fake LLM) on the plan the tests serve."""
    service, _ = shared
    ctx = service.get_plan(date.fromisoformat(DEMO_DAY), 0.5)[0].context
    ans = agent_with(grounded_script()).ask("What will today cost?", ctx)
    assert ans.verified
    path = dirs["base"] / "answers.json"
    write_demo_answers(
        path,
        day=date.fromisoformat(DEMO_DAY),
        weight=0.5,
        recorded_at="2026-11-18T10:00:00+06:00",
        model="fake-model",
        entries=[build_entry("cost", ans)],
    )
    return path


def stored_service(shared, dirs, path: Path, mode: str = "fallback") -> PlanService:
    """A service sharing the module's plan cache, with stored answers from ``path``."""
    base, _ = shared
    service = PlanService(
        settings_for(dirs, demo_answers_path=path, explain_cache=mode, explain_per_ip_per_min=10_000)
    )
    service._cache = base._cache  # same plans; avoids re-solving
    return service


def test_stored_answer_served_when_live_fails_and_question_matches(shared, dirs, stored_file):
    service = stored_service(shared, dirs, stored_file)
    with client_for(service, agent_with([LLMError("network", "down")])) as c:
        r = ask(c, "  what will TODAY cost?  ")
    body = ExplainResponse.model_validate(r.json())
    assert body.cached and body.live_error == "network" and body.error is None and body.verified
    assert body.cached_model == "fake-model" and body.cached_recorded_at == "2026-11-18T10:00:00+06:00"
    assert body.banners and body.answer.startswith(body.banners[0]) and body.provenance


def test_stored_answer_not_used_when_live_succeeds(shared, dirs, stored_file):
    service = stored_service(shared, dirs, stored_file)
    with client_for(service, agent_with(grounded_script())) as c:
        body = ask(c).json()
    assert body["cached"] is False and body["verified"] is True and body["live_error"] is None


@pytest.mark.parametrize(
    "question, w", [("What will today cost?!", 0.5), ("What does today cost?", 0.5), (QUESTION, 0.6)]
)
def test_stored_answer_needs_exact_question_and_weight(shared, dirs, stored_file, question, w):
    service = stored_service(shared, dirs, stored_file)
    with client_for(service, agent_with([LLMError("network", "down")])) as c:
        body = ask(c, question, w).json()
    assert body["cached"] is False and body["error"] == "network"


def _tampered(stored_file: Path, tmp_path: Path, fn) -> Path:
    raw = json.loads(stored_file.read_text(encoding="utf-8"))
    fn(raw["answers"][0])
    out = tmp_path / "tampered.json"
    out.write_text(json.dumps(raw), encoding="utf-8")
    return out


def test_changed_facts_fall_through_to_live(shared, dirs, stored_file, tmp_path):
    def change_cost(item):  # the plan the answer was recorded on cost something else
        item["answer"]["tool_calls"][0]["result"]["totals"]["cost_bdt"] += 1000.0

    service = stored_service(shared, dirs, _tampered(stored_file, tmp_path, change_cost))
    with client_for(service, agent_with([LLMError("network", "down")])) as c:
        body = ask(c).json()
    assert body["cached"] is False and body["error"] == "network"


def test_unverifiable_stored_text_falls_through_to_live(shared, dirs, stored_file, tmp_path):
    def invent(item):
        item["body"] = "The plan costs 123456.7 BDT for the day."

    service = stored_service(shared, dirs, _tampered(stored_file, tmp_path, invent))
    with client_for(service, agent_with([LLMError("network", "down")])) as c:
        body = ask(c).json()
    assert body["cached"] is False and body["error"] == "network"


def test_prefer_mode_serves_stored_without_calling_the_llm(shared, dirs, stored_file):
    service = stored_service(shared, dirs, stored_file, mode="prefer")
    fake = FakeLLMClient([])
    with client_for(service, ExplainerAgent(fake)) as c:
        body = ask(c).json()
    assert body["cached"] is True and body["live_error"] is None and fake.requests == []


def test_off_mode_never_serves_stored(shared, dirs, stored_file):
    service = stored_service(shared, dirs, stored_file, mode="off")
    with client_for(service, agent_with([LLMError("network", "down")])) as c:
        body = ask(c).json()
    assert body["cached"] is False and body["error"] == "network"


# ---------------------------------------------------------------------------------------------- backtest


def test_backtest_summary(shared):
    _, c = shared
    r = c.get("/backtest")
    assert r.status_code == 200
    body = BacktestSummary.model_validate(r.json())
    assert body.caveats and body.headline.lines and body.headline.weight == 0.5
    assert "data_sha256" not in body.config
    assert "data_sha256" in c.get("/backtest?include_sha=true").json()["config"]


def test_backtest_days(shared):
    _, c = shared
    r = c.get("/backtest/days")
    assert r.status_code == 200
    body = BacktestDaysResponse.model_validate(r.json())
    assert body.weight == 0.5 and body.weights == [0.5, 1.0, 0.0] and body.caveats
    assert [d.date.isoformat() for d in body.days] == ["2024-07-01", DEMO_DAY]
    assert set(body.days[1].methods) == {"optimiser", "baseline_solar_first", "baseline_time_of_use"}
    assert body.days[1].methods["optimiser"].adjusted_cost_bdt == 100.5 and body.days[1].outage_slots == 8
    one = c.get("/backtest/days?weight=1").json()
    assert one["days"][0]["methods"]["optimiser"]["cost_bdt"] == 101.0
    r = c.get("/backtest/days?weight=0.3")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "weight_not_in_backtest"
    assert c.get("/backtest/days?weight=2").status_code == 422


def test_backtest_missing_is_clear_404(dirs, tmp_path):
    service = PlanService(settings_for(dirs, backtest_path=tmp_path / "none.json"))
    with client_for(service) as c:
        for path in ("/backtest", "/backtest/days"):
            r = c.get(path)
            assert r.status_code == 404
            assert r.json()["detail"]["code"] == "backtest_unavailable"
            assert "python -m energy_brain.evaluator" in r.json()["detail"]["message"]


# ------------------------------------------------------------------------------------------- CORS, settings


def _preflight(c: TestClient, origin: str):
    return c.options(
        "/plan",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def test_cors_allows_the_react_dev_server_only(shared):
    _, c = shared
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        r = _preflight(c, origin)
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
        assert c.get("/health", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin
    r = _preflight(c, "http://evil.example")
    assert "access-control-allow-origin" not in r.headers
    assert (
        "access-control-allow-origin"
        not in c.get("/health", headers={"Origin": "http://evil.example"}).headers
    )


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("EB_CORS_ORIGINS", "https://demo.example/, http://localhost:5173")
    monkeypatch.setenv("EB_EXPLAIN_CACHE", "PREFER")
    monkeypatch.setenv("EB_WARMUP", "0")
    s = ApiSettings.from_env()
    assert s.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173", "https://demo.example")
    assert s.explain_cache == "prefer" and s.warmup is False
    monkeypatch.setenv("EB_EXPLAIN_CACHE", "always")
    with pytest.raises(ValueError, match="EB_EXPLAIN_CACHE"):
        ApiSettings.from_env()


def test_extra_origin_from_settings_is_allowed(dirs):
    service = PlanService(settings_for(dirs, cors_origins=("https://demo.example",)))
    with client_for(service) as c:
        assert (
            _preflight(c, "https://demo.example").headers["access-control-allow-origin"]
            == "https://demo.example"
        )


# ---------------------------------------------------------------------------------------------- warm-up


def test_warmup_plans_demo_days_in_the_background(dirs):
    service = PlanService(settings_for(dirs, warmup=True, warmup_weights=(0.5,)))
    with client_for(service):  # lifespan starts the warm-up
        service._warmup_thread.join(120)
        h = HealthResponse.model_validate(service.health().model_dump())
    assert h.warmup.state == "done" and h.warmup.total == 3 and h.warmup.planned == 3 and h.warmup.errors == 0
    # the plans of the warm-up weights plus the Pareto grid of each demo day, which share cache entries
    assert h.cache.size == 3 * len(set(PARETO_WEIGHTS) | {0.5})
    keys = service.warmup_keys()
    assert keys[0] == (date.fromisoformat(DEMO_DAY), 0.5) and keys[-1][0] == last_plan_day()


# ---------------------------------------------------------------------------------------------- what-ifs


def test_override_impact_and_default_cache_isolation(dirs):
    service = PlanService(settings_for(dirs))
    with client_for(service, agent_with(grounded_script())) as c:
        default = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5}).json()
        request = {
            "date": DEMO_DAY,
            "w": 0.5,
            "overrides": [{"time": "13:00", "battery_kw": 100, "generator_kw": 300}],
        }
        result = c.post("/plan", json=request)
        assert result.status_code == 200, result.text
        body = result.json()
        impact = body["override"]
        assert impact["applied"] == request["overrides"]
        assert impact["feasible"]
        assert impact["baseline"] == default["plan"]["totals"]
        assert body["plan"]["schedule"]["generator_kw"][52] == pytest.approx(300)
        assert body["plan"]["schedule"]["battery_discharge_kw"][52] == 0
        assert body["plan"]["schedule"]["battery_charge_kw"][52] == pytest.approx(100)
        for name in ("cost_bdt", "co2_kg", "generator_hours", "diesel_litres"):
            assert impact["delta"][name] == pytest.approx(
                body["plan"]["totals"][name] - default["plan"]["totals"][name]
            )
        again = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5}).json()
        assert again["override"] is None
        assert again["plan"] == default["plan"]
        assert list(again) == list(default)
        assert service.cache_stats().size == 1
        cached = c.post("/plan", json=request).json()
        assert cached["cached"] and cached["plan"] == body["plan"]
        explanation = c.post("/explain", json={"date": DEMO_DAY, "w": 0.5, "question": QUESTION}).json()
        assert explanation["verified"]
        assert str(round(default["plan"]["totals"]["cost_bdt"], 1)) in explanation["answer"]


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ([{"time": "19:01", "battery_kw": 0}], "15-minute"),
        ([{"time": "24:00", "battery_kw": 0}], "HH:MM"),
        ([{"time": "7:00", "battery_kw": 0}], "HH:MM"),
        ([{"time": "19:00"}], "at least one"),
        ([{"time": "19:00", "battery_kw": 0}] * 2, "duplicate"),
        ([{"time": "19:00", "generator_kw": 1}], "generator_kw"),
        ([{"time": "19:00", "generator_kw": 9999}], "generator_kw"),
        ([{"time": "19:00", "battery_kw": 9999}], "battery_kw"),
        ([{"time": "19:00", "battery_kw": -9999}], "battery_kw"),
        ([{"time": "19:00", "battery_kw": "NaN"}], "finite"),
        ([{"time": "19:00", "battery_kw": 0}] * 97, "96"),
    ],
)
def test_override_validation_error_body(shared, overrides, needle):
    _, c = shared
    r = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5, "overrides": overrides})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "invalid_overrides"
    assert needle in r.json()["detail"]["message"]


def test_impossible_override_returns_unsafe_with_reason(shared):
    _, c = shared
    r = c.post(
        "/plan",
        json={
            "date": DEMO_DAY,
            "w": 0.5,
            "overrides": [{"time": f"0{t // 4}:{t % 4 * 15:02d}", "battery_kw": -500} for t in range(8)],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["override"]["feasible"] and not body["safe"] and not body["risk_ok"]
    assert any("SoC floor" in b for b in body["banners"])
    assert any("00:" in b and "500" in b for b in body["banners"])


def test_cloud_replan_demo_prefix_constraints_and_time(shared):
    from energy_brain.planner import find_violations

    service, c = shared
    baseline = c.post("/plan", json={"date": DEMO_DAY, "w": 0.5}).json()
    r = c.post(
        "/simulate/cloud",
        json={"date": DEMO_DAY, "w": 0.5, "time": "13:00", "drop_pct": 60, "duration_minutes": 120},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    before, after = (PlanResponse.model_validate(body[k]) for k in ("before", "after"))
    assert before.plan.model_dump(mode="json") == baseline["plan"]
    assert body["affected_slots"] == {"start": 52, "end": 60}
    assert body["event"]["time"] == "13:00" and body["event"]["drop_pct"] == 60
    for name in type(after.plan.schedule).model_fields:
        assert getattr(after.plan.schedule, name)[:52] == pytest.approx(
            getattr(before.plan.schedule, name)[:52], abs=1e-4
        )
    for q in ("p10_kw", "p50_kw", "p90_kw"):
        old, new = getattr(before.forecast.solar, q), getattr(after.forecast.solar, q)
        assert new[:52] == old[:52] and new[60:] == old[60:]
        assert new[52:60] == pytest.approx([v * 0.4 for v in old[52:60]])
    assert not find_violations(
        after.plan.schedule, after.forecast, after.signals, after.assets, after.plan.reserve_margin_kwh
    )
    assert body["safe"] and body["risk_ok"]
    assert body["replan_seconds"] < 10, f"Demo cloud re-plan took {body['replan_seconds']:.3f} s"
    print(f"\nDemo cloud replan_seconds={body['replan_seconds']:.6f}")
    assert any("simulated cloud event" in b and "historical replay" in b for b in body["banners"])
    for name in ("cost_bdt", "co2_kg", "generator_hours", "diesel_litres"):
        assert body["delta"][name] == pytest.approx(
            getattr(after.plan.totals, name) - getattr(before.plan.totals, name)
        )
    assert service.get_plan(date(2024, 7, 2), 0.5)[0].context.forecast == before.forecast


def test_cloud_clips_at_midnight_and_handles_slot_zero(shared):
    _, c = shared
    for clock, expected in [("23:45", {"start": 95, "end": 96}), ("00:00", {"start": 0, "end": 8})]:
        r = c.post("/simulate/cloud", json={"date": DEMO_DAY, "w": 0.5, "time": clock, "drop_pct": 100})
        assert r.status_code == 200, r.text
        assert r.json()["affected_slots"] == expected
        assert r.json()["event"]["duration_minutes"] == 120


@pytest.mark.parametrize(
    "change",
    [
        {"time": "13:01"},
        {"time": "24:00"},
        {"drop_pct": 0},
        {"drop_pct": 101},
        {"duration_minutes": 14},
        {"duration_minutes": 721},
    ],
)
def test_cloud_bad_input(shared, change):
    _, c = shared
    payload = {"date": DEMO_DAY, "w": 0.5, "time": "13:00", "drop_pct": 60, **change}
    r = c.post("/simulate/cloud", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_cloud_event"


def test_whatif_rate_limits_and_cache_hits(dirs):
    service = PlanService(settings_for(dirs))
    service.override_limiter = RateLimiter(1, 10)
    service.cloud_limiter = RateLimiter(1, 10)
    with client_for(service) as c:
        for path, extra in [
            ("/plan", {"overrides": [{"time": "19:00", "battery_kw": 0}]}),
            ("/simulate/cloud", {"time": "13:00", "drop_pct": 60}),
        ]:
            payload = {"date": DEMO_DAY, "w": 0.5, **extra}
            assert c.post(path, json=payload).status_code == 200
            assert c.post(path, json=payload).status_code == 200
            r = c.post(path, json={**payload, "w": 0.6})
            assert r.status_code == 429, r.text
            assert r.json()["detail"]["code"] == "rate_limited"
            assert float(r.headers["Retry-After"]) >= 1


def test_cloud_non_aligned_duration_covers_whole_slots(shared):
    _, c = shared
    r = c.post(
        "/simulate/cloud",
        json={"date": DEMO_DAY, "w": 0.5, "time": "13:00", "drop_pct": 60, "duration_minutes": 16},
    )
    assert r.status_code == 200, r.text
    assert r.json()["affected_slots"] == {"start": 52, "end": 54}


def test_cloud_infeasible_during_outage_is_honest(dirs):
    from energy_brain.planner import MilpPlanner
    from energy_brain.risk import P10RiskAgent
    from energy_brain.stubs import StubAssetProvider, StubForecastAgent, StubSignalAgent

    start = PlanService.start_of(date(2024, 7, 2))
    forecast = StubForecastAgent().run(start)
    solar = [200.0 if t == 52 else 0.0 for t in range(96)]
    forecast = forecast.model_copy(
        update={
            "solar": type(forecast.solar)(p10_kw=solar, p50_kw=solar, p90_kw=solar),
            "load": type(forecast.load)(p10_kw=[120.0] * 96, p50_kw=[120.0] * 96, p90_kw=[120.0] * 96),
        }
    )
    signals = StubSignalAgent().run(start).model_copy(update={"outage": [t == 52 for t in range(96)]})
    assets = StubAssetProvider().run(start)
    assets = assets.model_copy(
        update={
            "battery": assets.battery.model_copy(update={"max_discharge_kw": 1.0}),
            "generator": assets.generator.model_copy(update={"min_kw": 0.0, "rated_kw": 100.0}),
            "hvac": assets.hvac.model_copy(update={"occupied": [False] * 96}),
            "ev": assets.ev.model_copy(update={"energy_required_kwh": 0}),
        }
    )

    class Input:
        def __init__(self, value):
            self.value = value

        def run(self, start):
            return self.value

    service = PlanService(
        settings_for(dirs),
        agent_factory=lambda: {
            "forecast_agent": Input(forecast),
            "signal_agent": Input(signals),
            "assets": Input(assets),
            "planner": MilpPlanner(),
            "risk": P10RiskAgent(),
        },
    )
    with client_for(service) as c:
        r = c.post(
            "/simulate/cloud",
            json={"date": DEMO_DAY, "w": 0.5, "time": "13:00", "drop_pct": 100, "duration_minutes": 15},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"]["safe"]
    assert not body["safe"] and not body["risk_ok"] and not body["after"]["safe"]
    assert body["after"]["plan"]["status"] == "infeasible"
    assert any("cannot be served" in b for b in body["banners"])
    assert any("NOT SAFE TO EXECUTE" in b for b in body["banners"])
    for name in ("battery_charge_kw", "battery_discharge_kw", "generator_kw", "ev_charge_kw", "hvac_kw"):
        assert body["before"]["plan"]["schedule"][name][:52] == pytest.approx(
            body["after"]["plan"]["schedule"][name][:52], abs=1e-4
        )


@pytest.mark.parametrize("kind", ["override", "cloud"])
def test_whatif_single_flight_and_cache_available_during_solve(dirs, monkeypatch, kind):
    from concurrent.futures import Future, ThreadPoolExecutor

    import energy_brain.api.service as module
    from energy_brain.api.models import CloudRequest, SlotOverride
    from energy_brain.planner import MilpPlanner

    entered, release, following = threading.Event(), threading.Event(), threading.Event()

    class BlockingPlanner(MilpPlanner):
        fixed_calls = 0

        def plan(self, *args, fixes=None, **kwargs):
            if fixes is not None:
                self.fixed_calls += 1
                entered.set()
                assert release.wait(10), "test did not release the solve"
            return super().plan(*args, fixes=fixes, **kwargs)

    class ObservedFuture(Future):
        def result(self, timeout=None):
            following.set()
            return super().result(timeout)

    planner = BlockingPlanner()
    factory = default_agent_factory(settings_for(dirs))
    service = PlanService(settings_for(dirs), agent_factory=lambda: {**factory(), "planner": planner})
    day = date(2024, 7, 2)
    baseline, _ = service.get_plan(day, 0.5)
    monkeypatch.setattr(module, "Future", ObservedFuture)

    def solve():
        if kind == "cloud":
            return service.cloud_response(CloudRequest(date=day, w=0.5, time="13:00", drop_pct=60)).after
        return service.override_response(day, 0.5, [SlotOverride(time="13:00", battery_kw=100)])

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(solve)
        try:
            assert entered.wait(5)
            second = pool.submit(solve)
            assert following.wait(5)
            # A cached normal plan is readable while HiGHS and a what-if follower are blocked.
            cached = pool.submit(service.get_plan, day, 0.5).result(timeout=2)
            assert cached[0] is baseline and cached[1]
        finally:
            release.set()
        responses = [f.result(timeout=15) for f in (first, second)]
    assert sorted(r.cached for r in responses) == [False, True]
    assert responses[0].plan == responses[1].plan
    assert planner.fixed_calls == 1
    assert service.get_plan(day, 0.5)[0] is baseline


def test_whatif_cache_is_bounded_and_default_plans_stay_clean(dirs, monkeypatch):
    import energy_brain.api.service as module
    from energy_brain.api.models import SlotOverride

    monkeypatch.setattr(module, "WHATIF_CACHE_SIZE", 2)
    service = PlanService(settings_for(dirs))
    day = date(2024, 7, 2)
    baseline, _ = service.get_plan(day, 0.5)
    for power in (10, 20, 30):
        response = service.override_response(day, 0.5, [SlotOverride(time="13:00", battery_kw=power)])
        assert not response.cached
    assert service.override_response(day, 0.5, [SlotOverride(time="13:00", battery_kw=30)]).cached
    assert not service.override_response(day, 0.5, [SlotOverride(time="13:00", battery_kw=10)]).cached
    assert service.get_plan(day, 0.5)[0] is baseline


def test_explainer_rejects_override_request(shared):
    _, c = shared
    r = c.post(
        "/explain",
        json={
            "date": DEMO_DAY,
            "w": 0.5,
            "question": QUESTION,
            "overrides": [{"time": "19:00", "battery_kw": 0}],
        },
    )
    assert r.status_code == 422


def test_default_plan_null_and_empty_overrides_are_identical(shared):
    _, c = shared
    bodies = [
        c.post("/plan", json={"date": DEMO_DAY, "w": 0.5, **extra}).json()
        for extra in ({}, {"overrides": None}, {"overrides": []})
    ]
    for b in bodies:
        b.pop("cached")
    assert bodies[0] == bodies[1] == bodies[2]
    assert list(bodies[0])[-1] == "override" and bodies[0]["override"] is None


def test_insufficient_override_names_time_and_power(shared):
    _, c = shared
    r = c.post(
        "/plan",
        json={
            "date": DEMO_DAY,
            "w": 0.5,
            "overrides": [{"time": "19:00", "battery_kw": 0, "generator_kw": 300}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["override"]["feasible"]
    assert any("19:00" in b and "generator_kw=300" in b and "unserved" in b for b in body["banners"])


def test_risk_failing_whatifs_are_not_labelled_safe(dirs):
    from energy_brain.risk import format_reason
    from energy_brain.schemas import RiskReport

    class RejectRisk:
        def check(self, plan, forecast, signals, assets):
            return RiskReport(
                ok=False,
                reasons=[
                    format_reason(
                        {
                            "check": "unserved_energy",
                            "fixable": "no",
                            "detail": "test stress failure",
                        }
                    )
                ],
            )

    factory = default_agent_factory(settings_for(dirs))
    service = PlanService(settings_for(dirs), agent_factory=lambda: {**factory(), "risk": RejectRisk()})
    with client_for(service) as c:
        override = c.post(
            "/plan", json={"date": DEMO_DAY, "w": 0.5, "overrides": [{"time": "13:00", "battery_kw": 100}]}
        ).json()
        assert override["override"]["feasible"]
        assert not override["safe"] and not override["risk_ok"]
        cloud = c.post(
            "/simulate/cloud", json={"date": DEMO_DAY, "w": 0.5, "time": "13:00", "drop_pct": 60}
        ).json()
        assert not cloud["safe"] and not cloud["risk_ok"]
        assert not cloud["after"]["safe"] and not cloud["before"]["safe"]
        assert any("Risk check failed" in b for b in cloud["banners"])

