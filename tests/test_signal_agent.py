"""Tests for the Signal agent (issue #15). No dependency on data/processed or models/."""

from __future__ import annotations

import math
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from energy_brain.assets import AssetAgent
from energy_brain.config import load_site_config
from energy_brain.data.outages import generate_outage_events, get_daily_outage_mask
from energy_brain.data.signals_lookup import load_signals_config
from energy_brain.defaults import build_default_agents
from energy_brain.forecast import ModelForecastAgent
from energy_brain.interfaces import SignalAgent
from energy_brain.orchestrator import run_plan
from energy_brain.planner import MilpPlanner, is_safe
from energy_brain.schemas import SignalTable
from energy_brain.signals import NOTE_PREFIX, CalendarSignalAgent, analog_day, format_note, parse_note
from energy_brain.signals import agent as agent_mod
from energy_brain.stubs import StubRisk
from energy_brain.timeaxis import n_slots, slot_minutes

DHAKA = ZoneInfo("Asia/Dhaka")
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"
DAY = datetime(2024, 6, 3, tzinfo=DHAKA)  # Monday; calendar outage 19:30-20:45 (also in data/sample)
FUTURE = datetime(2026, 11, 18, tzinfo=DHAKA)  # Wednesday, outside the outage calendar
SIG = load_signals_config()
PEAK = SIG.tariff.rates.peak_bdt_per_kwh
FLAT = SIG.tariff.rates.flat_bdt_per_kwh
OFF = SIG.tariff.rates.off_peak_bdt_per_kwh


def _slot(hour: int, minute: int = 0) -> int:
    """Index of the slot starting at hh:mm on a midnight-started day."""
    return (hour * 60 + minute) // slot_minutes()


def _events(*intervals: tuple[datetime, datetime]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"start": s, "end": e, "duration_hours": (e - s).total_seconds() / 3600, "reason": "test"}
            for s, e in intervals
        ]
    )


def _at(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=DHAKA)


@pytest.fixture
def no_files(tmp_path):
    """A data_dir with nothing in it: the agent must regenerate the calendar in memory."""
    return tmp_path / "empty_data"


@pytest.fixture(scope="module")
def calendar() -> pd.DataFrame:
    return generate_outage_events()


# ---------------------------------------------------------------- shape and contract


def test_satisfies_protocol_and_shapes(no_files):
    agent = CalendarSignalAgent(data_dir=no_files)
    assert isinstance(agent, SignalAgent)
    table = agent.run(DAY)
    n = n_slots()
    assert table.start == DAY
    for name in (
        "tariff_bdt_per_kwh",
        "grid_co2_kg_per_kwh",
        "diesel_price_bdt_per_l",
        "diesel_co2_kg_per_l",
    ):
        values = getattr(table, name)
        assert len(values) == n
        assert all(math.isfinite(v) and v >= 0 for v in values)
    assert len(table.outage) == n and all(isinstance(x, bool) for x in table.outage)
    assert table.grid_co2_kg_per_kwh == [SIG.grid_emission.factor_kg_per_kwh] * n
    assert table.diesel_price_bdt_per_l == [SIG.diesel.price_bdt_per_l] * n
    assert table.diesel_co2_kg_per_l == [SIG.diesel.emission_factor_kg_per_l] * n
    assert SignalTable.model_validate_json(table.model_dump_json()) == table


