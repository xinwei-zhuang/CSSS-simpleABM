from __future__ import annotations

import argparse
import csv
import html
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


SIM_START = datetime(2025, 1, 1, 0)
SIM_HOURS = 24 * 30
NO_SUN_DAY = 15


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


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_scenario(input_path: Path, out_dir: Path, norm: float) -> dict[str, object]:
    start = time.perf_counter()
    agents, sun, config = load_input(input_path)
    pv_area_m2 = float(config.get("pv_area_m2", 1.0))
    battery_capacity_kwh = float(config.get("battery_capacity_kwh", 5.0))
    for a in agents:
        a.norm = norm
    by_cell = {(a.row, a.col): a for a in agents}
    hourly = []
    daily = []
    day_alive_area = 0.0

    for t in range(SIM_HOURS):
        deficits, surplus = {}, {}
        imports = {a: 0.0 for a in agents}
        exports = {a: 0.0 for a in agents}
        storage_start = {}

        for a in agents:
            d = a.demand[t]
            g = sun[t] * pv_area_m2
            storage_start[a] = a.storage
            a.generation = g
            available = a.storage + g
            a.storage = 0.0
            if available >= d:
                surplus[a] = available - d
            else:
                deficits[a] = d - available

        for requester, request in deficits.items():
            received = 0.0
            for donor in sorted(neighbors(requester, by_cell), key=lambda x: x.id):
                gift = min(surplus.get(donor, 0.0), request - received) * donor.norm
                surplus[donor] = surplus.get(donor, 0.0) - gift
                imports[requester] += gift
                exports[donor] += gift
                received += gift
                if received >= request:
                    break

        for donor, left in surplus.items():
            donor.storage = min(left, battery_capacity_kwh)

        alive_count = 0
        health_sum = 0.0
        for a in agents:
            if a.demand[t] > 0:
                a.health = max(0.0, min(1.0, (a.generation + storage_start[a] + imports[a] - exports[a]) / a.demand[t]))
            else:
                a.health = 1.0
            a.health_sum += a.health
            health_sum += a.health
            if a.health > 0:
                alive_count += 1
                a.alive_hours += 1

        alive_percent = 100.0 * alive_count / len(agents)
        q_t = health_sum / len(agents)
        resilience_so_far = sum(row["q_t"] for row in hourly + [{"q_t": q_t}]) / (t + 1)
        current_time = SIM_START + timedelta(hours=t)
        hourly.append(
            {
                "hour": t + 1,
                "time": current_time.strftime("%Y-%m-%d %H:%M"),
                "alive_percent": round(alive_percent, 4),
                "q_t": round(q_t, 6),
                "resilience": round(resilience_so_far, 6),
            }
        )
        day_alive_area += q_t

        if (t + 1) % 24 == 0:
            day = (t + 1) // 24
            date = (SIM_START + timedelta(days=day - 1)).strftime("%Y-%m-%d")
            daily.append(
                {
                    "day": day,
                    "date": date,
                    "alive_percent": round(sum(row["alive_percent"] for row in hourly[-24:]) / 24.0, 4),
                    "q_t": round(day_alive_area / 24.0, 6),
                    "resilience": round(day_alive_area / 24.0, 6),
                }
            )
            day_alive_area = 0.0

    summary = {
        "norm": norm,
        "building_type_filter": config.get("building_type_filter", "residential"),
        "load_profile_source": config.get("load_profile_source", "data/agents_initial.json"),
        "solar_source": config.get("solar_source", "data/agents_initial.json"),
        "solar_source_variable": config.get("solar_source_variable", "ALLSKY_SFC_SW_DWN"),
        "pv_area_m2": pv_area_m2,
        "battery_capacity_kwh": battery_capacity_kwh,
        "solar_generation_rule": config.get("solar_generation_rule", "generation_i(t) = solar_kwh_per_m2(t) * pv_area_m2"),
        "storage_rule": config.get("storage_rule", "storage_i(t) is capped at battery_capacity_kwh."),
        "agents": len(agents),
        "grid": f'{config["grid"]}x{config["grid"]}',
        "simulation_start": SIM_START.strftime("%Y-%m-%d %H:%M"),
        "simulation_end": (SIM_START + timedelta(hours=SIM_HOURS - 1)).strftime("%Y-%m-%d %H:%M"),
        "simulated_hours": SIM_HOURS,
        "simulated_days": SIM_HOURS // 24,
        "no_sun_day": NO_SUN_DAY,
        "no_sun_date": (SIM_START + timedelta(days=NO_SUN_DAY - 1)).strftime("%Y-%m-%d"),
        "alive_percent": round(sum(row["alive_percent"] for row in hourly) / len(hourly), 4),
        "resilience": round(sum(row["q_t"] for row in hourly) / len(hourly), 6),
        "runtime_seconds": round(time.perf_counter() - start, 3),
    }
    write_outputs(out_dir, agents, hourly, daily, summary)
    return summary


