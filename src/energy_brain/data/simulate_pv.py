"""Simulate site solar (PV) AC power output with pvlib (Issue #6).

Turns the cleaned weather history into a 15-minute resolution PV AC power series
matching the site and asset specifications in config/site.yaml.

Output:
- data/processed/pv.parquet (timestamp, pv_kw)
- data/sample/pv_sample.parquet & pv_sample.csv (1-week test sample)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pvlib

from energy_brain.config import SiteConfig, load_site_config

logger = logging.getLogger(__name__)

DHAKA_TZ = ZoneInfo("Asia/Dhaka")


def resample_weather_to_15min(weather_df: pd.DataFrame, tz_name: str = "Asia/Dhaka") -> pd.DataFrame:
    """Resample hourly weather data to 15-minute intervals covering complete 96-slot days.

    Uses time-based linear interpolation between hourly points, and forward fills
    the final 3 slots of the last hour to ensure complete 96-slot days.
    """
    if "timestamp" in weather_df.columns:
        df = weather_df.set_index("timestamp").copy()
    else:
        df = weather_df.copy()

    start_ts = df.index.min().floor("D")
    end_ts = df.index.max().floor("D") + pd.Timedelta(hours=23, minutes=45)

    full_15min_index = pd.date_range(
        start=start_ts,
        end=end_ts,
        freq="15min",
        tz=tz_name,
        name="timestamp",
    )

    resampled = df.reindex(df.index.union(full_15min_index))
    resampled = resampled.interpolate(method="time").ffill().bfill()
    resampled = resampled.reindex(full_15min_index)

    return resampled


def calculate_pv_ac_power(weather_15min: pd.DataFrame, cfg: SiteConfig) -> pd.DataFrame:
    """Calculate 15-minute PV AC power output in kW using pvlib.

    Pipeline:
    1. Solar position calculation (zenith, azimuth, elevation) from site coords.
    2. Strict nighttime zeroing where apparent elevation <= 0.
    3. Extraterrestrial irradiance calculation.
    4. Plane-of-Array (POA) irradiance calculation using the Hay-Davies transposition model.
    5. Cell temperature calculation using Faiman model.
    6. DC power calculation using PVWatts DC model with standard temp coefficient (-0.4%/°C).
    7. System losses derating (14% default from config/site.yaml).
    8. AC power calculation using PVWatts inverter model (nominal 96% efficiency).
    """
    times = weather_15min.index
    loc = pvlib.location.Location(
        latitude=cfg.site.latitude,
        longitude=cfg.site.longitude,
        tz=cfg.site.timezone,
    )

    solpos = loc.get_solarposition(times)

    # Clean nighttime irradiance: when sun is below horizon, irradiance must be zero
    is_night = solpos["apparent_elevation"] <= 0.0
    w_clean = weather_15min.copy()
    w_clean.loc[is_night, ["ghi_w_per_m2", "dni_w_per_m2", "dhi_w_per_m2"]] = 0.0

    dni_extra = pvlib.irradiance.get_extra_radiation(times)

    # In-plane total irradiance (POA)
    total_irr = pvlib.irradiance.get_total_irradiance(
        surface_tilt=cfg.solar.tilt_deg,
        surface_azimuth=cfg.solar.azimuth_deg,
        solar_zenith=solpos["apparent_zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=w_clean["dni_w_per_m2"],
        ghi=w_clean["ghi_w_per_m2"],
        dhi=w_clean["dhi_w_per_m2"],
        dni_extra=dni_extra,
        model="haydavies",
    )
    poa_global = total_irr["poa_global"].fillna(0.0).clip(lower=0.0)

    # Cell temperature
    temp_cell = pvlib.temperature.faiman(
        poa_global=poa_global,
        temp_air=w_clean["temperature_c"],
        wind_speed=w_clean["wind_speed_m_per_s"],
    )

    # PVWatts DC model
    pdc0_watts = cfg.solar.capacity_kwp * 1000.0
    pdc = pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=poa_global,
        temp_cell=temp_cell,
        pdc0=pdc0_watts,
        gamma_pdc=-0.004,
        temp_ref=25.0,
    )

    # Net DC after balance-of-system losses
    losses_frac = cfg.solar.system_losses_pct / 100.0
    pdc_net = pdc * (1.0 - losses_frac)

    # Inverter AC output
    pac_watts = pvlib.inverter.pvwatts(pdc=pdc_net, pdc0=pdc0_watts, eta_inv_nom=0.96)
    pac_kw = (pac_watts / 1000.0).fillna(0.0).clip(lower=0.0)

    # Hard-zero at night to avoid any numerical residual
    pac_kw.loc[is_night] = 0.0

    result = pd.DataFrame(
        {
            "timestamp": times,
            "pv_kw": np.round(pac_kw.values, 2),
            "poa_global_w_per_m2": np.round(poa_global.values, 2),
            "cell_temperature_c": np.round(temp_cell.values, 2),
        }
    )
    return result


def simulate_pv_pipeline(
    weather_path: Path | None = None,
    output_path: Path | None = None,
    sample_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Execute the end-to-end PV simulation pipeline."""
    cfg = load_site_config()
    root_dir = Path(__file__).resolve().parents[3]

    if weather_path is None:
        weather_path = root_dir / "data" / "processed" / "weather.parquet"
    if output_path is None:
        output_path = root_dir / "data" / "processed" / "pv.parquet"
    if sample_path is None:
        sample_path = root_dir / "data" / "sample" / "pv_sample.parquet"

    if not weather_path.exists():
        raise FileNotFoundError(f"Weather dataset not found at {weather_path}. Run download_weather first.")

    logger.info("Loading weather data from %s...", weather_path)
    weather_df = pd.read_parquet(weather_path)

    logger.info("Resampling weather to 15-minute intervals...")
    weather_15min = resample_weather_to_15min(weather_df, tz_name=cfg.site.timezone)

    logger.info("Simulating %s kWp solar system with pvlib...", cfg.solar.capacity_kwp)
    pv_df = calculate_pv_ac_power(weather_15min, cfg)

    # Save processed pv output (timestamp, pv_kw)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pv_export = pv_df[["timestamp", "pv_kw"]].copy()
    pv_export.to_parquet(output_path, index=False)
    logger.info("Saved %d slots of PV output to %s", len(pv_export), output_path)

    # Save 1-week sample (matching June 1 - June 8, 2024 = 672 slots)
    sample_dir = sample_path.parent
    sample_dir.mkdir(parents=True, exist_ok=True)

    sample_mask = (pv_df["timestamp"] >= "2024-06-01 00:00:00+06:00") & (
        pv_df["timestamp"] < "2024-06-08 00:00:00+06:00"
    )
    sample_df = pv_df[sample_mask][["timestamp", "pv_kw"]].copy()
    if len(sample_df) == 0:
        sample_df = pv_df.iloc[:672][["timestamp", "pv_kw"]].copy()

    sample_df.to_parquet(sample_path, index=False)
    csv_sample_path = sample_dir / "pv_sample.csv"
    sample_df.to_csv(csv_sample_path, index=False)
    logger.info("Saved 1-week PV sample (%d rows) to %s and %s", len(sample_df), sample_path, csv_sample_path)

    # Sanity checks summary
    max_kw = pv_df["pv_kw"].max()
    annual_mwh = [
        pv_df[pv_df["timestamp"].dt.year == y]["pv_kw"].sum() * 0.25 / 1000.0
        for y in pv_df["timestamp"].dt.year.unique()
    ]
    logger.info("PV Simulation Sanity: Peak kW = %.2f kW, Annual MWh = %s", max_kw, annual_mwh)

    return pv_df, sample_df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Simulate site PV output with pvlib.")
    parser.parse_args()
    simulate_pv_pipeline()


if __name__ == "__main__":
    main()
