from __future__ import annotations

import csv
import html
import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(eq=False)
class Agent:
    id: int
    row: int
    col: int
    demand: list[float]
    norm: float
    health: float = 1.0
    generation: float = 0.0
    demand_now: float = 0.0
    storage: float = 0.0
    total_demand: float = 0.0
    total_generated: float = 0.0
    total_self_served: float = 0.0
    total_imported: float = 0.0
    total_exported: float = 0.0
    total_unmet: float = 0.0

    @property
    def service_ratio(self) -> float:
        return 1.0 if self.total_demand <= 0 else 1.0 - self.total_unmet / self.total_demand


def load_input(path: Path) -> tuple[list[Agent], list[float], dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    agents = [
        Agent(
            id=int(a["id"]),
            row=int(a["row"]),
            col=int(a["col"]),
            demand=[float(x) for x in a["demand"]],
            norm=float(a["norm"]),
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


def run(input_path: Path, out_dir: Path) -> dict[str, object]:
    start = time.perf_counter()
    agents, sun, config = load_input(input_path)
    by_cell = {(a.row, a.col): a for a in agents}
    total_demand = total_generated = total_shared = total_unmet = 0.0
    daily = []
    day_demand = day_generated = day_shared = day_unmet = 0.0

    for t in range(int(config["hours"])):
        deficit, surplus = {}, {}
        for a in agents:
            d = a.demand[t]
            g = sun[t] * max(a.demand)
            a.demand_now, a.generation = d, g
            available = a.storage + g
            served = min(d, available)
            a.storage = 0.0
            if available > d:
                surplus[a] = available - d
            elif d > available:
                deficit[a] = d - available
            a.health = served / d if d else 1.0
            a.total_demand += d
            a.total_generated += g
            a.total_self_served += served
            total_demand += d
            total_generated += g
            day_demand += d
            day_generated += g

        for requester, request in deficit.items():
            got = 0.0
            for donor in sorted(neighbors(requester, by_cell), key=lambda x: -x.norm):
                give = min(surplus.get(donor, 0.0), request - got) * donor.norm
                surplus[donor] = surplus.get(donor, 0.0) - give
                donor.total_exported += give
                got += give
                if got >= request:
                    break
            requester.health = min(1.0, requester.health + got / requester.demand_now)
            requester.total_imported += got
            requester.total_unmet += max(0.0, request - got)
            total_shared += got
            total_unmet += max(0.0, request - got)
            day_shared += got
            day_unmet += max(0.0, request - got)

        for donor, left in surplus.items():
            donor.storage = left

        if (t + 1) % 24 == 0:
            daily.append(
                {
                    "day": (t + 1) // 24,
                    "demand": round(day_demand, 3),
                    "generated": round(day_generated, 3),
                    "shared": round(day_shared, 3),
                    "unmet": round(day_unmet, 3),
                    "service_ratio": round(1.0 - day_unmet / day_demand, 4) if day_demand else 1.0,
                }
            )
            day_demand = day_generated = day_shared = day_unmet = 0.0

    summary = {
        "agents": len(agents),
        "grid": f'{config["grid"]}x{config["grid"]}',
        "simulated_hours": int(config["hours"]),
        "simulated_days": int(config["hours"]) // 24,
        "no_sun_day": int(config["no_sun_day"]),
        "total_demand": round(total_demand, 2),
        "total_generated": round(total_generated, 2),
        "total_shared": round(total_shared, 2),
        "total_unmet": round(total_unmet, 2),
        "service_ratio": 1.0 - total_unmet / total_demand if total_demand else 1.0,
        "mean_final_health": sum(a.health for a in agents) / len(agents),
        "mean_norm": sum(a.norm for a in agents) / len(agents),
        "runtime_seconds": time.perf_counter() - start,
    }
    write_outputs(out_dir, agents, daily, summary)
    return summary


def write_outputs(out_dir: Path, agents: list[Agent], daily: list[dict[str, object]], summary: dict[str, object]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    agent_rows = [
        {
            "id": a.id,
            "row": a.row,
            "col": a.col,
            "norm": round(a.norm, 4),
            "final_health": round(a.health, 4),
            "service_ratio": round(a.service_ratio, 4),
            "total_demand": round(a.total_demand, 3),
            "total_generated": round(a.total_generated, 3),
            "total_self_served": round(a.total_self_served, 3),
            "total_imported": round(a.total_imported, 3),
            "total_exported": round(a.total_exported, 3),
            "total_unmet": round(a.total_unmet, 3),
            "final_storage": round(a.storage, 3),
        }
        for a in agents
    ]
    write_csv(out_dir / "agents_final.csv", agent_rows)
    write_csv(out_dir / "daily_metrics.csv", daily)
    (out_dir / "model_data.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "data_structure": {
                    "agent_state": ["health", "generation", "demand", "storage", "norm"],
                    "agent_metrics": list(agent_rows[0]),
                    "daily_metrics": list(daily[0]),
                },
                "agents": agent_rows,
                "daily_metrics": daily,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    write_html(out_dir / "report.html", agent_rows, daily, summary)


def write_html(path: Path, agents: list[dict[str, object]], daily: list[dict[str, object]], summary: dict[str, object]) -> None:
    worst = sorted(agents, key=lambda r: float(r["service_ratio"]))[:12]
    best = sorted(agents, key=lambda r: float(r["service_ratio"]), reverse=True)[:12]
    max_demand = max(float(d["demand"]) for d in daily) or 1.0
    bars = "\n".join(
        f'<div class="bar-row"><span>Day {int(d["day"]):02d}</span><b style="width:{100 * float(d["demand"]) / max_demand:.1f}%"></b><em>{float(d["service_ratio"]):.2f}</em></div>'
        for d in daily
    )

    def table(rows: list[dict[str, object]]) -> str:
        cols = ["id", "row", "col", "norm", "service_ratio", "total_demand", "total_imported", "total_unmet"]
        head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        body = "\n".join("<tr>" + "".join(f"<td>{html.escape(str(r[c]))}</td>" for c in cols) + "</tr>" for r in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Simple SF Energy Sharing ABM</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #17202a; background: #f7f8f4; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 20px 0; }}
    .cards section {{ background: white; border: 1px solid #d8ddd2; border-radius: 6px; padding: 12px; }}
    .cards strong {{ display: block; color: #56624f; font-size: 12px; text-transform: uppercase; }}
    .cards span {{ display: block; margin-top: 6px; font-size: 22px; }}
    table {{ border-collapse: collapse; width: 100%; background: white; margin: 12px 0 24px; }}
    th, td {{ border: 1px solid #d8ddd2; padding: 7px 8px; text-align: right; }}
    th {{ background: #e7ebdf; }}
    .bar-row {{ display: grid; grid-template-columns: 64px 1fr 48px; gap: 8px; align-items: center; margin: 4px 0; }}
    .bar-row b {{ display: block; height: 14px; background: #5a8f7b; border-radius: 2px; }}
    code {{ background: #eef1e8; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Simple SF Energy Sharing ABM</h1>
  <p><code>A_i(t) = {{health_i(t), generation_i(t), demand_i(t), storage_i(t), norm_i}}</code>. The only behavioral parameter is <code>norm_i ~ Uniform(0, 1)</code>.</p>
  <div class="cards">
    <section><strong>Agents</strong><span>{summary["agents"]}</span></section>
    <section><strong>Hours</strong><span>{summary["simulated_hours"]}</span></section>
    <section><strong>No Sun Day</strong><span>{summary["no_sun_day"]}</span></section>
    <section><strong>Service Ratio</strong><span>{summary["service_ratio"]:.3f}</span></section>
    <section><strong>Shared Energy</strong><span>{summary["total_shared"]:.1f}</span></section>
    <section><strong>Runtime Seconds</strong><span>{summary["runtime_seconds"]:.2f}</span></section>
  </div>
  <h2>Environment</h2>
  <p>One building per cell on a 36 x 36 San Francisco grid. Simulation length is 30 days / 720 hourly steps, with day 15 set to no solar generation.</p>
  <h2>Energy Sharing Rule</h2>
  <p>After self-use and storage, deficit agents request energy from four direct neighbors. Donors give <code>min(surplus, energy_request) * norm_donor</code>.</p>
  <h2>Daily Demand And Service Ratio</h2>
  {bars}
  <h2>Lowest Performing Buildings</h2>
  {table(worst)}
  <h2>Highest Performing Buildings</h2>
  {table(best)}
</body>
</html>
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    summary = run(base / "data" / "agents_initial.json", base / "outputs")
    for key, value in summary.items():
        print(f"{key},{value}")