def test_construction_reads_nothing(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("read at construction")

    monkeypatch.setattr(agent_mod, "load_signals_config", boom)
    monkeypatch.setattr(agent_mod, "generate_outage_events", boom)
    CalendarSignalAgent(data_dir=tmp_path)
    CalendarSignalAgent()


def test_aware_start_in_other_zone_and_bad_start(no_files):
    agent = CalendarSignalAgent(outages_df=_events())
    utc = DAY.astimezone(ZoneInfo("UTC"))
    assert agent.run(utc).start == DAY
    with pytest.raises(ValueError):
        agent.run(datetime(2024, 6, 3))  # naive
    with pytest.raises(ValueError):
        agent.run(DAY + timedelta(minutes=7))  # off a slot boundary


# ---------------------------------------------------------------- tariff


def test_tariff_window_edges_and_totals():
    table = CalendarSignalAgent(outages_df=_events()).run(DAY)
    tariff = table.tariff_bdt_per_kwh
    assert tariff[_slot(5, 45)] == OFF and tariff[_slot(6)] == FLAT
    assert tariff[_slot(16, 45)] == FLAT and tariff[_slot(17)] == PEAK
    assert tariff[_slot(22, 45)] == PEAK and tariff[_slot(23)] == OFF
    assert tariff[0] == OFF and tariff[-1] == OFF
    per_hour = 60 // slot_minutes()
    assert tariff.count(OFF) == 7 * per_hour  # 23:00-06:00
    assert tariff.count(FLAT) == 11 * per_hour  # 06:00-17:00
    assert tariff.count(PEAK) == 6 * per_hour  # 17:00-23:00
    assert sum(tariff) == pytest.approx(per_hour * (7 * OFF + 11 * FLAT + 6 * PEAK))


def test_slot_straddling_a_tariff_edge_raises():
    step = timedelta(minutes=45)
    with pytest.raises(ValueError, match="straddles"):
        CalendarSignalAgent._slot_tariff(_at(DAY.date(), 16, 30), step, SIG)
    assert CalendarSignalAgent._slot_tariff(_at(DAY.date(), 16, 15), step, SIG) == FLAT


# ---------------------------------------------------------------- outages


def test_outage_block_with_partial_slots_rounds_up():
    d = DAY.date()
    df = _events((_at(d, 13, 5), _at(d, 14, 10)), (_at(d, 18), _at(d, 19, 30)))
    mask = CalendarSignalAgent(outages_df=df).run(DAY).outage
    expected = set(range(_slot(13), _slot(14) + 1)) | set(range(_slot(18), _slot(19, 30)))
    assert {i for i, x in enumerate(mask) if x} == expected
    assert mask[_slot(12, 45)] is False  # 12:45-13:00, before the 13:05 start
    assert mask[_slot(14, 15)] is False  # outage ended 14:10, inside slot 14:00
    assert mask[_slot(19, 30)] is False  # outage ends exactly at the slot start


def test_mask_matches_issue_9_helper_on_generated_calendar(no_files, calendar):
    agent = CalendarSignalAgent(data_dir=no_files)
    days = sorted({ts.date() for ts in calendar["start"]})[:20]
    for d in days:
        start = _at(d, 0)
        mask = agent.run(start).outage
        assert any(mask)
        assert mask == get_daily_outage_mask(start, calendar, n_slots=n_slots(), slot_minutes=slot_minutes())
        assert agent.last_note is None
    assert agent.outage_source == "generated"


def test_calendar_day_from_generated_calendar(no_files):
    mask = CalendarSignalAgent(data_dir=no_files).run(DAY).outage
    assert {i for i, x in enumerate(mask) if x} == set(range(_slot(19, 30), _slot(20, 45)))


def test_non_midnight_start_spans_two_days():
    d = DAY.date()
    df = _events((_at(d, 11), _at(d, 12)), (_at(d + timedelta(days=1), 2), _at(d + timedelta(days=1), 3)))
    start = _at(d, 12)
    table, note = CalendarSignalAgent(outages_df=df).run_with_note(start)
    assert table.start == start and note is None
    on = {i for i, x in enumerate(table.outage) if x}
    assert on == set(range(_slot(14), _slot(15)))  # 02:00-03:00 next day = 14 h after 12:00
    assert table.tariff_bdt_per_kwh[0] == FLAT  # 12:00
    assert table.tariff_bdt_per_kwh[_slot(5)] == PEAK  # 17:00


# ---------------------------------------------------------------- outside the data range


def test_analog_day_rule():
    a = analog_day(FUTURE.date(), agent_mod.DEFAULT_DATA_RANGE)
    assert a == date(2024, 11, 20) and a.weekday() == FUTURE.weekday()
    early = analog_day(date(2019, 3, 10), agent_mod.DEFAULT_DATA_RANGE)
    assert early.year == 2024 and early.weekday() == date(2019, 3, 10).weekday()
    leap = analog_day(date(2028, 2, 29), agent_mod.DEFAULT_DATA_RANGE)
    assert leap.weekday() == date(2028, 2, 29).weekday() and abs((leap - date(2024, 2, 29)).days) <= 3
    edge = analog_day(date(2025, 12, 31), agent_mod.DEFAULT_DATA_RANGE)  # 2024 analog would leave the range
    assert agent_mod.DEFAULT_DATA_RANGE[0] <= edge <= agent_mod.DEFAULT_DATA_RANGE[1]


def test_date_outside_range_uses_analog_day_and_is_flagged():
    src = date(2024, 11, 20)
    df = _events((_at(src, 18), _at(src, 19)), (_at(FUTURE.date(), 10), _at(FUTURE.date(), 11)))
    agent = CalendarSignalAgent(outages_df=df)
    table, note = agent.run_with_note(FUTURE)
    # Events dated on the out-of-range day itself are ignored; the analog day's are shifted onto it.
    assert {i for i, x in enumerate(table.outage) if x} == set(range(_slot(18), _slot(19)))
    assert note is not None and note.startswith(NOTE_PREFIX) and "\n" not in note
    assert agent.last_note == note
    fields = parse_note(note)
    assert fields["reason"] == "out_of_range"
    assert fields["method"] == "analog_day"
    assert fields["source"] == "injected"
    assert fields["outage_from"] == "2024-11-20"
    assert fields["data_range"] == "2022-01-01..2024-12-31"
    assert "2026-11-18" in fields["detail"]
    # Same table from run(); an in-range run clears last_note.
    assert agent.run(FUTURE) == table and agent.last_note == note
    agent.run(DAY)
    assert agent.last_note is None


def test_window_crossing_range_end_flags_only_the_outside_day():
    start = datetime(2024, 12, 31, 12, tzinfo=DHAKA)
    agent = CalendarSignalAgent(outages_df=_events())
    _, note = agent.run_with_note(start)
    fields = parse_note(note)
    assert fields["outage_from"] == analog_day(date(2025, 1, 1), agent.data_range).isoformat()
    assert "2025-01-01" in fields["detail"] and "2024-12-31 outside" not in fields["detail"]


def test_data_range_keyword(no_files):
    agent = CalendarSignalAgent(outages_df=_events(), data_range=(date(2024, 6, 1), date(2024, 6, 7)))
    assert agent.run(DAY) is not None and agent.last_note is None
    agent.run(datetime(2024, 6, 10, tzinfo=DHAKA))
    assert parse_note(agent.last_note)["outage_from"] == "2024-06-03"  # nearest Monday in the range
    with pytest.raises(ValueError):
        CalendarSignalAgent(data_range=(date(2024, 2, 1), date(2024, 1, 1)))


def test_note_round_trip():
    fields = {
        "reason": "out_of_range",
        "method": "analog_day",
        "source": "generated",
        "outage_from": "2024-11-20",
        "data_range": "2022-01-01..2024-12-31",
        "detail": "a; b = c",
    }
    assert parse_note(format_note(fields)) == {**fields, "detail": "a, b = c"}
    assert parse_note(None) is None
    assert parse_note("FORECAST FALLBACK: reason=x") is None


# ---------------------------------------------------------------- sources and caching


def test_processed_file_is_used_when_present(tmp_path):
    (tmp_path / "processed").mkdir()
    d = DAY.date()
    _events((_at(d, 1), _at(d, 2))).to_parquet(tmp_path / "processed" / "outages.parquet", index=False)
    agent = CalendarSignalAgent(data_dir=tmp_path)
    mask = agent.run(DAY).outage
    assert agent.outage_source == "processed"
    assert {i for i, x in enumerate(mask) if x} == set(range(_slot(1), _slot(2)))


def test_unreadable_processed_file_falls_back_to_generated(tmp_path):
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "outages.parquet").write_text("not parquet")
    agent = CalendarSignalAgent(data_dir=tmp_path)
    agent.run(DAY)
    assert agent.outage_source == "generated"


