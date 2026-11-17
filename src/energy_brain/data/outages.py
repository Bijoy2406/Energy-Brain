"""Generate synthetic load-shedding (outage) calendar for the site (Issue #9).

Models grid outages (load-shedding) in the Ashulia/Savar industrial belt near Dhaka:
- Outages model realistic rotational load-shedding common in industrial Bangladesh:
  1. Evening peak stress window (18:00 - 22:00) when nationwide domestic demand surges.
  2. Summer afternoon heatwave deficit (13:00 - 16:00) during hot months (April - September).
  3. Occasional daytime grid feeder maintenance (10:00 - 12:00).
- Configurable frequency and duration: seasonal variation (higher in summer, lower in winter).
- Slot-aligned to 15-minute intervals in Asia/Dhaka.

Outputs:
- data/processed/outages.parquet (start, end, duration_hours, reason)
- data/sample/outages_sample.parquet & outages_sample.csv (1-week test sample)
"""

from __future__ import annotations

import argparse
import datetime
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from energy_brain.data.synthetic_load import is_bangladesh_holiday

logger = logging.getLogger(__name__)

DHAKA_TZ = ZoneInfo("Asia/Dhaka")


def generate_outage_events(
    start_date: str = "2022-01-01",
    end_date: str = "2024-12-31",
    random_seed: int = 42,
) -> pd.DataFrame:
    """Generate a realistic synthetic outage calendar for Dhaka industrial area.

    Parameters:
    - Summer/Monsoon (Apr - Sep): 3 to 4 days/week have outages, 1 to 2 hours per event.
    - Winter/Dry (Oct - Mar): 1 to 2 days/week have outages, 1 hour per event.
    - Fridays & public holidays have significantly reduced outage probability.
    """
    rng = np.random.default_rng(random_seed)
    days = pd.date_range(start=start_date, end=end_date, freq="D", tz="Asia/Dhaka")

    records = []

    for day in days:
        date_obj = day.date()
        is_friday = day.day_name() == "Friday"
        is_holiday = is_bangladesh_holiday(date_obj)
        month = day.month
        is_summer = 4 <= month <= 9

        # Base outage probability for the day
        if is_friday or is_holiday:
            p_outage = 0.10 if is_summer else 0.05
        else:
            p_outage = 0.60 if is_summer else 0.25

        if rng.random() > p_outage:
            continue

        # Decide scenario for this outage day
        # Scenario A: Evening peak load-shedding (18:00 to 22:00 window)
        # Scenario B: Afternoon summer peak deficit (13:00 to 16:00 window)
        # Scenario C: Morning feeder maintenance (10:00 to 12:00 window)
        scenarios = ["evening", "afternoon"] if is_summer else ["evening", "morning"]
        chosen_scenario = rng.choice(scenarios, p=[0.7, 0.3])

        if chosen_scenario == "evening":
            # Start between 18:00 and 20:30, slot-aligned to 15 min
            start_hour = rng.choice([18, 19, 20])
            start_minute = rng.choice([0, 15, 30, 45])
            # Duration: 1.0 to 2.0 hours (4 to 8 slots)
            duration_slots = rng.choice([4, 5, 6, 8])  # 60, 75, 90, 120 mins
            reason = "evening_peak_load_shedding"
        elif chosen_scenario == "afternoon":
            start_hour = rng.choice([13, 14])
            start_minute = rng.choice([0, 15, 30])
            duration_slots = rng.choice([4, 5, 6])  # 60, 75, 90 mins
            reason = "summer_afternoon_deficit"
        else:
            start_hour = 10
            start_minute = rng.choice([0, 30])
            duration_slots = rng.choice([4, 6])  # 60 or 90 mins
            reason = "grid_feeder_maintenance"

        outage_start = day + pd.Timedelta(hours=start_hour, minutes=start_minute)
        duration_minutes = duration_slots * 15
        outage_end = outage_start + pd.Timedelta(minutes=duration_minutes)

        records.append(
            {
                "start": outage_start,
                "end": outage_end,
                "duration_hours": round(duration_minutes / 60.0, 2),
                "reason": reason,
            }
        )

        # On very severe summer days (15% chance), a second evening outage occurs
        if is_summer and chosen_scenario == "afternoon" and rng.random() < 0.25:
            ev_start = day + pd.Timedelta(hours=19, minutes=30)
            ev_end = ev_start + pd.Timedelta(hours=1, minutes=15)
            records.append(
                {
                    "start": ev_start,
                    "end": ev_end,
                    "duration_hours": 1.25,
                    "reason": "evening_peak_load_shedding",
                }
            )

    df = pd.DataFrame(records).sort_values("start").reset_index(drop=True)
    return df


