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

## Parameters You Can Tune

| Parameter | Default | What it does | Set in |
| --- | --- | --- | --- |
| `pv_generation_scale` | `5.0` | PV scaling factor: multiplies every building's solar generation. Higher = more energy. | `prepare_agents.py` |
| `battery_capacity_kwh` | `5.0` | Battery scaling: max energy each building can store. | `prepare_agents.py` |
| `pv_area_m2` | `1.0` | PV panel area per building (same for all). | `prepare_agents.py` |
| `norm` | `0` and `1` | Sharing switch: `0` = share nothing, `1` = share all spare energy. | `--norm` flag |
| `grid` | `36` | Grid is `grid x grid` cells, one building each. | `prepare_agents.py` |
| `dead_hours_until_permanent` | `24` | Consecutive dead hours before a building is permanently dead. | `simple_abm.py` |
| `no_sun_day` | `15` | Day with zero solar (the disturbance). | `prepare_agents.py` |

`pv_generation_scale`, `battery_capacity_kwh`, and `pv_area_m2` are written into
`data/agents_initial.json`; rerun `prepare_agents.py` after changing them.

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

The load profile comes from the local SF energy profile data. The absolute
paths below are on the author's machine; the prepared result is already
committed in `data/`, so you do not need these files to run the model.

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

The PV size is fixed and uniform across all grid cells:

`pv_area_m2 = 1.0`

The model also uses one global PV generation scaling factor:

`pv_generation_scale = 5.0`

The final generation rule is:

`generation_i(t) = solar_kwh_per_m2(t) * pv_area_m2 * pv_generation_scale`

Every grid cell uses the same `pv_area_m2` and the same `pv_generation_scale`,
so solar generation is spatially uniform. `pv_generation_scale = 5.0` is an
arbitrary multiplier that puts generation on a comparable scale to demand.

### Battery / Storage Size

Battery size is fixed and uniform across all residential buildings:

`battery_capacity_kwh = 5.0`

`storage_i(t)` starts at `0` and is updated from unused surplus energy:

`storage_i(t+1) = unused surplus after self-use and sharing`

Storage is capped:

`storage_i(t) <= 5.0 kWh`

## Energy Sharing Rule

Each hour has two steps:

1. **Self-use.** Every building uses its own generation plus stored energy to
   meet its demand. Whatever is left over is its `surplus`; whatever is missing
   means it is `short`.
2. **Share (one simultaneous step).** Every building with a surplus splits
   `norm * surplus` equally among the neighbors that are short this hour. All
   buildings do this at once, so there is no ordering and no asking one neighbor
   at a time.

`gift to each short neighbor = norm * surplus / (number of short neighbors)`

Two versions are run:

- `norm = 0`: a building shares nothing.
- `norm = 1`: a building shares all of its spare energy.

## Healthy / Alive Rule

Each building has its own health value:

`health_i(t) = clip((generation + starting_storage + energy_received - energy_exported) / demand, 0, 1)`

Building states:

- `alive`: `health_i(t) > 0`
- `dead` (this hour): `health_i(t) = 0`

### Permanent Death

If a building stays dead for **24 consecutive hours** it becomes **permanently
dead**. From then on its health is fixed at `0` for the rest of the simulation:
it never revives, and it no longer generates, stores, or shares energy. The
consecutive-dead-hour counter resets to `0` on any hour the building is alive.

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

## Does Sharing Help? Parameter Sweep

With this simple sharing rule, sharing (`norm = 1`) **never** beats no-sharing
(`norm = 0`) across the tested `pv_generation_scale` x `battery_capacity_kwh`
grid: it is worse at every setting. Generation is spatially uniform, so sharing
cannot add energy to the system; and because a donor splits *all* its spare
energy among its short neighbors (often more than they need), it wastes the
excess and drains its own battery before the no-sun stress, so more buildings
die permanently. The gap shrinks as energy gets more abundant but stays negative.

A less wasteful rule (cap each gift at the neighbor's actual shortfall) can let
sharing win in the abundant regime, but that is more complex; this version keeps
the rule as simple as possible.

See `outputs/sweep_collage.png`, `outputs/sweep_delta_heatmap.png`, and
`outputs/sweep_results.csv`.

## Files

- `simple_abm.py`: the core model only (agents, the hourly loop, sharing, health, metrics). Short on purpose.
- `reports.py`: all output writers (CSV metrics and the HTML report / animation / hover-grid pages). Imported by `simple_abm.py`.
- `prepare_agents.py`: helper used to prepare residential-only `agents_initial.json` from the large local SF data.
- `data/agents_initial.json`: compact prepared agent and solar input. Each agent includes `id`, `row`, `col`, `building_type`, initial `norm`, `profile_id`, and the full 720-hour `demand` load profile.
- `data/agents_initial_summary.csv`: per-agent initial summary with `profile_id`, PV size, PV scale, battery size, and demand min/mean/max.
- `outputs/report.html`: comparison report for both norm versions.
- `outputs/animation.html`: building health over time, with `norm = 0` (no sharing) and `norm = 1` (full sharing) shown side by side. A single time slider (plus play/pause/step) drives both 36 x 36 grids in sync. Each cell is colored by a 5-band health heatmap (deep red 0-20% to green 80-100%); a cell is black once permanently dead. Black lines mark donor-recipient pairs that have shared at any earlier hour; white lines show sharing in the current hour.
- `outputs/agent_grid.html`: hoverable 36 x 36 grid; each building shows generation, demand, and storage curves.
- `outputs/comparison.csv`: scenario-level metrics.
- `outputs/sweep_results.csv`: resilience and alive % for both norms across a `pv_generation_scale` x `battery_capacity_kwh` grid, with the share-minus-no-share delta.
- `outputs/sweep_collage.png`: per-building resilience maps, left = no sharing, right = sharing, one row per `pv_generation_scale` (battery fixed at the most share-favorable value).
- `outputs/sweep_delta_heatmap.png`: `resilience(share) - resilience(no-share)` across the full grid; blue = sharing wins, red = sharing loses.
- `outputs/norm_0/`: outputs for no sharing.
- `outputs/norm_1/`: outputs for full sharing.
- `outputs/norm_*/agents_final.csv`: per-building `alive_percent` and `resilience`.
- `outputs/norm_*/daily_metrics.csv`: daily `alive_percent` and `resilience`.
- `outputs/norm_*/hourly_metrics.csv`: hourly `alive_percent` and running `resilience`.
- `outputs/norm_*/model_data.json`: simple continuation data structure.