def write_outputs(
    out_dir: Path,
    agents: list[Agent],
    hourly: list[dict[str, object]],
    daily: list[dict[str, object]],
    summary: dict[str, object],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    agent_rows = [
        {
            "id": a.id,
            "row": a.row,
            "col": a.col,
            "building_type": a.building_type,
            "norm": round(a.norm, 4),
            "alive_percent": round(a.alive_percent, 4),
            "resilience": round(a.resilience, 6),
        }
        for a in agents
    ]
    write_csv(out_dir / "agents_final.csv", agent_rows)
    write_csv(out_dir / "hourly_metrics.csv", hourly)
    write_csv(out_dir / "daily_metrics.csv", daily)
    (out_dir / "model_data.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "data_structure": {
                    "agent_definition": ["building_type", "norm", "generation_i(t)", "demand_i(t)", "storage_i(t)", "health_i(t)"],
                    "building_performance_metrics": ["alive_percent", "resilience"],
                    "hourly_metrics": ["alive_percent", "q_t", "resilience"],
                    "daily_metrics": ["alive_percent", "q_t", "resilience"],
                },
                "agents": agent_rows,
                "hourly_metrics": hourly,
                "daily_metrics": daily,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def write_report(path: Path, summaries: list[dict[str, object]], base_out: Path) -> None:
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(s['norm']))}</td>"
        f"<td>{html.escape(str(s['simulation_start']))}</td>"
        f"<td>{html.escape(str(s['simulation_end']))}</td>"
        f"<td>{html.escape(str(s['no_sun_date']))}</td>"
        f"<td>{float(s['alive_percent']):.2f}%</td>"
        f"<td>{float(s['resilience']):.4f}</td>"
        "</tr>"
        for s in summaries
    )
    links = "\n".join(
        f"<li>norm = {html.escape(str(s['norm']))}: "
        f"<a href=\"norm_{int(float(s['norm']))}/daily_metrics.csv\">daily metrics</a>, "
        f"<a href=\"norm_{int(float(s['norm']))}/hourly_metrics.csv\">hourly metrics</a>, "
        f"<a href=\"norm_{int(float(s['norm']))}/agents_final.csv\">building metrics</a></li>"
        for s in summaries
    )
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Simple SF Energy Sharing ABM</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #17202a; background: #f7f8f4; }}
    table {{ border-collapse: collapse; width: 100%; background: white; margin: 16px 0; }}
    th, td {{ border: 1px solid #d8ddd2; padding: 8px; text-align: right; }}
    th {{ background: #e7ebdf; }}
    td:nth-child(2), td:nth-child(3), td:nth-child(4) {{ text-align: left; }}
    code {{ background: #eef1e8; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Simple SF Energy Sharing ABM</h1>
  <p>Simulation time is explicitly <strong>2025-01-01 00:00 through 2025-01-30 23:00</strong>, with no-sun disturbance on <strong>2025-01-15</strong>.</p>
  <p>Agent state: <code>A_i(t) = {{building_type, norm, generation_i(t), demand_i(t), storage_i(t), health_i(t)}}</code>. This version uses only residential buildings.</p>
  <p>Load profile source: local SF residential rows from <code>energy_profiles_hourly_used.csv</code>, joined to residential buildings in <code>building_energy_metadata.csv</code> by <code>profile_id</code>. The GitHub repo stores the compact prepared version in <code>data/agents_initial.json</code>.</p>
  <p>Solar generation potential source: cached NASA POWER 2025 hourly <code>ALLSKY_SFC_SW_DWN</code> for San Francisco. Each hourly value is converted from W/m2 to kWh/m2 by dividing by 1000. Generation uses the same fixed PV area for every grid cell: <code>generation_i(t) = solar_kwh_per_m2(t) * 1.0 m2</code>.</p>
  <p>Battery/storage size is fixed for every building: <code>battery_capacity_i = 5.0 kWh</code>. <code>storage_i(t)</code> carries unused surplus energy forward and is capped at 5.0 kWh.</p>
  <p>Building health is continuous: <code>health_i(t) = clip((generation + starting_storage + energy_received - energy_exported) / demand, 0, 1)</code>. A building is dead only when <code>health_i(t) = 0</code>.</p>
  <p>Energy sharing rule: <code>gift = min(surplus, energy_request) * norm_donor</code>. The two scenarios set every residential building's <code>norm_i</code> to 0 or 1.</p>
  <p>Only two performance metrics are reported: <strong>% building alive</strong> and <strong>resilience</strong>. Here <code>Q(t)</code> is system performance, defined as the average building health: <code>Q(t) = mean_i health_i(t)</code>. Resilience is normalized area under that curve: <code>R = integral Q(t) dt / integral Q0 dt</code>, where <code>Q0 = 1</code>.</p>
  <table>
    <thead><tr><th>Norm</th><th>Start</th><th>End</th><th>No-sun date</th><th>% Building Alive</th><th>Resilience</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>Files</h2>
  <ul>{links}</ul>
</body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--norm", type=float, choices=[0.0, 1.0], help="Run only one norm scenario.")
    parser.add_argument("--generosity", type=float, choices=[0.0, 1.0], help="Backward-compatible alias for --norm.")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    input_path = base / "data" / "agents_initial.json"
    outputs = base / "outputs"
    requested_norm = args.norm if args.norm is not None else args.generosity
    scenarios = [requested_norm] if requested_norm is not None else [0.0, 1.0]
    summaries = []

    for norm in scenarios:
        scenario_dir = outputs / f"norm_{int(norm)}"
        summaries.append(run_scenario(input_path, scenario_dir, norm))

    if len(summaries) == 2:
        write_csv(outputs / "comparison.csv", summaries)
        write_report(outputs / "report.html", summaries, outputs)

    for summary in summaries:
        print(
            f"norm={summary['norm']}, "
            f"alive_percent={summary['alive_percent']}, "
            f"resilience={summary['resilience']}, "
            f"time={summary['simulation_start']} to {summary['simulation_end']}"
        )


if __name__ == "__main__":
    main()
