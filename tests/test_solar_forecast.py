"""Tests for solar PV quantile forecaster and persistence baseline (Issue #10)."""

import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from energy_brain.forecast.solar import predict

DHAKA = ZoneInfo("Asia/Dhaka")


def test_forecast_metrics_beats_persistence():
    metrics_path = Path(__file__).resolve().parents[1] / "reports" / "forecast_metrics.json"
    assert metrics_path.exists(), "forecast_metrics.json not found"

    with open(metrics_path, encoding="utf-8") as f:
        metrics = json.load(f)

    assert "solar" in metrics, "solar key missing in forecast_metrics.json"
    solar_m = metrics["solar"]

    # Model must beat persistence baseline
    assert solar_m["lightgbm_p50_mae_kw"] < solar_m["persistence_mae_kw"]
    assert solar_m["lightgbm_p50_rmse_kw"] < solar_m["persistence_rmse_kw"]
    assert solar_m["mae_improvement_pct"] > 50.0  # Significant gain required

    # Uncertainty band coverage must be healthy
    assert solar_m["p10_p90_coverage_pct"] >= 75.0


def test_solar_predict_shape_and_monotonicity():
    day = datetime.datetime(2024, 6, 3, 0, 0, tzinfo=DHAKA)
    pred_df = predict(day)

    # Exactly 96 slots
    assert len(pred_df) == 96
    required_cols = ["timestamp", "p10_kw", "p50_kw", "p90_kw"]
    for col in required_cols:
        assert col in pred_df.columns

    # Non-negative
    assert (pred_df["p10_kw"] >= 0.0).all()
    assert (pred_df["p50_kw"] >= 0.0).all()
    assert (pred_df["p90_kw"] >= 0.0).all()

    # Monotonicity: P10 <= P50 <= P90 for every slot
    p10 = pred_df["p10_kw"].values
    p50 = pred_df["p50_kw"].values
    p90 = pred_df["p90_kw"].values
    assert (p10 <= p50 + 1e-5).all(), "Violation: p10 > p50"
    assert (p50 <= p90 + 1e-5).all(), "Violation: p50 > p90"

    # Nighttime slots must be zero
    ts = pd.to_datetime(pred_df["timestamp"])
    midnight_slots = pred_df[ts.dt.hour == 0]
    assert np.allclose(midnight_slots["p10_kw"], 0.0)
    assert np.allclose(midnight_slots["p50_kw"], 0.0)
    assert np.allclose(midnight_slots["p90_kw"], 0.0)


def test_solar_predict_naive_datetime_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        predict(datetime.datetime(2024, 6, 3, 0, 0))
