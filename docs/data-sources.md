# Data Sources & AI Provenance

This document records the exact data sources, variables, units, coverage dates, access terms, and preprocessing logic used across Energy Brain.

All data used in the MVP is free, public, and reproducible offline from cached files.

---

## 1. Historical Weather Data (Issue #5)

### Sources
1. **NASA POWER (Prediction Of Worldwide Energy Resources)**
   - **Provider:** NASA Langley Research Center
   - **Model / Assimilation:** MERRA-2 (Modern-Era Retrospective analysis for Research and Applications, Version 2) and GEOS-FP
   - **Endpoint:** `https://power.larc.nasa.gov/api/temporal/hourly/point`
   - **Community:** Renewable Energy (`RE`)
   - **License / Terms:** Open and freely available worldwide for public, academic, and commercial use. NASA data policy mandates open sharing.
   - **Coordinates:** Lat `23.90°N`, Lon `90.32°E` (Ashulia/Savar, Dhaka district, Bangladesh from `config/site.yaml`)
   - **Date Range:** 2022-01-01 00:00:00 to 2024-12-31 23:00:00 (3 full calendar years, 26,304 hours)

2. **Open-Meteo Historical Weather API (Backup & Imputation)**
   - **Provider:** Open-Meteo GmbH
   - **Endpoint:** `https://archive-api.open-meteo.com/v1/archive`
   - **License / Terms:** Free for non-commercial and open research under Attribution 4.0 International (CC BY 4.0).
   - **Date Range:** Same 3-year range as NASA POWER (2022-01-01 to 2024-12-31).

### Variables & Units

| Variable Name in `weather.parquet` | Description | Unit | Primary Source Field | Backup Source Field |
| :--- | :--- | :---: | :--- | :--- |
| `timestamp` | Slot start timestamp in Asia/Dhaka | ISO 8601 | UTC converted to Asia/Dhaka | `time` (Asia/Dhaka) |
| `ghi_w_per_m2` | Global Horizontal Irradiance | $\text{W/m}^2$ | `ALLSKY_SFC_SW_DWN` | `shortwave_radiation` |
| `dni_w_per_m2` | Direct Normal Irradiance | $\text{W/m}^2$ | `ALLSKY_SFC_SW_DNI` | `direct_normal_irradiance` |
| `dhi_w_per_m2` | Diffuse Horizontal Irradiance | $\text{W/m}^2$ | `ALLSKY_SFC_SW_DIFF` | `diffuse_radiation` |
| `temperature_c` | Ambient Air Temperature at 2m | $^\circ\text{C}$ | `T2M` | `temperature_2m` |
| `relative_humidity_pct` | Relative Humidity at 2m | $\%$ | `RH2M` | `relative_humidity_2m` |
| `cloud_cover_pct` | Total Cloud Cover | $\%$ | `CLOUD_AMT` | `cloud_cover` |
| `wind_speed_m_per_s` | Wind Speed | $\text{m/s}$ | `WS2M` (at 2m) | `wind_speed_10m` |

### Preprocessing & Missing Value Policy
1. **Time Alignment:** All records are anchored to the beginning of each hour in `Asia/Dhaka` (`UTC+6`).
2. **Missing Fill Values:** NASA POWER denotes missing or unavailable observations with `-999.0`. Any reading $\le -900$ is flagged as `NaN`.
3. **Cross-Source Imputation:** Missing values in NASA POWER are imputed using values from Open-Meteo for the matching hour via `combine_first()`.
4. **Interpolation:** Any remaining transient gaps are linearly interpolated across the time index (`interpolate(method='time')`).
5. **Physical Bounds:**
   - Irradiances (`ghi`, `dni`, `dhi`) and wind speed are clamped to $\ge 0.0$.
   - Humidity and cloud cover are clamped to $[0.0, 100.0]$.
   - Nighttime irradiance: Hours between 19:00 and 05:00 local time in Dhaka are explicitly clamped to `0.0` to eliminate sensor drift or reanalysis noise.

