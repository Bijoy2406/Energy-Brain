"""Pareto API contract: injected agents, no network or local processed data/models."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from energy_brain.api.app import create_app
from energy_brain.api.models import last_plan_day
from energy_brain.api.service import PlanService, RateLimiter
from energy_brain.api.settings import ApiSettings
from energy_brain.stubs import (
    StubAssetProvider,
    StubForecastAgent,
    StubPlanner,
    StubRisk,
    StubSignalAgent,
)

WEIGHTS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0]
DAY = date(2024, 7, 2)


class ChosenPlanner(StubPlanner):
    def __init__(self, totals=None, unsafe=()):
        super().__init__()
        self.totals = totals or [(100 - i, 10 + i) for i in range(11)]
        self.unsafe = unsafe
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def plan(self, forecast, signals, assets, weight, reserve_margin_kwh=0):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(10)
        p = super().plan(forecast, signals, assets, weight, reserve_margin_kwh)
        i = WEIGHTS.index(weight)
        cost, co2 = self.totals[i]
        return p.model_copy(
            update={
                "totals": p.totals.model_copy(update={"cost_bdt": cost, "co2_kg": co2}),
                "status": "infeasible" if i in self.unsafe else p.status,
            }
        )


def service_for(tmp_path, planner=None, forecast=None, **kwargs):
    planner = planner or ChosenPlanner()
    settings = ApiSettings(warmup=False, backtest_path=tmp_path / "missing.json", **kwargs)
    service = PlanService(
        settings,
        agent_factory=lambda: dict(
            forecast_agent=forecast or StubForecastAgent(),
            signal_agent=StubSignalAgent(),
            assets=StubAssetProvider(),
            planner=planner,
            risk=StubRisk(),
        ),
    )
    return service, planner


def test_weights_extremes_and_totals(tmp_path):
    service, planner = service_for(tmp_path)
    with TestClient(create_app(service)) as c:
        r = c.get("/pareto", params={"date": DAY.isoformat()})
        assert r.status_code == 200
        body = r.json()
        pts = body["points"]
        assert [p["weight"] for p in pts] == WEIGHTS
        assert [(p["cost_bdt"], p["co2_kg"]) for p in pts] == planner.totals
        assert pts[-1]["cost_bdt"] == min(p["cost_bdt"] for p in pts if p["safe"])
        assert pts[0]["co2_kg"] == min(p["co2_kg"] for p in pts if p["safe"])
        assert body["distinct_points"] == 11
        assert not body["cached"] and body["solve_seconds"] > 0
        assert body["weights_meaning"] == "1 = cheapest, 0 = cleanest"
        assert body["basis"] == "planned totals at the P50 forecast, before settlement against actuals"
        plan = c.post("/plan", json={"date": DAY.isoformat(), "w": 0.3}).json()
        assert plan["cached"]
        for name in ("generator_hours", "diesel_litres"):
            assert pts[6][name] == plan["plan"]["totals"][name]


def test_dominance_groups_and_unsafe(tmp_path):
    totals = [
        (10, 10),
        (10.000001, 10),
        (10, 9),
        (9, 10),
        (11, 11),
        (8, 12),
        (12, 8),
        (1, 1),
        (10, 9),
        (9, 10),
        (12, 12),
    ]
    service, _ = service_for(tmp_path, ChosenPlanner(totals, unsafe=(7,)))
    with TestClient(create_app(service)) as c:
        body = c.get("/pareto", params={"date": DAY.isoformat()}).json()
    pts = body["points"]
    assert [p["group"] for p in pts] == [0, 0, 1, 2, 3, 4, 5, 6, 1, 2, 7]
    assert [p["same_as_weight"] for p in pts] == [
        None,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        0.1,
        0.15,
        None,
    ]
    assert [p["efficient"] for p in pts] == [
        False,
        False,
        True,
        True,
        False,
        True,
        True,
        False,
        True,
        True,
        False,
    ]
    assert [p["weight"] for p in body["frontier"]] == [0.25, 0.15, 0.1, 0.3]
    assert body["distinct_points"] == 7
    assert not pts[7]["safe"] and pts[7]["status"] == "infeasible"


def test_all_unsafe(tmp_path):
    service, _ = service_for(tmp_path, ChosenPlanner(unsafe=range(11)))
    with TestClient(create_app(service)) as c:
        body = c.get("/pareto", params={"date": DAY.isoformat()}).json()
    assert body["frontier"] == [] and body["distinct_points"] == 0
    assert not any(p["efficient"] for p in body["points"])
    assert any("no safe" in b.lower() for b in body["banners"])


def test_cache_and_shared_plans(tmp_path):
    service, planner = service_for(tmp_path)
    for w in WEIGHTS:
        service.get_plan(DAY, w)
    with TestClient(create_app(service)) as c:
        first = c.get("/pareto", params={"date": DAY.isoformat()}).json()
        second = c.get("/pareto", params={"date": DAY.isoformat()}).json()
    assert not first["cached"] and second["cached"]
    assert all(p["plan_cached"] for p in first["points"])
    assert first["points"] == second["points"]
    assert first["computed_at"] == second["computed_at"]
    assert planner.calls == 11


def test_single_flight(tmp_path, monkeypatch):
    from concurrent.futures import Future

    import energy_brain.api.service as module

    waiting = threading.Event()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout)

    monkeypatch.setattr(module, "Future", ObservedFuture)
    service, planner = service_for(tmp_path)
    planner.release.clear()
    with TestClient(create_app(service)) as c, ThreadPoolExecutor(2) as pool:
        first = pool.submit(c.get, "/pareto", params={"date": DAY.isoformat()})
        assert planner.entered.wait(5)
        second = pool.submit(c.get, "/pareto", params={"date": DAY.isoformat()})
        try:
            assert waiting.wait(5)  # follower has joined the in-flight computation
        finally:
            planner.release.set()
        bodies = [f.result(timeout=15).json() for f in (first, second)]
    assert sorted(b["cached"] for b in bodies) == [False, True]
    assert bodies[0]["points"] == bodies[1]["points"]
    assert planner.calls == 11


@pytest.mark.parametrize("value", ["bad-date", "2021-12-31", "2099-01-01", None])
def test_dates(tmp_path, value):
    service, _ = service_for(tmp_path)
    with TestClient(create_app(service)) as c:
        r = c.get("/pareto", params={} if value is None else {"date": value})
        assert r.status_code == 422
        if value in ("2021-12-31", "2099-01-01"):
            plan = c.post("/plan", json={"date": value, "w": 0})
            assert r.json()["detail"][0]["msg"] == plan.json()["detail"][0]["msg"]


def test_no_forecast_and_retry(tmp_path):
    from energy_brain.forecast import ForecastUnavailableError

    class Missing:
        def run(self, start):
            raise ForecastUnavailableError("missing")

    service, _ = service_for(tmp_path, forecast=Missing())
    with TestClient(create_app(service)) as c:
        r = c.get("/pareto", params={"date": DAY.isoformat()})
        assert r.status_code == 503
        assert r.json() == c.post("/plan", json={"date": DAY.isoformat(), "w": 0}).json()
        service._agents["forecast_agent"] = StubForecastAgent()
        assert c.get("/pareto", params={"date": DAY.isoformat()}).status_code == 200


def test_rate_limit_cache_hits_free(tmp_path):
    service, _ = service_for(tmp_path)
    service.pareto_limiter = RateLimiter(1, 30)
    with TestClient(create_app(service)) as c:
        assert c.get("/pareto", params={"date": DAY.isoformat()}).status_code == 200
        r = c.get("/pareto", params={"date": (DAY + timedelta(days=1)).isoformat()})
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
        assert r.json()["detail"]["code"] == "rate_limited"
        assert c.get("/pareto", params={"date": DAY.isoformat()}).json()["cached"]


def test_tomorrow_fallback_matches_plan(tmp_path):
    from pathlib import Path
    from shutil import copytree

    from energy_brain.api.service import default_agent_factory

    data = tmp_path / "data"
    copytree(Path(__file__).resolve().parents[1] / "data/sample", data / "sample")
    service = PlanService(
        ApiSettings(
            warmup=False,
            data_dir=data,
            models_dir=tmp_path / "models",
            backtest_path=tmp_path / "missing.json",
        )
    )
    service._agent_factory = default_agent_factory(service.settings)
    with TestClient(create_app(service)) as c:
        day = last_plan_day().isoformat()
        body = c.get("/pareto", params={"date": day}).json()
        plan = c.post("/plan", json={"date": day, "w": 0}).json()
    for key in ("forecast_fallback", "signal_fallback", "forecast_source", "banners"):
        assert body[key] == plan[key]
    assert body["forecast_fallback"]["used"] and body["signal_fallback"]["used"]
    safe = [p for p in body["points"] if p["safe"]]
    assert safe
    assert body["points"][-1]["cost_bdt"] <= min(p["cost_bdt"] for p in safe) * (1 + 1e-6)
    assert body["points"][0]["co2_kg"] <= min(p["co2_kg"] for p in safe) * (1 + 1e-6)


def test_lru_eviction_reuses_plan_cache(tmp_path, monkeypatch):
    import energy_brain.api.service as module

    monkeypatch.setattr(module, "PARETO_CACHE_SIZE", 2)
    service, planner = service_for(tmp_path)
    days = [DAY + timedelta(days=i) for i in range(3)]
    for day in days[:2]:
        assert not service.pareto_response(day).cached
    assert service.pareto_response(days[0]).cached  # touch oldest
    service.pareto_response(days[2])
    assert service.pareto_response(days[0]).cached
    evicted = service.pareto_response(days[1])
    assert not evicted.cached
    assert all(p.plan_cached for p in evicted.points)
    assert planner.calls == 33


def test_rate_limit_total_and_default_client_limit(tmp_path):
    service, _ = service_for(tmp_path)
    for i in range(6):
        service.pareto_response(DAY + timedelta(days=i), "one-client")
    from energy_brain.api.service import RateLimited

    with pytest.raises(RateLimited) as error:
        service.pareto_response(DAY + timedelta(days=6), "one-client")
    assert error.value.scope == "client"
    service.pareto_limiter = RateLimiter(6, 1)
    service.pareto_response(DAY + timedelta(days=6), "another-client")
    with pytest.raises(RateLimited) as error:
        service.pareto_response(DAY + timedelta(days=7), "third-client")
    assert error.value.scope == "total"


def test_warmup_pareto_runs_after_plans_and_survives_failure(tmp_path, monkeypatch):
    days = tmp_path / "days.yaml"
    days.write_text('days:\n  - {date: "2024-07-02", label: "demo", demo: true}\n')
    service, _ = service_for(tmp_path, demo_days_path=days, warmup_weights=(0.5,))
    calls = []
    original = service.pareto_response

    def observed(day):
        # All initial warm-up plans have finished before the first Pareto computation.
        assert service._warmup["planned"] == service._warmup["total"]
        calls.append(day)
        if len(calls) == 1:
            raise RuntimeError("injected warm-up failure")
        return original(day)

    monkeypatch.setattr(service, "pareto_response", observed)
    service.start_warmup()
    service._warmup_thread.join(15)
    assert not service._warmup_thread.is_alive()
    assert service._warmup["state"] == "done"
    assert service._warmup["errors"] == 1
    assert len(calls) == 2
    assert original(last_plan_day()).cached
    service.shutdown()


def test_zero_totals_and_relative_tolerance(tmp_path):
    totals = [
        (0, 0),
        (0, 0),
        (1e-12, 0),
        (100, 10),
        (100.00005, 10.000005),
        (100.001, 10),
        (0, 1e-12),
        (10, 0),
        (0, 10),
        (11, 11),
        (12, 12),
    ]
    service, _ = service_for(tmp_path, ChosenPlanner(totals))
    body = service.pareto_response(DAY)
    assert [p.group for p in body.points] == [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8]
    assert [p.efficient for p in body.points] == [True, True] + [False] * 9
    assert [p.weight for p in body.frontier] == [0]
