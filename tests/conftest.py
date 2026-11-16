"""Shared fixtures for the agent schema / orchestrator tests (issue #13)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from energy_brain.stubs import StubAssetProvider, StubForecastAgent, StubPlanner, StubSignalAgent

DHAKA = ZoneInfo("Asia/Dhaka")


@pytest.fixture
def start() -> datetime:
    return datetime(2026, 11, 17, 0, 0, tzinfo=DHAKA)


@pytest.fixture
def forecast(start):
    return StubForecastAgent().run(start)


@pytest.fixture
def signals(start):
    return StubSignalAgent().run(start)


@pytest.fixture
def assets(start):
    return StubAssetProvider().run(start)


@pytest.fixture
def plan(forecast, signals, assets):
    return StubPlanner().plan(forecast, signals, assets, weight=0.5)
