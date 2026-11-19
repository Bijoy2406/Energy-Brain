"""Generate synthetic 15-minute electricity load profile for the site (Issue #7).

Models electricity demand for a mid-size RMG garment factory in Ashulia/Dhaka:
- Fixed inflexible base load: factory machinery (cutting/sewing/steam/lights), background
  unoccupied load (250 kW), Friday weekly holiday, and major Bangladesh public holidays.
- Flexible thermal cooling load: HVAC power driven by outdoor temperature from weather.parquet
  with occupied minimum (100 kW) and rated capacity (250 kW).
- Total site load = base_kw + hvac_kw (peaks at ~1200 kW on hot working days).
- EV fleet charging is scheduled separately by the Planner agent.

Outputs:
- data/processed/load.parquet (timestamp, base_kw, hvac_kw, total_kw)
- data/sample/load_sample.parquet & load_sample.csv (1-week test sample)
"""

from __future__ import annotations

import argparse
import datetime
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from energy_brain.config import SiteConfig, load_site_config
from energy_brain.data.simulate_pv import resample_weather_to_15min

logger = logging.getLogger(__name__)

DHAKA_TZ = ZoneInfo("Asia/Dhaka")

# Major Bangladesh public holidays (2022 - 2024)
BANGLADESH_HOLIDAYS_FIXED = {
    (2, 21),  # International Mother Language Day
    (3, 17),  # Sheikh Mujibur Rahman Birthday
    (3, 26),  # Independence Day
    (4, 14),  # Pohela Boishakh (Bengali New Year)
    (5, 1),  # May Day
    (8, 15),  # National Mourning Day
    (12, 16),  # Victory Day
    (12, 25),  # Christmas Day
}

# Major Lunar / Eid festival multi-day windows
BANGLADESH_EID_WINDOWS = [
    # 2022
    ("2022-05-02", "2022-05-04"),  # Eid-ul-Fitr
    ("2022-07-09", "2022-07-11"),  # Eid-ul-Adha
    # 2023
    ("2023-04-21", "2023-04-23"),  # Eid-ul-Fitr
    ("2023-06-28", "2023-06-30"),  # Eid-ul-Adha
    # 2024
    ("2024-04-10", "2024-04-12"),  # Eid-ul-Fitr
    ("2024-06-16", "2024-06-18"),  # Eid-ul-Adha
    # 2025
    ("2025-03-31", "2025-04-02"),  # Eid-ul-Fitr
    ("2025-06-06", "2025-06-08"),  # Eid-ul-Adha
    # 2026
    ("2026-03-20", "2026-03-22"),  # Eid-ul-Fitr
    ("2026-05-27", "2026-05-29"),  # Eid-ul-Adha
]


def is_bangladesh_holiday(date: datetime.date) -> bool:
    """Return True if the date is a recognized public holiday or Eid festival in Bangladesh."""
    if (date.month, date.day) in BANGLADESH_HOLIDAYS_FIXED:
        return True
    date_str = date.isoformat()
    for start_str, end_str in BANGLADESH_EID_WINDOWS:
        if start_str <= date_str <= end_str:
            return True
    return False


def compute_base_machinery_multiplier(
    dt_index: pd.DatetimeIndex,
    weekend_days: list[str],
) -> np.ndarray:
    """Compute normalized factory machinery activity profile (0.0 to 1.0) for each slot.

    - Weekends (Friday) & public holidays: 0.0 (standby only).
    - Shift 1 (08:00 - 17:00): Full production lines running (0.95 - 1.0), with lunch pause (13:00 - 14:00).
    - Shift 2 Overtime (17:00 - 20:00): Partial production lines (~0.60).
    - Ramp-up (07:30 - 08:00) and ramp-down (20:00 - 20:30).
    - Night (20:30 - 07:30): 0.0.
    """
    multipliers = np.zeros(len(dt_index), dtype=float)

    for i, ts in enumerate(dt_index):
        # Check weekend or holiday
        day_name = ts.day_name()
        if day_name in weekend_days or is_bangladesh_holiday(ts.date()):
            multipliers[i] = 0.0
            continue

        hour = ts.hour + ts.minute / 60.0

        if hour < 7.5:
            multipliers[i] = 0.0
        elif 7.5 <= hour < 8.0:
            # Morning ramp-up
            multipliers[i] = (hour - 7.5) / 0.5 * 0.95
        elif 8.0 <= hour < 13.0:
            # Morning full production
            multipliers[i] = 1.0
        elif 13.0 <= hour < 14.0:
            # Lunch break: partial shutdown of sewing lines
            multipliers[i] = 0.82
        elif 14.0 <= hour < 17.0:
            # Afternoon full production
            multipliers[i] = 1.0
        elif 17.0 <= hour < 20.0:
            # Overtime shift: reduced active lines
            multipliers[i] = 0.60
        elif 20.0 <= hour < 20.5:
            # Evening ramp-down
            multipliers[i] = (20.5 - hour) / 0.5 * 0.60
        else:
            multipliers[i] = 0.0

    return multipliers