def test_calendar_and_config_cached_after_first_run(monkeypatch, no_files, calendar):
    calls = {"gen": 0, "cfg": 0}

    def fake_generate(*args, **kwargs):
        calls["gen"] += 1
        return calendar

    def fake_cfg(*args, **kwargs):
        calls["cfg"] += 1
        return SIG

    monkeypatch.setattr(agent_mod, "generate_outage_events", fake_generate)
    monkeypatch.setattr(agent_mod, "load_signals_config", fake_cfg)
    agent = CalendarSignalAgent(data_dir=no_files)
    for k in range(3):
        agent.run(DAY + timedelta(days=k))
    assert calls == {"gen": 1, "cfg": 1}


def test_naive_injected_timestamps_are_site_local():
    df = _events((datetime(2024, 6, 3, 9), datetime(2024, 6, 3, 10)))
    mask = CalendarSignalAgent(outages_df=df).run(DAY).outage
    assert {i for i, x in enumerate(mask) if x} == set(range(_slot(9), _slot(10)))


def test_provenance_names_sources_and_dates(no_files):
    text = CalendarSignalAgent(data_dir=no_files).provenance()
    assert "as_of=2024-02" in text and "as_of=2022" in text and "as_of=2024-05" in text
    assert "0.62 kg/kWh flat" in text and "source=generated" in text


# ---------------------------------------------------------------- planner


def test_planner_never_imports_grid_during_outages(tmp_path):
    shutil.copytree(SAMPLE_DIR, tmp_path / "sample")
    signals = CalendarSignalAgent(data_dir=tmp_path)
    result = run_plan(
        DAY,
        0.5,
        forecast_agent=ModelForecastAgent(data_dir=tmp_path, models_dir=tmp_path / "models"),
        signal_agent=signals,
        assets=AssetAgent(),
        planner=MilpPlanner(),
        risk=StubRisk(),
    )
    assert result.status in ("optimal", "feasible") and is_safe(result)
    outage = signals.run(DAY).outage
    assert sum(outage) == len(range(_slot(19, 30), _slot(20, 45)))
    for t, down in enumerate(outage):
        if down:
            assert result.schedule.grid_import_kw[t] == pytest.approx(0.0, abs=1e-9)
    result.schedule.check_energy_balance(tol=1e-6)


def test_build_default_agents_uses_calendar_signal_agent():
    cfg = load_site_config()
    agents = build_default_agents(cfg)
    assert isinstance(agents["signal_agent"], CalendarSignalAgent)
    other = CalendarSignalAgent(outages_df=_events())
    assert build_default_agents(cfg, signal_agent=other)["signal_agent"] is other
