"""Tests for synthetic site load generation and sample data integrity (Issue #7)."""

from pathlib import Path

import numpy as np
import pandas as pd

from energy_brain.config import load_site_config
from energy_brain.data.synthetic_load import is_bangladesh_holiday


def test_load_sample_exists_and_loads():
    sample_path = Path(__file__).resolve().parents[1] / "data" / "sample" / "load_sample.parquet"
    assert sample_path.exists(), f"Sample file not found at {sample_path}"

    df = pd.read_parquet(sample_path)
    assert len(df) == 672  # 7 days * 96 slots

    required_cols = ["timestamp", "base_kw", "hvac_kw", "total_kw"]
    for col in required_cols:
        assert col in df.columns, f"Missing column {col} in load sample"

    ts = pd.to_datetime(df["timestamp"])
    assert ts.dt.tz is not None, "Timestamp must be timezone-aware"
    assert str(ts.dt.tz) == "Asia/Dhaka", f"Expected Asia/Dhaka, got {ts.dt.tz}"

    # Check for nulls
    assert df.isna().sum().sum() == 0, "Load dataset contains NaN values"

    # Non-negativity
    assert (df["base_kw"] >= 0.0).all()
    assert (df["hvac_kw"] >= 0.0).all()
    assert (df["total_kw"] >= 0.0).all()

    # Invariant: total = base + hvac
    assert np.allclose(df["total_kw"], df["base_kw"] + df["hvac_kw"], atol=0.05)


def test_load_physical_and_operational_bounds():
    cfg = load_site_config()
    sample_path = Path(__file__).resolve().parents[1] / "data" / "sample" / "load_sample.parquet"
    df = pd.read_parquet(sample_path)
    ts = pd.to_datetime(df["timestamp"])

    # Standby minimum load should be close to cfg.load.base_kw (250 kW)
    assert df["base_kw"].min() >= cfg.load.base_kw * 0.85
    assert df["base_kw"].min() <= cfg.load.base_kw * 1.15

    # Peak total load should approach cfg.load.peak_kw (1200 kW)
    assert df["total_kw"].max() >= 1000.0
    assert df["total_kw"].max() <= cfg.load.peak_kw * 1.10

    # HVAC bounds
    assert (df["hvac_kw"] >= 0.0).all()
    assert (df["hvac_kw"] <= cfg.hvac.rated_kw + 1.0).all()

    # Friday weekend check: Friday is weekly holiday, base load should remain near standby
    fridays = df[ts.dt.day_name() == "Friday"]
    assert len(fridays) == 96
    # On Friday, base machinery should not be running full production
    assert fridays["base_kw"].max() <= cfg.load.base_kw * 1.30


def test_bangladesh_holiday_detection():
    # Language Day
    lang_day = pd.Timestamp("2024-02-21").date()
    assert is_bangladesh_holiday(lang_day) is True

    # Eid-ul-Fitr 2024 window (April 11)
    eid_day = pd.Timestamp("2024-04-11").date()
    assert is_bangladesh_holiday(eid_day) is True

    # 2025 Eid dates
    assert is_bangladesh_holiday(pd.Timestamp("2025-04-01").date()) is True  # Eid-ul-Fitr
    assert is_bangladesh_holiday(pd.Timestamp("2025-06-07").date()) is True  # Eid-ul-Adha

    # 2026 Eid dates
    assert is_bangladesh_holiday(pd.Timestamp("2026-03-21").date()) is True  # Eid-ul-Fitr
    assert is_bangladesh_holiday(pd.Timestamp("2026-05-28").date()) is True  # Eid-ul-Adha

    # Normal working day (e.g. Wednesday 2024-05-15, Tuesday 2026-11-17)
    normal_day = pd.Timestamp("2024-05-15").date()
    assert is_bangladesh_holiday(normal_day) is False
    assert is_bangladesh_holiday(pd.Timestamp("2026-11-17").date()) is False
