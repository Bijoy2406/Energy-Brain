"""Tests for synthetic load-shedding (outage) calendar generation (Issue #9)."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from energy_brain.data.outages import generate_outage_events, get_daily_outage_mask

DHAKA = ZoneInfo("Asia/Dhaka")


def test_outages_sample_exists_and_loads():
    sample_path = Path(__file__).resolve().parents[1] / "data" / "sample" / "outages_sample.parquet"
    assert sample_path.exists(), f"Sample file not found at {sample_path}"

    df = pd.read_parquet(sample_path)
    required_cols = ["start", "end", "duration_hours", "reason"]
    for col in required_cols:
        assert col in df.columns, f"Missing column {col} in outages sample"

    # Interval checks
    start_ts = pd.to_datetime(df["start"])
    end_ts = pd.to_datetime(df["end"])

    assert str(start_ts.dt.tz) == "Asia/Dhaka"
    assert str(end_ts.dt.tz) == "Asia/Dhaka"
    assert (end_ts > start_ts).all()

    # Duration check
    durations = (end_ts - start_ts).dt.total_seconds() / 3600.0
    assert np.allclose(df["duration_hours"].values, durations.values, atol=0.01)


def test_outage_calendar_seasonal_distribution():
    # Generated in memory: data/processed/ is git-ignored, so it does not exist in CI.
    df = generate_outage_events()

    assert len(df) > 200, "Expected at least 200 outage events over 3 years"

    start_ts = pd.to_datetime(df["start"])
    summer_outages = df[(start_ts.dt.month >= 4) & (start_ts.dt.month <= 9)]
    winter_outages = df[(start_ts.dt.month >= 11) | (start_ts.dt.month <= 2)]

    # Summer should have noticeably more outages due to heatwave deficits
    summer_rate = len(summer_outages) / 6.0  # per month
    winter_rate = len(winter_outages) / 4.0  # per month
    assert summer_rate > winter_rate


def test_get_daily_outage_mask():
    # Construct a dummy outage table with one outage from 18:00 to 19:30
    day = datetime(2024, 6, 1, 0, 0, tzinfo=DHAKA)
    outages_df = pd.DataFrame(
        [
            {
                "start": datetime(2024, 6, 1, 18, 0, tzinfo=DHAKA),
                "end": datetime(2024, 6, 1, 19, 30, tzinfo=DHAKA),
                "duration_hours": 1.5,
                "reason": "evening_peak_load_shedding",
            }
        ]
    )

    mask = get_daily_outage_mask(day, outages_df, n_slots=96, slot_minutes=15)
    assert len(mask) == 96
    assert all(isinstance(x, bool) for x in mask)

    # 18:00 is slot 18 * 4 = 72
    # 19:30 is slot 19.5 * 4 = 78
    # Slots 72 to 77 (6 slots) should be True
    assert mask[72] is True
    assert mask[77] is True
    assert mask[78] is False  # slot 78 starts at 19:30 (after the outage ends)
    assert mask[71] is False  # slot 71 is 17:45 - 18:00 (before outage starts)
    assert sum(mask) == 6


def test_get_daily_outage_mask_naive_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        get_daily_outage_mask(datetime(2024, 6, 1, 0, 0), pd.DataFrame())
