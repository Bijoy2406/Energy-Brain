"""Download and cache Dhaka weather history (NASA POWER + Open-Meteo).

Implements Issue #5:
- Downloads 3 years (2022-2024) of hourly weather for the site specified in config/site.yaml
- Primary source: NASA POWER (MERRA-2 / GEOS-FP)
- Secondary / cross-check source: Open-Meteo Historical Archive API
- Caches raw JSON responses in data/raw/
- Cleans and aligns to Asia/Dhaka timezone in data/processed/weather.parquet
- Commits a 1-week sample to data/sample/weather_sample.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from energy_brain.config import load_site_config

logger = logging.getLogger(__name__)

DHAKA_TZ = ZoneInfo("Asia/Dhaka")

# NASA POWER endpoint and parameters
NASA_POWER_HOURLY_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
NASA_PARAMETERS = [
    "ALLSKY_SFC_SW_DWN",  # GHI (Wh/m^2 = W/m^2 hourly)
    "ALLSKY_SFC_SW_DIFF",  # DHI (Wh/m^2)
    "ALLSKY_SFC_SW_DNI",  # DNI (Wh/m^2)
    "T2M",  # 2m temperature (°C)
    "RH2M",  # 2m relative humidity (%)
    "CLOUD_AMT",  # Cloud amount (%)
    "WS2M",  # 2m wind speed (m/s)
]

# Open-Meteo archive endpoint and hourly variables
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_VARIABLES = [
    "shortwave_radiation",  # GHI (W/m^2)
    "diffuse_radiation",  # DHI (W/m^2)
    "direct_normal_irradiance",  # DNI (W/m^2)
    "temperature_2m",  # °C
    "relative_humidity_2m",  # %
    "cloud_cover",  # %
    "wind_speed_10m",  # m/s
]


def fetch_open_meteo_hourly(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    raw_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch hourly historical data from Open-Meteo Archive API."""
    if raw_path and raw_path.exists() and not force:
        logger.info("Loading cached Open-Meteo raw data from %s", raw_path)
        with open(raw_path, encoding="utf-8") as f:
            return json.load(f)

    logger.info(
        "Downloading Open-Meteo weather (%s to %s) for (%.4f, %.4f)...", start_date, end_date, lat, lon
    )
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": OPEN_METEO_VARIABLES,
        "timezone": "Asia/Dhaka",
        "wind_speed_unit": "ms",
    }
    response = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=60)
    response.raise_for_status()
    data = response.json()

    if raw_path:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved raw Open-Meteo data to %s", raw_path)

    return data


