# Simulated site and asset spec

The MVP plans one simulated mid-size garment (RMG) factory in the Ashulia/Savar industrial belt near Dhaka.
**All values are assumptions for simulation, not measurements.** They are chosen to be plausible and
internally consistent, and are stated as assumptions in the UI, the pitch and the Data & AI provenance section.
Source of truth: [`config/site.yaml`](../config/site.yaml), validated by `src/energy_brain/config.py`.

## Why a garment factory

RMG is Bangladesh's largest export industry. Factories run daytime shifts that line up with solar output,
face grid load-shedding, keep diesel generators as backup, and increasingly get carbon questions from export
buyers. That makes them the PRD's first customer segment and the clearest story for judges.

## Assumed values

| Asset | Value | Reasoning |
| --- | --- | --- |
| Location | approx. 23.90 N, 90.32 E, Asia/Dhaka | Ashulia/Savar industrial area, rounded; only used for weather and sun position |
| Planning | 15-min slots, 24 h horizon (96 slots) | PRD resolution |
| Grid | max import 1500 kW, no export | Headroom above peak; no net metering keeps the MVP conservative |
| Load | peak 1200 kW, base 250 kW, Friday off, shift 08:00–17:00 + overtime to 20:00 | Mid-size factory profile; Friday is the weekly holiday |
| Solar | 600 kWp, 23° tilt, south, 14% losses | Large rooftop; tilt ≈ latitude; 14% is the PVWatts default loss |
| Battery | 1000 kWh, 500 kW charge/discharge, 90% round trip, SoC 10–95%, start 50% | About 1 h of evening load; 0.5C; typical Li-ion efficiency |
| HVAC | 250 kW rated, min 100 kW when occupied, comfort 24–28 °C, pre-cool up to 2 h | Cooling share of peak load; the flexible load the planner can shift |
| EV fleet | 6 vans × 40 kWh, 11 kW chargers, park 18:00–07:00, arrive 30%, leave ≥ 90% | Overnight charging is flexible but the departure charge is a hard constraint |
| Generator | 1000 kVA / 800 kW, min load 30%, 0.27 L/kWh | Backup for load-shedding; fuel figure refined with the sourced diesel data in #8 |

## Built-in consistency checks

`load_site_config()` rejects the file if, for example:

- battery SoC limits are out of order, or the initial SoC is outside them;
- HVAC minimum exceeds its rating, or the comfort band is inverted;
- the EV chargers cannot deliver the required energy within the parking window;
- generator kW exceeds its kVA;
- peak load cannot be met by grid + generator + battery, or by **generator + battery alone during an outage**;
- a key is misspelt or unknown.

Derived values used by the agents: 96 slots, 13 h EV parking window, 24 kWh needed per van, 240 kW
generator minimum output.

## Replacing assumptions with real data

If a factory or installer shares real numbers (or a meter export), update `config/site.yaml`, mark the line as
measured in its comment, update this table, and run `pytest`.
