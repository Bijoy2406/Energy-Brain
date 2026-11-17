"""Tests for weather data download and sample integrity (Issue #5)."""

from pathlib import Path

import numpy as np
import pandas as pd


def test_weather_sample_exists_and_loads():
    sample_path = Path(__file__).resolve().parents[1] / "data" / "sample" / "weather_sample.parquet"
    assert sample_path.exists(), f"Sample file not found at {sample_path}"

    df = pd.read_parquet(sample_path)
    assert len(df) == 168  # 7 days * 24 hours

    required_cols = [
        "timestamp",
        "ghi_w_per_m2",
        "dni_w_per_m2",
        "dhi_w_per_m2",
        "temperature_c",
        "relative_humidity_pct",
        "cloud_cover_pct",
        "wind_speed_m_per_s",
    ]
    for col in required_cols:
        assert col in df.columns, f"Missing column {col} in weather sample"

    # Check timezone is Asia/Dhaka
    ts = pd.to_datetime(df["timestamp"])
    assert ts.dt.tz is not None, "Timestamp must be timezone-aware"
    assert str(ts.dt.tz) == "Asia/Dhaka", f"Expected Asia/Dhaka, got {ts.dt.tz}"

    # Check for nulls
    assert df.isna().sum().sum() == 0, "Weather sample contains NaN values"

    # Sanity checks on values
    assert (df["ghi_w_per_m2"] >= 0).all()
    assert (df["dni_w_per_m2"] >= 0).all()
    assert (df["dhi_w_per_m2"] >= 0).all()
    assert (df["wind_speed_m_per_s"] >= 0).all()
    assert ((df["relative_humidity_pct"] >= 0) & (df["relative_humidity_pct"] <= 100)).all()
    assert ((df["cloud_cover_pct"] >= 0) & (df["cloud_cover_pct"] <= 100)).all()

    # Temperature in Dhaka should be reasonable (e.g. 10 to 45 °C)
    assert ((df["temperature_c"] >= 10.0) & (df["temperature_c"] <= 45.0)).all()

    # Daytime peak should reach at least 200 W/m2 in June
    assert df["ghi_w_per_m2"].max() > 200.0

    # Nighttime (midnight) irradiance must be zero
    midnight_rows = df[ts.dt.hour == 0]
    assert np.allclose(midnight_rows["ghi_w_per_m2"], 0.0)
