"""Generate the prepared agent input for the simple ABM.

Both load and solar are abstract: a base curve (an archetype) plus an hourly
Gaussian perturbation. No real-world data files are read, so the repo is fully
self-contained and reproducible from a seed. The two archetypes were abstracted
from the original SF data (their average shape over the 30 days), so they stay
realistic without depending on it.

    demand_i(t) = max(0, level + noise),  level = demand_archetype[hour] * building_scale
                  noise ~ Normal(0, DEMAND_NOISE_FRACTION * level)
                  building_scale ~ Normal(1.0, HETERO_SCALE_STD), floored at 0.3
    sun(t)      = max(0, base + noise),   base = solar_archetype[hour]
                  noise ~ Normal(0, SOLAR_NOISE_FRACTION * base)   (zero on the no-sun day)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path


GRID = 36
HOURS = 24 * 30
NO_SUN_DAY = 15
PV_AREA_M2 = 1.0
PV_GENERATION_SCALE = 5.0
BATTERY_CAPACITY_KWH = 5.0

# Mean residential daily load shape, kWh per hour (midnight .. 23:00), abstracted
# from the original SF residential data (its hour-of-day average).
DEMAND_ARCHETYPE_KWH = [
    1.049, 1.067, 1.016, 0.862, 0.828, 0.855, 0.859, 0.887,
    1.006, 1.129, 1.161, 1.147, 1.133, 1.069, 0.877, 0.875,
    0.800, 0.750, 0.715, 0.777, 0.906, 0.969, 1.058, 1.092,
]
# Mean daily solar shape, kWh per m2 per hour, abstracted from the cached NASA
# POWER irradiance for SF (its hour-of-day average): a midday bell curve.
SOLAR_ARCHETYPE_KWH_M2 = [
    0.000, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000, 0.012,
    0.124, 0.269, 0.388, 0.461, 0.473, 0.436, 0.348, 0.221,
    0.078, 0.001, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000,
]
HETERO_SCALE_STD = 0.25      # building-to-building spread of the load level
DEMAND_NOISE_FRACTION = 0.15  # hourly load noise band, fraction of the load level
SOLAR_NOISE_FRACTION = 0.10   # hourly solar noise band, fraction of the base value


def make_demand(rng: random.Random, scale: float) -> list[float]:
    demand = []
    for t in range(HOURS):
        level = DEMAND_ARCHETYPE_KWH[t % 24] * scale
        value = level + rng.gauss(0.0, DEMAND_NOISE_FRACTION * level)
        demand.append(round(max(0.0, value), 4))
    return demand


def make_sun(rng: random.Random) -> list[float]:
    sun = []
    for t in range(HOURS):
        base = SOLAR_ARCHETYPE_KWH_M2[t % 24]
        if (t // 24) + 1 == NO_SUN_DAY or base <= 0.0:
            sun.append(0.0)
        else:
            sun.append(round(max(0.0, base + rng.gauss(0.0, SOLAR_NOISE_FRACTION * base)), 6))
    return sun


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "data" / "agents_initial.json")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    sun = make_sun(rng)
    agents = []
    for row in range(GRID):
        for col in range(GRID):
            scale = max(0.3, rng.gauss(1.0, HETERO_SCALE_STD))
            agents.append(
                {
                    "id": len(agents),
                    "row": row,
                    "col": col,
                    "building_type": "residential",
                    "norm": 0.0,
                    "profile_id": f"res-{scale:.2f}",
                    "load_scale": round(scale, 4),
                    "demand": make_demand(rng, scale),
                }
            )

    payload = {
        "grid": GRID,
        "hours": HOURS,
        "no_sun_day": NO_SUN_DAY,
        "building_type_filter": "residential",
        "load_profile_source": "synthetic residential archetype + Gaussian noise (prepare_agents.py)",
        "solar_source": "synthetic solar archetype + Gaussian noise (prepare_agents.py)",
        "load_archetype_kwh": DEMAND_ARCHETYPE_KWH,
        "solar_archetype_kwh_m2": SOLAR_ARCHETYPE_KWH_M2,
        "hetero_scale_std": HETERO_SCALE_STD,
        "demand_noise_fraction": DEMAND_NOISE_FRACTION,
        "solar_noise_fraction": SOLAR_NOISE_FRACTION,
        "pv_area_m2": PV_AREA_M2,
        "pv_generation_scale": PV_GENERATION_SCALE,
        "battery_capacity_kwh": BATTERY_CAPACITY_KWH,
        "seed": args.seed,
        "sun": sun,
        "agents": agents,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    summary_path = args.out.with_name("agents_initial_summary.csv")
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id", "row", "col", "building_type", "norm", "profile_id",
                "load_scale", "pv_area_m2", "pv_generation_scale", "battery_capacity_kwh",
                "load_profile_hours", "demand_min", "demand_mean", "demand_max",
            ],
        )
        writer.writeheader()
        for agent in agents:
            demand = agent["demand"]
            writer.writerow(
                {
                    "id": agent["id"],
                    "row": agent["row"],
                    "col": agent["col"],
                    "building_type": agent["building_type"],
                    "norm": agent["norm"],
                    "profile_id": agent["profile_id"],
                    "load_scale": agent["load_scale"],
                    "pv_area_m2": PV_AREA_M2,
                    "pv_generation_scale": PV_GENERATION_SCALE,
                    "battery_capacity_kwh": BATTERY_CAPACITY_KWH,
                    "load_profile_hours": len(demand),
                    "demand_min": round(min(demand), 6),
                    "demand_mean": round(sum(demand) / len(demand), 6),
                    "demand_max": round(max(demand), 6),
                }
            )
    print(args.out)


if __name__ == "__main__":
    main()
