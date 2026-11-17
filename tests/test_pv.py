"""Tests for solar PV simulation and sample data integrity (Issue #6)."""

from pathlib import Path

import numpy as np
import pandas as pd

from energy_brain.config import load_site_config
from energy_brain.data.simulate_pv import calculate_pv_ac_power, resample_weather_to_15min


def test_pv_sample_exists_and_loads():
    sample_path = Path(__file__).resolve().parents[1] / "data" / "sample" / "pv_sample.parquet"
    assert sample_path.exists(), f"Sample file not found at {sample_path}"

    df = pd.read_parquet(sample_path)
    assert len(df) == 672  # 7 days * 96 slots

    assert "timestamp" in df.columns
    assert "pv_kw" in df.columns

    ts = pd.to_datetime(df["timestamp"])
    assert ts.dt.tz is not None, "Timestamp must be timezone-aware"
    assert str(ts.dt.tz) == "Asia/Dhaka", f"Expected Asia/Dhaka, got {ts.dt.tz}"

    # Check for nulls
    assert df["pv_kw"].isna().sum() == 0, "pv_kw contains NaN values"

    # Non-negative
    assert (df["pv_kw"] >= 0.0).all()

    # Peak generation should be positive and capped below system kWp (600 kWp)
    cfg = load_site_config()
    assert df["pv_kw"].max() > 200.0
    assert df["pv_kw"].max() <= cfg.solar.capacity_kwp

    # Nighttime generation (midnight) must be strictly 0.0
    midnight_rows = df[ts.dt.hour == 0]
    assert np.allclose(midnight_rows["pv_kw"], 0.0)


def test_resample_weather_to_15min():
    # 2 hours of dummy weather in Asia/Dhaka
    times = pd.date_range("2024-01-01 08:00:00", "2024-01-01 09:00:00", freq="1h", tz="Asia/Dhaka")
    df = pd.DataFrame(
        {
            "timestamp": times,
            "ghi_w_per_m2": [100.0, 200.0],
            "dni_w_per_m2": [50.0, 100.0],
            "dhi_w_per_m2": [50.0, 100.0],
            "temperature_c": [20.0, 22.0],
            "wind_speed_m_per_s": [2.0, 3.0],
        }
    )

    w15 = resample_weather_to_15min(df, tz_name="Asia/Dhaka")
    # Must cover 96 slots for full day (00:00 to 23:45)
    assert len(w15) == 96
    assert (w15.index.minute % 15 == 0).all()


def test_calculate_pv_ac_power_night_zero():
    cfg = load_site_config()
    # Midnight slot
    times = pd.date_range("2024-01-01 00:00:00", "2024-01-01 02:00:00", freq="15min", tz="Asia/Dhaka")
    df = pd.DataFrame(
        {
            "ghi_w_per_m2": np.zeros(len(times)),
            "dni_w_per_m2": np.zeros(len(times)),
            "dhi_w_per_m2": np.zeros(len(times)),
            "temperature_c": np.full(len(times), 18.0),
            "wind_speed_m_per_s": np.full(len(times), 2.0),
        },
        index=times,
    )

    res = calculate_pv_ac_power(df, cfg)
    assert (res["pv_kw"] == 0.0).all()