### File Locations
- **Raw API Responses:**
  - `data/raw/open_meteo_2022_2024.json`
  - `data/raw/nasa_power_2022.json`
  - `data/raw/nasa_power_2023.json`
  - `data/raw/nasa_power_2024.json`
- **Cleaned Dataset:** `data/processed/weather.parquet` (26,304 rows, 3 full years)
- **Committed Sample:** `data/sample/weather_sample.parquet` and `data/sample/weather_sample.csv` (1 week / 168 rows)

### Re-creating the Weather Data
To re-run the download and cleaning pipeline from scratch:
```powershell
python -m energy_brain.data.download_weather
```
Or to force re-download without using cached raw files:
```powershell
python -m energy_brain.data.download_weather --force
```

---

## 2. Solar PV Output Simulation (Issue #6)

### Method & Engineering Specification
Because the site has no real meter export, 15-minute AC solar power generation is simulated from the cleaned weather history using **pvlib-python (v0.16+)** based on the parameters in `config/site.yaml`.

- **Solar System Rating:** 600 kWp rooftop array
- **Orientation & Tilt:** Tilt $23^\circ$ (equal to site latitude), Azimuth $180^\circ$ (true South)
- **System Losses:** 14.0% balance-of-system losses (soiling, shading, wiring, mismatch, default PVWatts assumption)
- **Nominal Inverter Efficiency:** 96.0% (PVWatts inverter model)
- **Cell Temperature Model:** Faiman thermal model using ambient temperature and wind speed
- **DC Power Model:** PVWatts DC model with temperature coefficient $\gamma = -0.4\% / ^\circ\text{C}$ relative to $25^\circ\text{C}$
- **Transposition Model:** Hay-Davies model for diffuse and direct plane-of-array (POA) irradiance calculation

### Time Resolution & Resampling
1. Hourly weather points (`GHI`, `DNI`, `DHI`, `temperature_2m`, `wind_speed`) are resampled to 15-minute intervals (`:00`, `:15`, `:30`, `:45`) using time-based linear interpolation.
2. Complete 96-slot planning days are maintained across all 3 years (105,216 total 15-minute slots).
3. Exact solar position (zenith, azimuth, elevation) is computed per 15-minute slot using `pvlib.location.Location.get_solarposition()`.
4. When apparent solar elevation $\le 0^\circ$ (sun below horizon), plane-of-array irradiance and AC power are strictly zeroed.

### Validation & Benchmark Sanity Checks
- **Peak AC Power:** Reaches $569.05\text{ kW}$ (94.8% of 600 kWp nameplate, reflecting cell heating, 14% system losses, and inverter derating).
- **Annual Energy Output:**
  - 2022: $745.7\text{ MWh}$ ($1,242.8\text{ kWh/kWp/year}$)
  - 2023: $714.3\text{ MWh}$ ($1,190.5\text{ kWh/kWp/year}$)
  - 2024: $691.4\text{ MWh}$ ($1,152.3\text{ kWh/kWp/year}$)
- **Bangladesh Empirical Benchmark:** Typical rooftop solar installations in Dhaka yield between $1,100$ and $1,300\text{ kWh/kWp/year}$. The simulated series aligns with empirical regional data.
- **Verification Notebook:** `notebooks/pv_check.ipynb` provides interactive visualization of clear days, monsoon intermittency, and yield statistics.

### File Locations
- **Processed Series:** `data/processed/pv.parquet` (105,216 slots, columns: `timestamp`, `pv_kw`)
- **Committed Sample:** `data/sample/pv_sample.parquet` and `data/sample/pv_sample.csv` (672 slots / 1 week)

### Re-creating the PV Simulation
```powershell
python -m energy_brain.data.simulate_pv
```

---

## 3. Site Load Profile (Issue #7)
*(Documented during Issue #7 implementation with factory shift schedule and temperature-dependent HVAC modeling.)*

---

## 4. Tariffs, Emission Factors & Fuel Costs (Issue #8)
*(Documented during Issue #8 implementation with BERC schedules and national grid carbon factors.)*

---

## 5. Outage Scenarios (Issue #9)
*(Documented during Issue #9 implementation with synthetic load-shedding generator.)*
