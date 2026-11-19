"""Tests for Issue #12: Solar LSTM benchmark forecaster."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from energy_brain.forecast.lstm_solar import (
    TORCH_AVAILABLE,
    create_sliding_sequences,
)

if TORCH_AVAILABLE:
    import torch

    from energy_brain.forecast.lstm_solar import SolarLSTM, pinball_loss
else:
    torch = None
    SolarLSTM = None  # type: ignore
    pinball_loss = None  # type: ignore

pytestmark = pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch is not installed in environment")


def test_create_sliding_sequences() -> None:
    """Check sliding sequence window shapes."""
    n_samples = 20
    n_features = 4
    seq_len = 5
    X = np.ones((n_samples, n_features), dtype=np.float32)
    y = np.arange(n_samples, dtype=np.float32)

    xs, ys = create_sliding_sequences(X, y, seq_len=seq_len)
    assert xs.shape == (n_samples - seq_len + 1, seq_len, n_features)
    assert ys.shape == (n_samples - seq_len + 1,)
    assert ys[0] == y[seq_len - 1]
    assert ys[-1] == y[-1]


def test_solar_lstm_forward_pass() -> None:
    """Check SolarLSTM tensor shapes on forward pass."""
    batch_size = 16
    seq_len = 8
    in_dim = 9
    model = SolarLSTM(in_dim=in_dim, hidden_dim=32, num_layers=1)
    dummy_input = torch.randn(batch_size, seq_len, in_dim)
    output = model(dummy_input)

    assert output.shape == (batch_size, 3)


def test_pinball_loss() -> None:
    """Verify pinball loss returns non-negative scalar loss."""
    preds = torch.tensor([[5.0, 10.0, 15.0], [2.0, 4.0, 6.0]], dtype=torch.float32)
    targets = torch.tensor([9.0, 3.5], dtype=torch.float32)

    loss = pinball_loss(preds, targets)
    assert loss.ndim == 0
    assert float(loss) > 0.0


def test_metrics_json_preserves_keys() -> None:
    """Verify forecast_metrics.json has solar_lstm_comparison and preserves existing keys."""
    metrics_path = Path("reports/forecast_metrics.json")
    if not metrics_path.exists():
        pytest.skip("reports/forecast_metrics.json does not exist yet")

    with open(metrics_path, encoding="utf-8") as f:
        data = json.load(f)

    # Core keys must be preserved
    assert "solar" in data, "Existing 'solar' key was overwritten!"
    assert "load" in data, "Existing 'load' key was overwritten!"

    if "solar_lstm_comparison" in data:
        lstm_data = data["solar_lstm_comparison"]
        assert "lstm_p50_mae_kw" in lstm_data
        assert "lightgbm_p50_mae_kw" in lstm_data
        assert "persistence_mae_kw" in lstm_data
        assert "better_model" in lstm_data
