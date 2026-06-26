# Simple SF Energy Sharing ABM

This repository contains only the simple ABM, a compact prepared input file, and
generated reports. The large raw SF source CSV files are not included.

This version uses **residential buildings only**. Commercial load profiles are
not used.

Run both scenarios:

```bash
python simple_abm.py
```

Run one scenario:

```bash
python simple_abm.py --norm 0
python simple_abm.py --norm 1
```

## Simulation Time

The simulation covers exactly 30 days:

- Start: `2025-01-01 00:00`
- End: `2025-01-30 23:00`
- Time step: hourly
- Total time steps: `720`
- No-sun disturbance day: `2025-01-15`

## Agent Definition

Each grid cell is one building agent:

`A_i(t) = {building_type, norm, generation_i(t), demand_i(t), storage_i(t), health_i(t)}`

- `building_type`: fixed to `residential` in this version.
- `norm`: energy sharing norm. The two scenarios set all residential buildings to `0` or `1`.
- `health_i(t)`: building health at time `t`, normalized to `[0, 1]`.
- `generation_i(t)`: solar generation for the hour.
- `demand_i(t)`: hourly load sampled from the prepared SF building energy profiles.
- `storage_i(t)`: unused surplus carried to the next hour.

## Environment

- Location: San Francisco.
- Grid: `36 x 36` cells, one building agent per cell.
- Building type filter: `residential` only.
- Data: prepared 36 x 36 residential agent input in `data/agents_initial.json`.
- Solar: prepared hourly SF solar profile in `data/agents_initial.json`.
- Disturbance: no solar generation on `2025-01-15`.

## Data Sources And Assumptions

### Load Profile

The load profile comes from the local SF energy profile data:

- Raw profile file: `D:\CSSS2026\cities in petri dish\data\energy_profiles_clean\energy_profiles_hourly_used.csv`
- Raw building metadata file: `D:\CSSS2026\cities in petri dish\data\energy_profiles_clean\building_energy_metadata.csv`

The raw profile file contains hourly load profiles with columns like:

`profile_id, profile_type, t0001, t0002, ..., t8761`

The raw building metadata file assigns buildings to profiles using `profile_id`.
For this simple ABM, only rows with:

`profile_type = residential`

are used. Commercial rows are ignored. The prepared residential-only data is
stored in:

`data/agents_initial.json`

Each agent stores a 720-hour residential demand vector:

`demand_i(t)`

### Solar Generation Potential

Solar generation potential comes from the local cached NASA POWER file:

`D:\CSSS2026\cities in petri dish\data\sf_terrain_energy_growth_cache\nasa_power_sf_2025_hourly.json`

The source variable is:

`ALLSKY_SFC_SW_DWN`

This is hourly all-sky surface shortwave downward irradiance for San Francisco.
The model uses the first 720 hourly values, converts each value from `W/m2` to
`kWh/m2` by dividing by `1000`, then sets `2025-01-15` to zero sun.

The simple generation rule is:

`generation_i(t) = solar_kwh_per_m2(t) * pv_area_m2`

The PV size is fixed and uniform across all grid cells:

`pv_area_m2 = 1.0`

Because every grid cell has the same assumed PV area, solar generation potential
is spatially uniform in this minimal version.

### Battery / Storage Size

Battery size is fixed and uniform across all residential buildings:

`battery_capacity_kwh = 5.0`

`storage_i(t)` starts at `0` and is updated from unused surplus energy:

`storage_i(t+1) = unused surplus after self-use and sharing`

Storage is capped:

`storage_i(t) <= 5.0 kWh`

So all buildings use the same modular battery size and the same modular PV size.

## Energy Sharing Rule

For each hour, agents first use their own generation and stored energy. If an
agent has a deficit, it asks its four direct grid neighbors for energy.

`gift = min(surplus, energy_request) * norm_donor`

Two versions are run:

- `norm = 0`: no energy sharing.
- `norm = 1`: donors fully share up to `min(surplus, energy_request)`.

## Healthy / Alive Rule

Each building has its own health value:

`health_i(t) = clip((generation + starting_storage + energy_received - energy_exported) / demand, 0, 1)`

A building is dead only when:

`health_i(t) = 0`

A building is alive when:

`health_i(t) > 0`

## Building Performance Metrics

Only two metrics are reported.

1. `% building alive`

The percent of buildings with `health_i(t) > 0` over the evaluated time window.

2. `resilience`

Resilience is normalized to `[0, 1]` as the area under the system performance curve:

`R = integral Q(t) dt / integral Q0 dt`

where:

- `Q(t) = mean_i health_i(t)`
- `Q0 = 1`

So resilience is the average system health over time.

## Files

- `simple_abm.py`: model runner.
- `prepare_agents.py`: helper used to prepare residential-only `agents_initial.json` from the large local SF data.
- `data/agents_initial.json`: compact prepared agent and solar input.
- `outputs/report.html`: comparison report for both norm versions.
- `outputs/animation.html`: animated 36 x 36 grid of building health over time.
- `outputs/comparison.csv`: scenario-level metrics.
- `outputs/norm_0/`: outputs for no sharing.
- `outputs/norm_1/`: outputs for full sharing.
- `outputs/norm_*/agents_final.csv`: building-level `% building alive` and `resilience`.
- `outputs/norm_*/daily_metrics.csv`: daily `% building alive` and `resilience`.
- `outputs/norm_*/hourly_metrics.csv`: hourly `% building alive` and running `resilience`.
- `outputs/norm_*/model_data.json`: simple continuation data structure.