def fetch_nasa_power_year(
    lat: float,
    lon: float,
    year: int,
    raw_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch one calendar year of hourly data from NASA POWER API."""
    if raw_path and raw_path.exists() and not force:
        logger.info("Loading cached NASA POWER raw data from %s", raw_path)
        with open(raw_path, encoding="utf-8") as f:
            return json.load(f)

    start_str = f"{year}0101"
    end_str = f"{year}1231"
    logger.info("Downloading NASA POWER weather for %d...", year)
    params = {
        "parameters": ",".join(NASA_PARAMETERS),
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "start": start_str,
        "end": end_str,
        "format": "JSON",
        "time-standard": "UTC",
    }
    response = requests.get(NASA_POWER_HOURLY_URL, params=params, timeout=120)
    response.raise_for_status()
    data = response.json()

    if raw_path:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info("Saved raw NASA POWER data to %s", raw_path)

    return data


def parse_open_meteo(data: dict[str, Any]) -> pd.DataFrame:
    """Parse Open-Meteo JSON into a standardized Asia/Dhaka DataFrame."""
    hourly = data["hourly"]
    times = pd.to_datetime(hourly["time"]).tz_localize("Asia/Dhaka")

    df = pd.DataFrame(
        {
            "timestamp": times,
            "ghi_w_per_m2": hourly.get("shortwave_radiation", []),
            "dni_w_per_m2": hourly.get("direct_normal_irradiance", []),
            "dhi_w_per_m2": hourly.get("diffuse_radiation", []),
            "temperature_c": hourly.get("temperature_2m", []),
            "relative_humidity_pct": hourly.get("relative_humidity_2m", []),
            "cloud_cover_pct": hourly.get("cloud_cover", []),
            "wind_speed_m_per_s": hourly.get("wind_speed_10m", []),
        }
    )
    df.set_index("timestamp", inplace=True)
    return df


def parse_nasa_power_records(data_list: list[dict[str, Any]]) -> pd.DataFrame:
    """Parse a list of yearly NASA POWER JSON objects into an Asia/Dhaka DataFrame."""
    frames = []
    for data in data_list:
        params = data.get("properties", {}).get("parameter", {})
        if not params:
            continue

        timestamps_str = list(params["T2M"].keys())
        # Timestamps are YYYYMMDDHH in UTC
        utc_index = pd.to_datetime(timestamps_str, format="%Y%m%d%H").tz_localize("UTC")
        dhaka_index = utc_index.tz_convert("Asia/Dhaka")

        df = pd.DataFrame(index=dhaka_index)
        mapping = {
            "ALLSKY_SFC_SW_DWN": "ghi_w_per_m2",
            "ALLSKY_SFC_SW_DNI": "dni_w_per_m2",
            "ALLSKY_SFC_SW_DIFF": "dhi_w_per_m2",
            "T2M": "temperature_c",
            "RH2M": "relative_humidity_pct",
            "CLOUD_AMT": "cloud_cover_pct",
            "WS2M": "wind_speed_m_per_s",
        }
        for nasa_key, col_name in mapping.items():
            if nasa_key in params:
                vals = [params[nasa_key].get(ts, np.nan) for ts in timestamps_str]
                arr = np.array(vals, dtype=float)
                # NASA missing value fill code is -999.0
                arr[arr <= -900.0] = np.nan
                df[col_name] = arr

        frames.append(df)

    if not frames:
        raise ValueError("No valid NASA POWER records to parse")

    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()
    combined.index.name = "timestamp"
    return combined


def clean_weather_dataset(
    nasa_df: pd.DataFrame,
    open_meteo_df: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Combine primary (NASA POWER) and secondary (Open-Meteo) weather data.

    Cleans missing values by:
    1. Using Open-Meteo values where NASA data has gaps (-999 / NaN).
    2. Enforcing physical non-negativity (e.g. irradiance >= 0, humidity in [0, 100]).
    3. Setting nighttime irradiance (hours where Open-Meteo and NASA both show 0 or night) to 0.0.
    4. Interpolating any remaining transient missing values linearly.
    """
    # Create complete hourly date range in Asia/Dhaka
    full_index = pd.date_range(
        start=f"{start_date} 00:00:00",
        end=f"{end_date} 23:00:00",
        freq="1h",
        tz="Asia/Dhaka",
        name="timestamp",
    )

    clean_df = pd.DataFrame(index=full_index)
    cols = [
        "ghi_w_per_m2",
        "dni_w_per_m2",
        "dhi_w_per_m2",
        "temperature_c",
        "relative_humidity_pct",
        "cloud_cover_pct",
        "wind_speed_m_per_s",
    ]

    for col in cols:
        nasa_series = (
            nasa_df[col].reindex(full_index) if col in nasa_df else pd.Series(np.nan, index=full_index)
        )
        om_series = (
            open_meteo_df[col].reindex(full_index)
            if col in open_meteo_df
            else pd.Series(np.nan, index=full_index)
        )

        # Impute missing NASA points with Open-Meteo
        combined_col = nasa_series.combine_first(om_series)

        # Interpolate any remaining isolated NaNs
        combined_col = combined_col.interpolate(method="time").bfill().ffill()

        # Physical bounds
        if col in ["ghi_w_per_m2", "dni_w_per_m2", "dhi_w_per_m2", "wind_speed_m_per_s"]:
            combined_col = combined_col.clip(lower=0.0)
        elif col in ["relative_humidity_pct", "cloud_cover_pct"]:
            combined_col = combined_col.clip(lower=0.0, upper=100.0)

        # Enforce zero irradiance at night (hours 19:00 to 05:00 local time in Dhaka)
        if col in ["ghi_w_per_m2", "dni_w_per_m2", "dhi_w_per_m2"]:
            is_night = (combined_col.index.hour >= 19) | (combined_col.index.hour < 5)
            combined_col.loc[is_night] = 0.0

        clean_df[col] = combined_col

    return clean_df.reset_index()


def download_and_process_weather(
    start_year: int = 2022,
    end_year: int = 2024,
    data_dir: Path | None = None,
    force: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the complete download and processing pipeline."""
    cfg = load_site_config()
    lat = cfg.site.latitude
    lon = cfg.site.longitude

    if data_dir is None:
        data_dir = Path(__file__).resolve().parents[3] / "data"

    raw_dir = data_dir / "raw"
    processed_dir = data_dir / "processed"
    sample_dir = data_dir / "sample"

    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)

    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"

    # 1. Fetch Open-Meteo
    om_raw_file = raw_dir / f"open_meteo_{start_year}_{end_year}.json"
    om_raw = fetch_open_meteo_hourly(lat, lon, start_date, end_date, om_raw_file, force=force)
    om_df = parse_open_meteo(om_raw)

    # 2. Fetch NASA POWER per year
    nasa_raw_list = []
    for y in range(start_year, end_year + 1):
        nasa_raw_file = raw_dir / f"nasa_power_{y}.json"
        nasa_raw = fetch_nasa_power_year(lat, lon, y, nasa_raw_file, force=force)
        nasa_raw_list.append(nasa_raw)
    nasa_df = parse_nasa_power_records(nasa_raw_list)

    # 3. Clean and merge
    clean_df = clean_weather_dataset(nasa_df, om_df, start_date, end_date)

    # 4. Save processed dataset
    processed_file = processed_dir / "weather.parquet"
    clean_df.to_parquet(processed_file, index=False)
    logger.info("Saved %d hours of weather data to %s", len(clean_df), processed_file)

    # 5. Save 1-week sample (168 hours) to data/sample/
    sample_mask = (clean_df["timestamp"] >= f"{end_year}-06-01 00:00:00+06:00") & (
        clean_df["timestamp"] < f"{end_year}-06-08 00:00:00+06:00"
    )
    sample_df = clean_df[sample_mask].copy()
    if len(sample_df) == 0:
        sample_df = clean_df.iloc[:168].copy()

    sample_parquet = sample_dir / "weather_sample.parquet"
    sample_csv = sample_dir / "weather_sample.csv"
    sample_df.to_parquet(sample_parquet, index=False)
    sample_df.to_csv(sample_csv, index=False)
    logger.info("Saved 1-week sample (%d rows) to %s and %s", len(sample_df), sample_parquet, sample_csv)

    return clean_df, sample_df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Download and cache Dhaka weather history.")
    parser.add_argument("--start-year", type=int, default=2022, help="Start year (default: 2022)")
    parser.add_argument("--end-year", type=int, default=2024, help="End year (default: 2024)")
    parser.add_argument("--force", action="store_true", help="Force redownload even if cached")
    args = parser.parse_args()

    download_and_process_weather(start_year=args.start_year, end_year=args.end_year, force=args.force)


if __name__ == "__main__":
    main()
