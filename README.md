# Simple SF Energy Sharing ABM

This is a standalone copy of the simple ABM. It contains only the model script,
a compact prepared input file, and generated reports. The large raw SF source
CSVs are not included.

Run:

```bash
python simple_abm.py
```

## Agent Definition
Each grid cell is one building agent:

`A_i(t) = {health_i(t), generation_i(t), demand_i(t), storage_i(t), norm_i}`

- `health_i(t)`: fraction of hourly demand served after self-generation, storage, and neighbor sharing.
- `generation_i(t)`: solar generation for the hour.
- `demand_i(t)`: hourly load sampled from the SF building energy profiles.
- `storage_i(t)`: unused surplus carried to the next hour.
- `norm_i`: generosity parameter sampled once from `Uniform(0, 1)`.

## Environment
- Location: San Francisco bounding box `{'west': -122.515, 'east': -122.355, 'south': 37.705, 'north': 37.812}`.
- Grid: `36 x 36` cells, one agent per cell.
- Data: prepared 36 x 36 agent input in `data/agents_initial.json`.
- Solar: prepared hourly SF solar profile in `data/agents_initial.json`.
- Simulation time: `30` days, `720` hourly steps.
- Stress event: day `15` has no solar generation.

## Energy Sharing Rule
For each hour, agents first use their own generation and stored energy. If an agent has a deficit, it asks its four grid neighbors for energy. A donor gives:

`gift = min(surplus, energy_request) * norm_donor`

This keeps `norm_i` as the only behavioral parameter.

## Building Performance Metrics
The report writes one row per building to `outputs/agents_final.csv`:

- `service_ratio`: month-level served demand fraction.
- `final_health`: final hourly served demand fraction.
- `total_demand`: total monthly energy demand.
- `total_generated`: total monthly solar generation.
- `total_self_served`: demand served before neighbor sharing.
- `total_imported`: energy received from neighbors.
- `total_exported`: energy given to neighbors.
- `total_unmet`: energy demand not served.
- `final_storage`: leftover stored energy at the end.

## Files
- `simple_abm.py`: model runner.
- `data/agents_initial.json`: compact prepared agent and solar input.
- `outputs/report.html`: readable summary report.
- `outputs/agents_final.csv`: simple agent table for continuing work.
- `outputs/daily_metrics.csv`: one row per simulated day.
- `outputs/model_data.json`: simple nested data structure with summary, schema, agents, and daily metrics.
