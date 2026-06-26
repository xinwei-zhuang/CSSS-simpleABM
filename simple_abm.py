from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import reports


SIM_START = datetime(2025, 1, 1, 0)
SIM_HOURS = 24 * 30
NO_SUN_DAY = 15
DEAD_HOURS_UNTIL_PERMANENT = 24


@dataclass(eq=False)
class Agent:
    id: int
    row: int
    col: int
    building_type: str
    norm: float
    demand: list[float]
    storage: float = 0.0
    generation: float = 0.0
    health: float = 1.0
    alive_hours: int = 0
    health_sum: float = 0.0
    dead_streak: int = 0
    permanently_dead: bool = False

    @property
    def alive_percent(self) -> float:
        return 100.0 * self.alive_hours / SIM_HOURS

    @property
    def resilience(self) -> float:
        return self.health_sum / SIM_HOURS


def load_input(path: Path) -> tuple[list[Agent], list[float], dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    agents = [
        Agent(
            id=int(a["id"]),
            row=int(a["row"]),
            col=int(a["col"]),
            building_type=str(a.get("building_type", "residential")),
            norm=float(a.get("norm", 0.0)),
            demand=[float(x) for x in a["demand"]],
        )
        for a in data["agents"]
    ]
    return agents, [float(x) for x in data["sun"]], data


def neighbors(agent: Agent, agents_by_cell: dict[tuple[int, int], Agent]) -> list[Agent]:
    out = []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        other = agents_by_cell.get((agent.row + dr, agent.col + dc))
        if other:
            out.append(other)
    return out


def run_scenario(input_path: Path, out_dir: Path, norm: float) -> tuple[dict[str, object], list[list[int]], list[list[int]], list[list[list[int]]]]:
    start = time.perf_counter()
    agents, sun, config = load_input(input_path)
    pv_area_m2 = float(config.get("pv_area_m2", 1.0))
    pv_generation_scale = float(config.get("pv_generation_scale", 1.0))
    battery_capacity_kwh = float(config.get("battery_capacity_kwh", 5.0))
    for a in agents:
        a.norm = norm
    by_cell = {(a.row, a.col): a for a in agents}
    hourly = []
    daily = []
    frames = []
    storage_frames = []
    share_edges = []
    q_sum = 0.0
    day_q_sum = 0.0

    for t in range(SIM_HOURS):
        deficits, surplus = {}, {}
        imports = {a: 0.0 for a in agents}
        exports = {a: 0.0 for a in agents}
        storage_start = {}
        hour_edges = []

        for a in agents:
            if a.permanently_dead:
                a.generation = 0.0
                a.storage = 0.0
                storage_start[a] = 0.0
                continue
            d = a.demand[t]
            g = sun[t] * pv_area_m2 * pv_generation_scale
            storage_start[a] = a.storage
            a.generation = g
            available = a.storage + g
            a.storage = 0.0
            if available >= d:
                surplus[a] = available - d
            else:
                deficits[a] = d - available

        # Sharing (one simultaneous step, no ordering): each building with spare
        # energy splits norm * surplus equally among the neighbors that are short
        # this hour. norm = 0 shares nothing; norm = 1 shares all spare energy.
        for donor in agents:
            spare = surplus.get(donor, 0.0)
            if spare <= 0.0 or donor.norm <= 0.0:
                continue
            short = [n for n in neighbors(donor, by_cell) if n in deficits]
            if not short:
                continue
            given = donor.norm * spare
            gift = given / len(short)
            for n in short:
                imports[n] += gift
                hour_edges.append([donor.id, n.id])
            exports[donor] += given
            surplus[donor] = spare - given

        for donor, left in surplus.items():
            donor.storage = min(left, battery_capacity_kwh)

        alive_count = 0
        health_sum = 0.0
        frame = []
        for a in agents:
            if a.permanently_dead:
                a.health = 0.0
            elif a.demand[t] > 0:
                a.health = max(0.0, min(1.0, (a.generation + storage_start[a] + imports[a] - exports[a]) / a.demand[t]))
            else:
                a.health = 1.0

            if not a.permanently_dead:
                if a.health <= 0.0:
                    a.dead_streak += 1
                    if a.dead_streak >= DEAD_HOURS_UNTIL_PERMANENT:
                        a.permanently_dead = True
                        a.health = 0.0
                else:
                    a.dead_streak = 0

            a.health_sum += a.health
            health_sum += a.health
            frame.append(-1 if a.permanently_dead else int(round(a.health * 100)))
            if a.health > 0:
                alive_count += 1
                a.alive_hours += 1

        frames.append(frame)
        storage_frames.append([int(round(a.storage * 100)) for a in agents])
        share_edges.append(hour_edges)
        q_t = health_sum / len(agents)
        q_sum += q_t
        day_q_sum += q_t
        hourly.append(
            {
                "hour": t + 1,
                "time": (SIM_START + timedelta(hours=t)).strftime("%Y-%m-%d %H:%M"),
                "alive_percent": round(100.0 * alive_count / len(agents), 4),
                "resilience": round(q_sum / (t + 1), 6),
            }
        )

        if (t + 1) % 24 == 0:
            day = (t + 1) // 24
            daily.append(
                {
                    "day": day,
                    "date": (SIM_START + timedelta(days=day - 1)).strftime("%Y-%m-%d"),
                    "alive_percent": round(sum(row["alive_percent"] for row in hourly[-24:]) / 24.0, 4),
                    "resilience": round(day_q_sum / 24.0, 6),
                }
            )
            day_q_sum = 0.0

    summary = {
        "norm": norm,
        "building_type_filter": config.get("building_type_filter", "residential"),
        "load_profile_source": config.get("load_profile_source", "data/agents_initial.json"),
        "solar_source": config.get("solar_source", "data/agents_initial.json"),
        "pv_area_m2": pv_area_m2,
        "pv_generation_scale": pv_generation_scale,
        "battery_capacity_kwh": battery_capacity_kwh,
        "agents": len(agents),
        "grid": f'{config["grid"]}x{config["grid"]}',
        "simulation_start": SIM_START.strftime("%Y-%m-%d %H:%M"),
        "simulation_end": (SIM_START + timedelta(hours=SIM_HOURS - 1)).strftime("%Y-%m-%d %H:%M"),
        "simulated_hours": SIM_HOURS,
        "no_sun_day": NO_SUN_DAY,
        "no_sun_date": (SIM_START + timedelta(days=NO_SUN_DAY - 1)).strftime("%Y-%m-%d"),
        "dead_hours_until_permanent": DEAD_HOURS_UNTIL_PERMANENT,
        "alive_percent": round(sum(row["alive_percent"] for row in hourly) / len(hourly), 4),
        "resilience": round(q_sum / SIM_HOURS, 6),
        "runtime_seconds": round(time.perf_counter() - start, 3),
    }
    reports.write_outputs(out_dir, agents, hourly, daily, summary)
    return summary, frames, storage_frames, share_edges


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--norm", type=float, choices=[0.0, 1.0], help="Run only one norm scenario.")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    input_path = base / "data" / "agents_initial.json"
    outputs = base / "outputs"
    start = SIM_START.strftime("%Y-%m-%d %H:%M")
    scenarios = [args.norm] if args.norm is not None else [0.0, 1.0]
    summaries = []
    frames_by_norm = {}
    storage_by_norm = {}
    edges_by_norm = {}

    for norm in scenarios:
        summary, frames, storage_frames, share_edges = run_scenario(input_path, outputs / f"norm_{int(norm)}", norm)
        summaries.append(summary)
        frames_by_norm[f"norm_{int(norm)}"] = frames
        storage_by_norm[f"norm_{int(norm)}"] = storage_frames
        edges_by_norm[f"norm_{int(norm)}"] = share_edges

    if len(summaries) == 2:
        reports.write_csv(outputs / "comparison.csv", summaries)
        reports.write_report(outputs / "report.html", summaries)
        reports.write_animation(outputs / "animation.html", start, summaries, frames_by_norm, edges_by_norm)
        reports.write_agent_grid(outputs / "agent_grid.html", start, input_path, summaries, storage_by_norm)

    for summary in summaries:
        print(f"norm={summary['norm']}, alive_percent={summary['alive_percent']}, resilience={summary['resilience']}, "
              f"time={summary['simulation_start']} to {summary['simulation_end']}")


if __name__ == "__main__":
    main()