def get_daily_outage_mask(
    day_start: datetime.datetime,
    outages_df: pd.DataFrame,
    n_slots: int = 96,
    slot_minutes: int = 15,
) -> list[bool]:
    """Return a list of n_slots booleans for a given day start.

    True indicates the grid is unavailable during that slot.
    Partial-slot outages round up to the whole slot (as per interfaces.md).
    """
    if day_start.tzinfo is None:
        raise ValueError("day_start must be timezone-aware (Asia/Dhaka)")

    local_start = day_start.astimezone(DHAKA_TZ)
    local_start = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    slot_delta = pd.Timedelta(minutes=slot_minutes)

    slot_times = [local_start + i * slot_delta for i in range(n_slots)]
    mask = [False] * n_slots

    # Filter outages overlapping this day
    day_end = local_start + pd.Timedelta(days=1)
    relevant = outages_df[(outages_df["end"] > local_start) & (outages_df["start"] < day_end)]

    for _, row in relevant.iterrows():
        o_start = row["start"]
        o_end = row["end"]
        for i, s_time in enumerate(slot_times):
            s_end = s_time + slot_delta
            # Overlap condition: slot interval overlaps with outage interval
            if max(s_time, o_start) < min(s_end, o_end):
                mask[i] = True

    return mask


def build_outage_calendar_pipeline(
    start_date: str = "2022-01-01",
    end_date: str = "2024-12-31",
    output_path: Path | None = None,
    sample_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate and save the synthetic outage calendar dataset."""
    root_dir = Path(__file__).resolve().parents[3]

    if output_path is None:
        output_path = root_dir / "data" / "processed" / "outages.parquet"
    if sample_path is None:
        sample_path = root_dir / "data" / "sample" / "outages_sample.parquet"

    logger.info("Generating synthetic outage calendar (%s to %s)...", start_date, end_date)
    outages_df = generate_outage_events(start_date=start_date, end_date=end_date)

    # Save full processed dataset
    output_path.parent.mkdir(parents=True, exist_ok=True)
    outages_df.to_parquet(output_path, index=False)
    logger.info("Saved %d outage events to %s", len(outages_df), output_path)

    # Save 1-week test sample (June 1 - June 8, 2024)
    sample_dir = sample_path.parent
    sample_dir.mkdir(parents=True, exist_ok=True)

    sample_mask = (outages_df["start"] >= "2024-06-01 00:00:00+06:00") & (
        outages_df["start"] < "2024-06-08 00:00:00+06:00"
    )
    sample_df = outages_df[sample_mask].copy()

    sample_df.to_parquet(sample_path, index=False)
    csv_sample_path = sample_dir / "outages_sample.csv"
    sample_df.to_csv(csv_sample_path, index=False)
    logger.info("Saved 1-week sample (%d events) to %s and %s", len(sample_df), sample_path, csv_sample_path)

    total_hours = outages_df["duration_hours"].sum()
    logger.info(
        "Outage Statistics: Total events = %d, Total outage hours = %.1f h",
        len(outages_df),
        total_hours,
    )
    return outages_df, sample_df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Generate synthetic outage calendar.")
    parser.parse_args()
    build_outage_calendar_pipeline()


if __name__ == "__main__":
    main()
