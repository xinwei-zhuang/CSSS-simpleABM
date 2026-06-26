from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path


GRID = 36
HOURS = 24 * 30
NO_SUN_DAY = 15
SF_BBOX = {"west": -122.515, "east": -122.355, "south": 37.705, "north": 37.812}


def cell(lon: float, lat: float) -> tuple[int, int]:
    col = int((lon - SF_BBOX["west"]) / (SF_BBOX["east"] - SF_BBOX["west"]) * GRID)
    row = int((SF_BBOX["north"] - lat) / (SF_BBOX["north"] - SF_BBOX["south"]) * GRID)
    return max(0, min(GRID - 1, row)), max(0, min(GRID - 1, col))


def load_residential_profiles(path: Path) -> dict[str, list[float]]:
    profiles = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = [c for c in reader.fieldnames or [] if c.startswith("t")][:HOURS]
        for row in reader:
            if row["profile_type"].lower() != "residential":
                continue
            profiles[row["profile_id"]] = [max(0.0, float(row[c] or 0.0)) for c in cols]
    return profiles


def load_residential_bins(metadata_path: Path, profiles: dict[str, list[float]]) -> tuple[list[list[list[str]]], list[str], int]:
    bins: list[list[list[str]]] = [[[] for _ in range(GRID)] for _ in range(GRID)]
    all_ids = []
    residential_rows = 0
    with metadata_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["profile_type"].lower() != "residential":
                continue
            residential_rows += 1
            lon, lat, pid = float(row["centroid_lon"]), float(row["centroid_lat"]), row["profile_id"]
            if pid in profiles and SF_BBOX["west"] <= lon <= SF_BBOX["east"] and SF_BBOX["south"] <= lat <= SF_BBOX["north"]:
                r, c = cell(lon, lat)
                bins[r][c].append(pid)
                all_ids.append(pid)
    return bins, all_ids, residential_rows


def load_sun(path: Path) -> list[float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    solar = data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
    values = [solar[k] for k in sorted(solar)[:HOURS]]
    hi = max(values) or 1.0
    return [0.0 if i // 24 == NO_SUN_DAY - 1 else values[i] / hi for i in range(HOURS)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-data-dir", type=Path, default=Path(r"D:\CSSS2026\cities in petri dish\data"))
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "data" / "agents_initial.json")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    energy_dir = args.raw_data_dir / "energy_profiles_clean"
    profiles_path = energy_dir / "energy_profiles_hourly_used.csv"
    metadata_path = energy_dir / "building_energy_metadata.csv"
    solar_path = args.raw_data_dir / "sf_terrain_energy_growth_cache" / "nasa_power_sf_2025_hourly.json"

    rng = random.Random(args.seed)
    profiles = load_residential_profiles(profiles_path)
    bins, all_ids, residential_rows = load_residential_bins(metadata_path, profiles)
    if not profiles or not all_ids:
        raise RuntimeError("No residential profiles/buildings found in the raw SF data.")

    agents = []
    for row in range(GRID):
        for col in range(GRID):
            profile_id = rng.choice(bins[row][col] or all_ids)
            agents.append(
                {
                    "id": len(agents),
                    "row": row,
                    "col": col,
                    "building_type": "residential",
                    "norm": 0.0,
                    "profile_id": profile_id,
                    "demand": profiles[profile_id],
                }
            )

    payload = {
        "grid": GRID,
        "hours": HOURS,
        "no_sun_day": NO_SUN_DAY,
        "building_type_filter": "residential",
        "source_note": "Prepared from local SF residential building load profiles and NASA POWER solar cache; raw source CSVs are omitted because they are large.",
        "load_profile_source": str(profiles_path),
        "building_metadata_source": str(metadata_path),
        "solar_source": str(solar_path),
        "solar_generation_rule": "generation_i(t) = normalized_solar(t) * max(demand_i)",
        "storage_rule": "storage_i(t) is carried-over unused surplus energy; no external battery-size data or capacity parameter is used.",
        "raw_residential_building_rows": residential_rows,
        "unique_residential_profiles": len(profiles),
        "sun": load_sun(solar_path),
        "agents": agents,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
