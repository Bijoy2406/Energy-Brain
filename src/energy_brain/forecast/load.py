"""Site load quantile forecaster (P10/P50/P90) and persistence baseline (Issue #11).

Trains LightGBM quantile regression models to forecast the next 24 hours of inflexible
base factory load at 15-minute steps (excluding flexible HVAC and EV loads):
- Features: outdoor temperature, time of day, day of week, Friday holiday flag,
  Bangladesh public holiday indicator, month, day of year, and 24h / 168h historical lags.
- Benchmark: 24-hour persistence baseline evaluated on out-of-sample 2024 test set.
- Models saved to models/load_p10.txt, models/load_p50.txt, models/load_p90.txt.
- Metrics recorded in reports/forecast_metrics.json.
- Provides predict(start_time) -> DataFrame[timestamp, p10_kw, p50_kw, p90_kw] (96 slots).
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import lightgbm as lgb
import numpy as np
import pandas as pd

from energy_brain.config import load_site_config
from energy_brain.data.simulate_pv import resample_weather_to_15min
from energy_brain.data.synthetic_load import is_bangladesh_holiday

logger = logging.getLogger(__name__)

DHAKA_TZ = ZoneInfo("Asia/Dhaka")

DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_MODELS_DIR = DEFAULT_ROOT_DIR / "models"
DEFAULT_REPORTS_DIR = DEFAULT_ROOT_DIR / "reports"
DEFAULT_DATA_DIR = DEFAULT_ROOT_DIR / "data"

FEATURE_COLS = [
    "slot",
    "hour",
    "dayofweek",
    "is_friday",
    "is_holiday",
    "month",
    "dayofyear",
    "temperature_c",
    "load_lag_24h",
    "load_lag_168h",
]


def prepare_load_dataset(
    load_df: pd.DataFrame,
    weather_df: pd.DataFrame,
) -> pd.DataFrame:
    """Merge resampled 15-minute weather and site base load, engineering feature columns."""
    cfg = load_site_config()

    if "timestamp" in load_df.columns:
        load_series = load_df.set_index("timestamp")["base_kw"].copy()
    else:
        load_series = load_df["base_kw"].copy()

    w15 = resample_weather_to_15min(weather_df, tz_name=cfg.site.timezone)
    df = pd.DataFrame({"base_kw": load_series}).join(w15[["temperature_c"]], how="inner")

    df["slot"] = df.index.hour * 4 + df.index.minute // 15
    df["hour"] = df.index.hour
    df["dayofweek"] = df.index.dayofweek
    df["is_friday"] = (df.index.day_name() == "Friday").astype(int)
    df["is_holiday"] = [int(is_bangladesh_holiday(d.date())) for d in df.index]
    df["month"] = df.index.month
    df["dayofyear"] = df.index.dayofyear
    df["load_lag_24h"] = df["base_kw"].shift(96)
    df["load_lag_168h"] = df["base_kw"].shift(672)

    return df


def train_load_quantile_models(
    df: pd.DataFrame,
    train_years: tuple[int, ...] = (2022, 2023),
    test_year: int = 2024,
    models_dir: Path | None = None,
) -> tuple[dict[float, lgb.Booster], dict[str, float]]:
    """Train LightGBM quantile regression models (P10, P50, P90) and evaluate on test set."""
    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    df_clean = df.dropna(subset=FEATURE_COLS + ["base_kw"]).copy()

    train_mask = df_clean.index.year.isin(train_years)
    test_mask = df_clean.index.year == test_year

    X_train = df_clean.loc[train_mask, FEATURE_COLS]
    y_train = df_clean.loc[train_mask, "base_kw"]

    X_test = df_clean.loc[test_mask, FEATURE_COLS]
    y_test = df_clean.loc[test_mask, "base_kw"]

    # 1. Baseline: 24h persistence (same time yesterday)
    pers_pred = X_test["load_lag_24h"].values
    pers_mae = float(np.mean(np.abs(y_test.values - pers_pred)))
    pers_rmse = float(np.sqrt(np.mean((y_test.values - pers_pred) ** 2)))

    # 2. Train Quantile Regressors (P10, P50, P90)
    models: dict[float, lgb.Booster] = {}
    preds: dict[float, np.ndarray] = {}

    for q in [0.10, 0.50, 0.90]:
        q_tag = int(q * 100)
        logger.info("Training LightGBM P%d load quantile model...", q_tag)
        reg = lgb.LGBMRegressor(
            objective="quantile",
            alpha=q,
            n_estimators=120,
            learning_rate=0.08,
            num_leaves=31,
            random_state=42,
            verbose=-1,
        )
        reg.fit(X_train, y_train)
        booster = reg.booster_

        # Save model
        model_path = models_dir / f"load_p{q_tag}.txt"
        booster.save_model(str(model_path))
        models[q] = booster

        # Predict test set
        pred = np.clip(booster.predict(X_test), 0.0, None)
        preds[q] = pred

    # 3. Post-processing monotonicity: P10 <= P50 <= P90
    p10 = preds[0.10]
    p50 = np.maximum(p10, preds[0.50])
    p90 = np.maximum(p50, preds[0.90])

    lgb_mae = float(np.mean(np.abs(y_test.values - p50)))
    lgb_rmse = float(np.sqrt(np.mean((y_test.values - p50) ** 2)))
    mae_improvement = (pers_mae - lgb_mae) / pers_mae * 100.0

    actuals = y_test.values
    coverage = float(np.mean((actuals >= p10) & (actuals <= p90)) * 100.0)

    metrics = {
        "persistence_mae_kw": round(pers_mae, 3),
        "persistence_rmse_kw": round(pers_rmse, 3),
        "lightgbm_p50_mae_kw": round(lgb_mae, 3),
        "lightgbm_p50_rmse_kw": round(lgb_rmse, 3),
        "mae_improvement_pct": round(mae_improvement, 2),
        "p10_p90_coverage_pct": round(coverage, 2),
        "test_year": test_year,
        "train_years": list(train_years),
    }

    logger.info(
        "Load Benchmark: Persistence MAE = %.2f kW, LightGBM P50 MAE = %.2f kW (Gain: +%.1f%%)",
        pers_mae,
        lgb_mae,
        mae_improvement,
    )
    logger.info("P10-P90 Coverage = %.1f%% (Nominal: 80%%)", coverage)

    return models, metrics


def load_load_boosters(models_dir: Path | None = None) -> dict[float, lgb.Booster]:
    """Load existing load quantile boosters from models/."""
    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR

    boosters: dict[float, lgb.Booster] = {}
    for q in [0.10, 0.50, 0.90]:
        q_tag = int(q * 100)
        model_path = models_dir / f"load_p{q_tag}.txt"
        if not model_path.exists():
            raise FileNotFoundError(f"Model file {model_path} not found. Run training first.")
        boosters[q] = lgb.Booster(model_file=str(model_path))

    return boosters


def predict(
    start_time: datetime.datetime,
    load_df: pd.DataFrame | None = None,
    weather_df: pd.DataFrame | None = None,
    models_dir: Path | None = None,
) -> pd.DataFrame:
    """Predict 24-hour base site load with P10/P50/P90 bands at 15-minute steps.

    Parameters:
    - start_time: tz-aware datetime in Asia/Dhaka representing slot 0 (00:00).
    - load_df: optional load DataFrame (loads load.parquet by default).
    - weather_df: optional weather DataFrame (loads weather.parquet by default).
    - models_dir: path to directory containing load_p*.txt models.

    Returns:
    - DataFrame with exactly 96 rows and columns: [timestamp, p10_kw, p50_kw, p90_kw].
    - Guarantees: 0 <= p10 <= p50 <= p90.
    """
    if start_time.tzinfo is None:
        raise ValueError("start_time must be timezone-aware (Asia/Dhaka)")

    start_dhaka = start_time.astimezone(DHAKA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dhaka = start_dhaka + pd.Timedelta(hours=23, minutes=45)

    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR

    # Load models; if absent, train them
    try:
        boosters = load_load_boosters(models_dir)
    except FileNotFoundError:
        logger.info("Models not found; auto-training load models...")
        l_df = (
            load_df
            if load_df is not None
            else pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "load.parquet")
        )
        w_df = (
            weather_df
            if weather_df is not None
            else pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "weather.parquet")
        )
        full_df = prepare_load_dataset(l_df, w_df)
        boosters, _ = train_load_quantile_models(full_df, models_dir=models_dir)

    # Load data
    if load_df is None:
        load_df = pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "load.parquet")
    if weather_df is None:
        weather_df = pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "weather.parquet")

    full_dataset = prepare_load_dataset(load_df, weather_df)

    day_slots = pd.date_range(start=start_dhaka, end=end_dhaka, freq="15min", tz="Asia/Dhaka")
    if len(day_slots) != 96:
        raise ValueError(f"Expected 96 slots, got {len(day_slots)}")

    sub_df = full_dataset.reindex(day_slots)
    sub_df["load_lag_24h"] = sub_df["load_lag_24h"].ffill().bfill().fillna(250.0)
    sub_df["load_lag_168h"] = sub_df["load_lag_168h"].ffill().bfill().fillna(250.0)
    sub_df = sub_df.interpolate(method="time").ffill().bfill()

    X_pred = sub_df[FEATURE_COLS]

    preds = {}
    for q in [0.10, 0.50, 0.90]:
        preds[q] = np.clip(boosters[q].predict(X_pred), 0.0, None)

    # Monotonicity: P10 <= P50 <= P90
    p10 = preds[0.10]
    p50 = np.maximum(p10, preds[0.50])
    p90 = np.maximum(p50, preds[0.90])

    return pd.DataFrame(
        {
            "timestamp": day_slots,
            "p10_kw": np.round(p10, 2),
            "p50_kw": np.round(p50, 2),
            "p90_kw": np.round(p90, 2),
        }
    )


def run_training_pipeline(
    reports_dir: Path | None = None,
    models_dir: Path | None = None,
) -> dict[str, float]:
    """Train load models, evaluate on 2024 test set, and save forecast_metrics.json."""
    if reports_dir is None:
        reports_dir = DEFAULT_REPORTS_DIR
    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR

    reports_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    load_path = DEFAULT_DATA_DIR / "processed" / "load.parquet"
    weather_path = DEFAULT_DATA_DIR / "processed" / "weather.parquet"

    logger.info("Loading load and weather data for training...")
    load_df = pd.read_parquet(load_path)
    weather_df = pd.read_parquet(weather_path)

    full_df = prepare_load_dataset(load_df, weather_df)
    _, metrics = train_load_quantile_models(full_df, models_dir=models_dir)

    metrics_path = reports_dir / "forecast_metrics.json"
    if metrics_path.exists():
        with open(metrics_path, encoding="utf-8") as f:
            all_metrics = json.load(f)
    else:
        all_metrics = {}

    all_metrics["load"] = metrics
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2)

    logger.info("Saved load forecast metrics to %s", metrics_path)
    return metrics


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Site load quantile forecaster.")
    parser.add_argument("--train", action="store_true", help="Train models and record metrics")
    parser.add_argument(
        "--date",
        type=str,
        default="2024-06-03",
        help="Date for sample forecast (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    if args.train:
        run_training_pipeline()

    target_dt = datetime.datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=DHAKA_TZ)
    df_pred = predict(target_dt)
    print(f"\n24-Hour Load Forecast for {args.date} (96 slots):")
    print(df_pred.describe())


if __name__ == "__main__":
    main()
