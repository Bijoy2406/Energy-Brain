"""Solar PV quantile forecaster (P10/P50/P90) and persistence baseline (Issue #10).

Trains LightGBM quantile regression models on historical weather and PV data:
- Features: weather irradiance (GHI, DNI, DHI), cloud cover, temperature, humidity,
  wind speed, slot/hour/month/dayofyear calendar terms, and 24h PV lag.
- Evaluation against 24-hour persistence baseline on out-of-sample test set (2024).
- Saves trained models to models/solar_p10.txt, models/solar_p50.txt, models/solar_p90.txt.
- Records MAE, RMSE, improvement, and P10-P90 coverage to reports/forecast_metrics.json.
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

logger = logging.getLogger(__name__)

DHAKA_TZ = ZoneInfo("Asia/Dhaka")

DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_MODELS_DIR = DEFAULT_ROOT_DIR / "models"
DEFAULT_REPORTS_DIR = DEFAULT_ROOT_DIR / "reports"
DEFAULT_DATA_DIR = DEFAULT_ROOT_DIR / "data"

FEATURE_COLS = [
    "ghi_w_per_m2",
    "dni_w_per_m2",
    "dhi_w_per_m2",
    "cloud_cover_pct",
    "temperature_c",
    "relative_humidity_pct",
    "wind_speed_m_per_s",
    "slot",
    "hour",
    "month",
    "dayofyear",
    "pv_lag_24h",
]


def prepare_solar_dataset(
    weather_df: pd.DataFrame,
    pv_df: pd.DataFrame,
) -> pd.DataFrame:
    """Merge resampled 15-minute weather and PV output, engineering feature columns."""
    cfg = load_site_config()

    if "timestamp" in pv_df.columns:
        pv_series = pv_df.set_index("timestamp")["pv_kw"].copy()
    else:
        pv_series = pv_df["pv_kw"].copy()

    w15 = resample_weather_to_15min(weather_df, tz_name=cfg.site.timezone)
    df = w15.join(pv_series, how="inner")

    df["slot"] = df.index.hour * 4 + df.index.minute // 15
    df["hour"] = df.index.hour
    df["month"] = df.index.month
    df["dayofyear"] = df.index.dayofyear
    df["pv_lag_24h"] = df["pv_kw"].shift(96)

    return df


def train_solar_quantile_models(
    df: pd.DataFrame,
    train_years: tuple[int, ...] = (2022, 2023),
    test_year: int = 2024,
    models_dir: Path | None = None,
) -> tuple[dict[float, lgb.Booster], dict[str, float]]:
    """Train LightGBM quantile regression models (P10, P50, P90) and evaluate on test set."""
    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    df_clean = df.dropna(subset=FEATURE_COLS + ["pv_kw"]).copy()

    train_mask = df_clean.index.year.isin(train_years)
    test_mask = df_clean.index.year == test_year

    X_train = df_clean.loc[train_mask, FEATURE_COLS]
    y_train = df_clean.loc[train_mask, "pv_kw"]

    X_test = df_clean.loc[test_mask, FEATURE_COLS]
    y_test = df_clean.loc[test_mask, "pv_kw"]

    # 1. Baseline: 24h persistence (same time yesterday)
    pers_pred = X_test["pv_lag_24h"].values
    pers_mae = float(np.mean(np.abs(y_test.values - pers_pred)))
    pers_rmse = float(np.sqrt(np.mean((y_test.values - pers_pred) ** 2)))

    # 2. Train Quantile Regressors (P10, P50, P90)
    models: dict[float, lgb.Booster] = {}
    preds: dict[float, np.ndarray] = {}

    for q in [0.10, 0.50, 0.90]:
        q_tag = int(q * 100)
        logger.info("Training LightGBM P%d solar quantile model...", q_tag)
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
        model_path = models_dir / f"solar_p{q_tag}.txt"
        booster.save_model(str(model_path))
        models[q] = booster

        # Predict test set
        pred = np.clip(booster.predict(X_test), 0.0, None)
        # Night zeroing where GHI is 0
        pred[X_test["ghi_w_per_m2"] == 0.0] = 0.0
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
        "Solar Benchmark: Persistence MAE = %.2f kW, LightGBM P50 MAE = %.2f kW (Gain: +%.1f%%)",
        pers_mae,
        lgb_mae,
        mae_improvement,
    )
    logger.info("P10-P90 Coverage = %.1f%% (Nominal: 80%%)", coverage)

    return models, metrics


def load_solar_boosters(models_dir: Path | None = None) -> dict[float, lgb.Booster]:
    """Load existing solar quantile boosters from models/."""
    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR

    boosters: dict[float, lgb.Booster] = {}
    for q in [0.10, 0.50, 0.90]:
        q_tag = int(q * 100)
        model_path = models_dir / f"solar_p{q_tag}.txt"
        if not model_path.exists():
            raise FileNotFoundError(f"Model file {model_path} not found. Run training first.")
        boosters[q] = lgb.Booster(model_file=str(model_path))

    return boosters


def predict(
    start_time: datetime.datetime,
    weather_df: pd.DataFrame | None = None,
    pv_df: pd.DataFrame | None = None,
    models_dir: Path | None = None,
) -> pd.DataFrame:
    """Predict 24-hour solar PV generation with P10/P50/P90 bands at 15-minute steps.

    Parameters:
    - start_time: tz-aware datetime in Asia/Dhaka representing slot 0 (00:00).
    - weather_df: optional hourly/15-min weather DataFrame (loads weather.parquet by default).
    - pv_df: optional pv DataFrame for calculating 24h lag (loads pv.parquet by default).
    - models_dir: path to directory containing solar_p*.txt models.

    Returns:
    - DataFrame with exactly 96 rows and columns: [timestamp, p10_kw, p50_kw, p90_kw].
    - Guarantees: 0 <= p10 <= p50 <= p90, zero at night.
    """
    if start_time.tzinfo is None:
        raise ValueError("start_time must be timezone-aware (Asia/Dhaka)")

    start_dhaka = start_time.astimezone(DHAKA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dhaka = start_dhaka + pd.Timedelta(hours=23, minutes=45)

    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR

    # Load models; if absent, train them
    try:
        boosters = load_solar_boosters(models_dir)
    except FileNotFoundError:
        logger.info("Models not found; auto-training solar models...")
        w_df = (
            weather_df
            if weather_df is not None
            else pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "weather.parquet")
        )
        p_df = pv_df if pv_df is not None else pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "pv.parquet")
        full_df = prepare_solar_dataset(w_df, p_df)
        boosters, _ = train_solar_quantile_models(full_df, models_dir=models_dir)

    # Load required weather data
    if weather_df is None:
        weather_df = pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "weather.parquet")
    if pv_df is None:
        pv_df = pd.read_parquet(DEFAULT_DATA_DIR / "processed" / "pv.parquet")

    full_dataset = prepare_solar_dataset(weather_df, pv_df)

    day_slots = pd.date_range(start=start_dhaka, end=end_dhaka, freq="15min", tz="Asia/Dhaka")
    if len(day_slots) != 96:
        raise ValueError(f"Expected 96 slots, got {len(day_slots)}")

    # Extract features for the 96 slots
    sub_df = full_dataset.reindex(day_slots)

    # Impute missing feature lags if start_time is at the very beginning of the dataset
    if sub_df["pv_lag_24h"].isna().any():
        sub_df["pv_lag_24h"] = sub_df["pv_lag_24h"].fillna(0.0)
    sub_df = sub_df.interpolate(method="time").ffill().bfill()

    X_pred = sub_df[FEATURE_COLS]

    preds = {}
    for q in [0.10, 0.50, 0.90]:
        raw_pred = np.clip(boosters[q].predict(X_pred), 0.0, None)
        # Night zeroing where GHI is zero
        raw_pred[X_pred["ghi_w_per_m2"] == 0.0] = 0.0
        preds[q] = raw_pred

    # Monotonicity adjustment: P10 <= P50 <= P90
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
    """Train models, evaluate on 2024 test set, and save forecast_metrics.json."""
    if reports_dir is None:
        reports_dir = DEFAULT_REPORTS_DIR
    if models_dir is None:
        models_dir = DEFAULT_MODELS_DIR

    reports_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    weather_path = DEFAULT_DATA_DIR / "processed" / "weather.parquet"
    pv_path = DEFAULT_DATA_DIR / "processed" / "pv.parquet"

    logger.info("Loading weather and PV data for training...")
    weather_df = pd.read_parquet(weather_path)
    pv_df = pd.read_parquet(pv_path)

    full_df = prepare_solar_dataset(weather_df, pv_df)
    _, metrics = train_solar_quantile_models(full_df, models_dir=models_dir)

    # Save to reports/forecast_metrics.json
    metrics_path = reports_dir / "forecast_metrics.json"
    if metrics_path.exists():
        with open(metrics_path, encoding="utf-8") as f:
            all_metrics = json.load(f)
    else:
        all_metrics = {}

    all_metrics["solar"] = metrics
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, indent=2)

    logger.info("Saved solar forecast metrics to %s", metrics_path)
    return metrics


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Solar PV quantile forecaster.")
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
    print(f"\n24-Hour Solar Forecast for {args.date} (96 slots):")
    print(df_pred.describe())


if __name__ == "__main__":
    main()
