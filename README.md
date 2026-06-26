# Simple SF Energy Sharing ABM

This repository contains only the simple ABM, a compact prepared input file, and
generated reports. The large raw SF source CSV files are not included.

Run both scenarios:

```bash
python simple_abm.py
```

Run one scenario:

```bash
python simple_abm.py --generosity 0
python simple_abm.py --generosity 1
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

`A_i(t) = {health_i(t), generation_i(t), demand_i(t), storage_i(t), generosity}`

- `health_i(t)`: building health at time `t`, normalized to `[0, 1]`.
- `generation_i(t)`: solar generation for the hour.
- `demand_i(t)`: hourly load sampled from the prepared SF building energy profiles.
- `storage_i(t)`: unused surplus carried to the next hour.
- `generosity`: energy sharing parameter. This repo runs two versions: `0` and `1`.

## Environment

- Location: San Francisco.
- Grid: `36 x 36` cells, one building agent per cell.
- Data: prepared 36 x 36 agent input in `data/agents_initial.json`.
- Solar: prepared hourly SF solar profile in `data/agents_initial.json`.
- Disturbance: no solar generation on `2025-01-15`.

## Energy Sharing Rule

For each hour, agents first use their own generation and stored energy. If an
agent has a deficit, it asks its four direct grid neighbors for energy.

`gift = min(surplus, energy_request) * generosity`

Two versions are run:

- `generosity = 0`: no energy sharing.
- `generosity = 1`: donors fully share up to `min(surplus, energy_request)`.

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
- `data/agents_initial.json`: compact prepared agent and solar input.
- `outputs/report.html`: comparison report for both generosity versions.
- `outputs/comparison.csv`: scenario-level metrics.
- `outputs/generosity_0/`: outputs for no sharing.
- `outputs/generosity_1/`: outputs for full sharing.
- `outputs/generosity_*/agents_final.csv`: building-level `% building alive` and `resilience`.
- `outputs/generosity_*/daily_metrics.csv`: daily `% building alive` and `resilience`.
- `outputs/generosity_*/hourly_metrics.csv`: hourly `% building alive` and running `resilience`.
- `outputs/generosity_*/model_data.json`: simple continuation data structure.