def generate_synthetic_load(
    weather_15min: pd.DataFrame,
    cfg: SiteConfig,
    random_seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic 15-minute load components (base_kw, hvac_kw, total_kw)."""
    rng = np.random.default_rng(random_seed)
    times = weather_15min.index

    # 1. Base Inflexible Load
    # Background night/unoccupied load = cfg.load.base_kw (250 kW)
    # Peak machinery load = peak_kw (1200 kW) - hvac.rated_kw (250 kW) = 950 kW
    standby_base_kw = cfg.load.base_kw
    max_machinery_kw = cfg.load.peak_kw - cfg.hvac.rated_kw

    machinery_mult = compute_base_machinery_multiplier(times, cfg.load.weekend_days)

    # Autocorrelated stochastic noise (~1.5% std dev) to model realistic machine line fluctuations
    raw_noise = rng.normal(0.0, 0.015, len(times))
    noise = np.zeros(len(times), dtype=float)
    noise[0] = raw_noise[0]
    for t in range(1, len(times)):
        noise[t] = 0.85 * noise[t - 1] + 0.15 * raw_noise[t]

    base_kw = standby_base_kw + machinery_mult * (max_machinery_kw - standby_base_kw)
    base_kw = base_kw * (1.0 + noise)
    base_kw = np.clip(base_kw, standby_base_kw * 0.90, max_machinery_kw * 1.05)

    # 2. Flexible Thermal HVAC Cooling Load
    # Driven by ambient temperature from weather data
    temp = weather_15min["temperature_c"].values
    hvac_kw = np.zeros(len(times), dtype=float)

    hvac_rated = cfg.hvac.rated_kw
    hvac_min_occ = cfg.hvac.min_kw_when_occupied
    comfort_min = cfg.hvac.comfort_min_c  # 24.0 °C

    for i, ts in enumerate(times):
        day_name = ts.day_name()
        is_off_day = (day_name in cfg.load.weekend_days) or is_bangladesh_holiday(ts.date())
        hour = ts.hour + ts.minute / 60.0
        is_occupied = (not is_off_day) and (7.5 <= hour < 20.0)

        t_amb = temp[i]

        if is_occupied:
            # During occupied hours: minimum cooling is guaranteed for worker ventilation
            # Additional cooling scales with heat above comfort threshold (24°C up to 38°C)
            delta_t = max(0.0, t_amb - comfort_min)
            thermal_demand = hvac_min_occ + (delta_t / 14.0) * (hvac_rated - hvac_min_occ)
            hvac_kw[i] = np.clip(thermal_demand, hvac_min_occ, hvac_rated)
        else:
            # Unoccupied / night / Friday: minimal setback or off
            if t_amb > 32.0:
                # Modest overnight protection for fabric inventory
                hvac_kw[i] = 15.0
            else:
                hvac_kw[i] = 0.0

    # 3. Total Load
    total_kw = base_kw + hvac_kw

    return pd.DataFrame(
        {
            "timestamp": times,
            "base_kw": np.round(base_kw, 2),
            "hvac_kw": np.round(hvac_kw, 2),
            "total_kw": np.round(total_kw, 2),
        }
    )


def build_synthetic_load_pipeline(
    weather_path: Path | None = None,
    output_path: Path | None = None,
    sample_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Execute the end-to-end synthetic load generation pipeline."""
    cfg = load_site_config()
    root_dir = Path(__file__).resolve().parents[3]

    if weather_path is None:
        weather_path = root_dir / "data" / "processed" / "weather.parquet"
    if output_path is None:
        output_path = root_dir / "data" / "processed" / "load.parquet"
    if sample_path is None:
        sample_path = root_dir / "data" / "sample" / "load_sample.parquet"

    if not weather_path.exists():
        raise FileNotFoundError(f"Weather dataset not found at {weather_path}.")

    logger.info("Loading weather data from %s...", weather_path)
    weather_df = pd.read_parquet(weather_path)
    weather_15min = resample_weather_to_15min(weather_df, tz_name=cfg.site.timezone)

    logger.info("Generating synthetic load profile for %s...", cfg.site.name)
    load_df = generate_synthetic_load(weather_15min, cfg)

    # Save full processed load dataset
    output_path.parent.mkdir(parents=True, exist_ok=True)
    load_df.to_parquet(output_path, index=False)
    logger.info("Saved %d slots of synthetic load to %s", len(load_df), output_path)

    # Save 1-week sample (matching June 1 - June 8, 2024 = 672 slots)
    sample_dir = sample_path.parent
    sample_dir.mkdir(parents=True, exist_ok=True)

    sample_mask = (load_df["timestamp"] >= "2024-06-01 00:00:00+06:00") & (
        load_df["timestamp"] < "2024-06-08 00:00:00+06:00"
    )
    sample_df = load_df[sample_mask].copy()
    if len(sample_df) == 0:
        sample_df = load_df.iloc[:672].copy()

    sample_df.to_parquet(sample_path, index=False)
    csv_sample_path = sample_dir / "load_sample.csv"
    sample_df.to_csv(csv_sample_path, index=False)
    logger.info(
        "Saved 1-week load sample (%d rows) to %s and %s",
        len(sample_df),
        sample_path,
        csv_sample_path,
    )

    # Summary statistics
    logger.info(
        "Load Statistics: Max Total = %.2f kW, Min Total = %.2f kW, Mean Total = %.2f kW",
        load_df["total_kw"].max(),
        load_df["total_kw"].min(),
        load_df["total_kw"].mean(),
    )
    logger.info(
        "Components: Max Base = %.2f kW, Max HVAC = %.2f kW",
        load_df["base_kw"].max(),
        load_df["hvac_kw"].max(),
    )

    return load_df, sample_df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Generate synthetic load profile.")
    parser.parse_args()
    build_synthetic_load_pipeline()


if __name__ == "__main__":
    main()
