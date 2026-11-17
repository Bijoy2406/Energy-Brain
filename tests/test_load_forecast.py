"""Tests for site load quantile forecaster and persistence baseline (Issue #11)."""

import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from energy_brain.config import load_site_config
from energy_brain.forecast.load import predict

DHAKA = ZoneInfo("Asia/Dhaka")
ROOT = Path(__file__).resolve().parents[1]

# data/processed/ and models/ are git-ignored, so predict() can only run where the pipeline was run locally.
needs_trained_load = pytest.mark.skipif(
    not (ROOT / "data" / "processed" / "load.parquet").exists()
    or not (ROOT / "data" / "processed" / "weather.parquet").exists()
    or not (ROOT / "models" / "load_p50.txt").exists(),
    reason="needs local data/processed/{load,weather}.parquet and models/load_*.txt",
)


def test_load_forecast_metrics_beats_persistence():
    metrics_path = Path(__file__).resolve().parents[1] / "reports" / "forecast_metrics.json"
    assert metrics_path.exists(), "forecast_metrics.json not found"

    with open(metrics_path, encoding="utf-8") as f:
        metrics = json.load(f)

    assert "load" in metrics, "load key missing in forecast_metrics.json"
    load_m = metrics["load"]

    # Model must beat persistence baseline decisively
    assert load_m["lightgbm_p50_mae_kw"] < load_m["persistence_mae_kw"]
    assert load_m["lightgbm_p50_rmse_kw"] < load_m["persistence_rmse_kw"]
    assert load_m["mae_improvement_pct"] > 70.0

    # Uncertainty coverage
    assert load_m["p10_p90_coverage_pct"] >= 75.0


@needs_trained_load
def test_load_predict_shape_and_monotonicity():
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


@needs_trained_load
def test_load_predict_friday_weekend_behavior():
    cfg = load_site_config()
    # June 7, 2024 is a Friday (weekly holiday in Bangladesh)
    friday = datetime.datetime(2024, 6, 7, 0, 0, tzinfo=DHAKA)
    pred_df = predict(friday)

    # On Friday, factory machinery should be off; base load should stay near standby (250 kW)
    assert pred_df["p50_kw"].max() <= cfg.load.base_kw * 1.35


def test_load_predict_naive_datetime_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        predict(datetime.datetime(2024, 6, 3, 0, 0))
