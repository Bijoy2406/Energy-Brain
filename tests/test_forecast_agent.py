"""Forecast agent (issue #14). No dependency on the git-ignored data/processed or models/.

Models are trained into tmp_path on a 3-week history made by repeating the committed sample week
(data/sample, 2024-06-01..07) at -14 and -7 days, so the target day 2024-06-03 has its 7 lag days.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from energy_brain.assets import AssetAgent
from energy_brain.defaults import build_default_agents
from energy_brain.forecast import (
    NOTE_PREFIX,
    ForecastUnavailableError,
    ModelForecastAgent,
    format_note,
    parse_note,
)
from energy_brain.forecast import agent as agent_mod
from energy_brain.forecast import load as load_model
from energy_brain.forecast import solar as solar_model
from energy_brain.interfaces import ForecastAgent
from energy_brain.orchestrator import run_plan
from energy_brain.planner import MilpPlanner, is_safe
from energy_brain.schemas import Forecast, PlanResult
from energy_brain.stubs import StubRisk, StubSignalAgent
from energy_brain.timeaxis import n_slots

DHAKA = ZoneInfo("Asia/Dhaka")
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"
DAY = datetime(2024, 6, 3, tzinfo=DHAKA)  # Monday, inside the synthetic history
FUTURE = datetime(2026, 11, 17, tzinfo=DHAKA)  # outside every data range


def _tile(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for weeks in (-2, -1, 0):
        part = df.copy()
        part["timestamp"] = part["timestamp"] + pd.Timedelta(weeks=weeks)
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    return {k: _tile(pd.read_parquet(SAMPLE_DIR / f"{k}_sample.parquet")) for k in ("weather", "pv", "load")}


@pytest.fixture(scope="module")
def models_dir(frames, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("models")
    solar_df = solar_model.prepare_solar_dataset(frames["weather"], frames["pv"])
    solar_model.train_solar_quantile_models(solar_df, train_years=(2024,), test_year=2024, models_dir=out)
    load_df = load_model.prepare_load_dataset(frames["load"], frames["weather"])
    load_model.train_load_quantile_models(load_df, train_years=(2024,), test_year=2024, models_dir=out)
    return out


@pytest.fixture
def no_training(monkeypatch):
    """Fail the test if anything tries to train a model."""

    def boom(*args, **kwargs):
        raise AssertionError("training was triggered")

    monkeypatch.setattr(solar_model, "train_solar_quantile_models", boom)
    monkeypatch.setattr(load_model, "train_load_quantile_models", boom)


def _agent(frames, models_dir, **kw) -> ModelForecastAgent:
    return ModelForecastAgent(
        weather_df=frames["weather"], pv_df=frames["pv"], load_df=frames["load"], models_dir=models_dir, **kw
    )


def _assert_valid(fc: Forecast, start: datetime) -> None:
    assert isinstance(fc, Forecast)
    assert fc.start == start
    for q in (fc.solar, fc.load):
        assert len(q.p10_kw) == len(q.p50_kw) == len(q.p90_kw) == n_slots()
        for a, b, c in zip(q.p10_kw, q.p50_kw, q.p90_kw, strict=True):
            assert 0.0 <= a <= b <= c
    assert Forecast.model_validate_json(fc.model_dump_json()) == fc


def _note(fc: Forecast) -> dict[str, str]:
    assert fc.fallback_used is True
    parsed = parse_note(fc.note)
    assert parsed is not None, fc.note
    return parsed


# ---------------------------------------------------------------- tier 0


def test_happy_path_uses_models(frames, models_dir, no_training):
    agent = _agent(frames, models_dir)
    assert isinstance(agent, ForecastAgent)
    fc = agent.run(DAY)
    _assert_valid(fc, DAY)
    assert fc.fallback_used is False
    assert fc.note is None
    assert fc.solar.p90_kw[0] == 0.0  # midnight: no sun
    assert max(fc.solar.p50_kw) > 50.0
    assert min(fc.load.p50_kw) > 100.0  # base load never drops to zero


def test_other_timezone_same_instant_is_tier0(frames, models_dir, no_training):
    start = datetime(2024, 6, 2, 18, 0, tzinfo=UTC)  # = 2024-06-03 00:00 Asia/Dhaka
    fc = _agent(frames, models_dir).run(start)
    assert fc.fallback_used is False
    assert fc.start == start


# ---------------------------------------------------------------- fallbacks


def test_missing_models_falls_back_without_training(frames, tmp_path, no_training):
    empty = tmp_path / "models"
    empty.mkdir()
    fc = _agent(frames, empty).run(DAY)
    _assert_valid(fc, DAY)
    note = _note(fc)
    assert note["reason"] == "models_missing"
    assert note["method"] == "persistence"
    assert note["source"] == "injected"
    assert note["solar_from"] == "2024-06-02"  # same slot yesterday
    assert note["load_from"] == "2024-05-27"  # same weekday last week
    assert "solar_p50.txt" in note["detail"]
    assert list(empty.iterdir()) == []  # nothing trained or written


def test_persistence_values_copy_the_source_days(frames, tmp_path):
    fc = _agent(frames, tmp_path).run(DAY)
    pv = frames["pv"].set_index("timestamp")["pv_kw"]
    load = frames["load"].set_index("timestamp")["base_kw"]
    yesterday = pd.date_range(DAY - timedelta(days=1), periods=n_slots(), freq="15min")
    last_week = pd.date_range(DAY - timedelta(days=7), periods=n_slots(), freq="15min")
    assert fc.solar.p50_kw == pytest.approx(pv.reindex(yesterday).tolist())
    assert fc.load.p50_kw == pytest.approx(load.reindex(last_week).tolist())


def test_out_of_range_date_is_flagged_analog_day(frames, models_dir, no_training):
    fc = _agent(frames, models_dir).run(FUTURE)
    _assert_valid(fc, FUTURE)
    note = _note(fc)
    assert note["reason"] == "out_of_range"
    assert note["method"] == "analog_day"
    assert note["data_range"] == "2024-05-18..2024-06-07"
    # No 17 Nov in the data: latest day for solar, latest same weekday (Tuesday) for load.
    assert note["solar_from"] == "2024-06-07"
    assert note["load_from"] == "2024-06-04"
    assert datetime.fromisoformat(note["load_from"]).weekday() == FUTURE.weekday()


def test_missing_lag_day_is_flagged(frames, models_dir, no_training):
    pv = frames["pv"]
    gap = pv[pv["timestamp"].dt.date != datetime(2024, 5, 30).date()]
    agent = ModelForecastAgent(
        weather_df=frames["weather"], pv_df=gap, load_df=frames["load"], models_dir=models_dir
    )
    fc = agent.run(DAY)
    _assert_valid(fc, DAY)
    note = _note(fc)
    assert note["reason"] == "history_incomplete"
    assert "2024-05-30" in note["detail"]


def test_prediction_error_falls_back(frames, models_dir, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("corrupt booster")

    monkeypatch.setattr(solar_model, "predict", broken)
    fc = _agent(frames, models_dir).run(DAY)
    _assert_valid(fc, DAY)
    note = _note(fc)
    assert note["reason"] == "prediction_error"
    assert "RuntimeError: corrupt booster" in note["detail"]


def test_badly_ordered_model_output_is_not_hidden(frames, models_dir, monkeypatch):
    real = solar_model.predict

    def swapped(*args, **kwargs):
        df = real(*args, **kwargs)
        df["p10_kw"] = df["p90_kw"] + 50.0
        return df

    monkeypatch.setattr(solar_model, "predict", swapped)
    fc = _agent(frames, models_dir).run(DAY)
    _assert_valid(fc, DAY)
    note = _note(fc)
    assert note["reason"] == "invalid_output"
    assert "solar" in note["detail"]


def test_repair_quantiles_fixes_tiny_violations_only():
    a, b, c = agent_mod.repair_quantiles([1.0, -1e-9], [1.0 - 1e-9, 0.0], [1.0, 0.0])
    assert a[0] <= b[0] <= c[0] and a[1] == 0.0
    with pytest.raises(ValueError):
        agent_mod.repair_quantiles([2.0], [1.0], [3.0])
    with pytest.raises(ValueError):
        agent_mod.repair_quantiles([float("nan")], [1.0], [1.0])


# ---------------------------------------------------------------- start handling


def test_naive_start_raises(frames, models_dir):
    with pytest.raises(ValueError, match="timezone-aware"):
        _agent(frames, models_dir).run(datetime(2024, 6, 3))


def test_off_slot_start_raises(frames, models_dir):
    with pytest.raises(ValueError, match="slot boundary"):
        _agent(frames, models_dir).run(datetime(2024, 6, 3, 0, 7, tzinfo=DHAKA))


def test_non_midnight_start_uses_persistence(frames, models_dir, no_training):
    start = datetime(2024, 6, 3, 6, 15, tzinfo=DHAKA)
    fc = _agent(frames, models_dir).run(start)
    _assert_valid(fc, start)
    note = _note(fc)
    assert note["reason"] == "non_midnight_start"
    assert note["method"] == "persistence"
    pv = frames["pv"].set_index("timestamp")["pv_kw"]
    src = pd.date_range(start - timedelta(days=1), periods=n_slots(), freq="15min")
    assert fc.solar.p50_kw == pytest.approx(pv.reindex(src).tolist())  # window spans two days


# ---------------------------------------------------------------- data sources


def test_processed_missing_uses_sample(tmp_path, no_training):
    shutil.copytree(SAMPLE_DIR, tmp_path / "sample")
    agent = ModelForecastAgent(data_dir=tmp_path, models_dir=tmp_path / "models")
    fc = agent.run(FUTURE)
    _assert_valid(fc, FUTURE)
    note = _note(fc)
    assert note["source"] == "sample"
    assert note["reason"].split(",") == ["out_of_range", "models_missing", "processed_data_missing"]
    assert note["data_range"] == "2024-06-01..2024-06-07"


def test_no_data_at_all_raises_clear_error(tmp_path):
    # Construction is lazy: no error until run().
    agent = ModelForecastAgent(data_dir=tmp_path / "nothing", models_dir=tmp_path / "models")
    with pytest.raises(ForecastUnavailableError, match="no forecast data"):
        agent.run(FUTURE)


def test_note_format_round_trip():
    fields = {
        "reason": "models_missing,processed_data_missing",
        "method": "persistence",
        "source": "sample",
        "solar_from": "2024-06-02",
        "load_from": "2024-05-27",
        "data_range": "2024-06-01..2024-06-07",
        "detail": "a; b = c",
    }
    note = format_note(fields)
    assert note.startswith(NOTE_PREFIX)
    assert "\n" not in note
    assert parse_note(note) == {**fields, "detail": "a, b = c"}
    assert parse_note(None) is None
    assert parse_note("STUB forecast") is None


# ---------------------------------------------------------------- orchestrator


def test_run_plan_with_real_forecast_agent(frames, models_dir, no_training):
    result = run_plan(
        DAY,
        0.5,
        forecast_agent=_agent(frames, models_dir),
        signal_agent=StubSignalAgent(),
        assets=AssetAgent(),
        planner=MilpPlanner(),
        risk=StubRisk(),
    )
    assert isinstance(result, PlanResult)
    assert result.start == DAY
    assert result.status in ("optimal", "feasible")
    assert is_safe(result)
    assert result.fallback_used is False
    result.schedule.check_energy_balance(tol=1e-6)


def test_build_default_agents_runs_end_to_end_on_sample(tmp_path, no_training):
    shutil.copytree(SAMPLE_DIR, tmp_path / "sample")
    agents = build_default_agents(
        forecast_agent=ModelForecastAgent(data_dir=tmp_path, models_dir=tmp_path / "models")
    )
    assert isinstance(agents["forecast_agent"], ModelForecastAgent)
    assert isinstance(build_default_agents()["forecast_agent"], ModelForecastAgent)
    result = run_plan(FUTURE, 0.5, **agents)
    assert result.status in ("optimal", "feasible")
    assert result.fallback_used is True  # the forecast fallback propagates to the plan
    with pytest.raises(TypeError):
        build_default_agents(forecaster=None)
