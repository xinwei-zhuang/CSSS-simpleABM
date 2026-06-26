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
DEAD_HOURS_UNTIL_PERMANENT = 24
CRITICAL_HEALTH = 0.05


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
    critical_hours: int = 0
    health_sum: float = 0.0
    dead_streak: int = 0
    permanently_dead: bool = False

    @property
    def alive_percent(self) -> float:
        return 100.0 * self.alive_hours / SIM_HOURS

    @property
    def critical_percent(self) -> float:
        return 100.0 * self.critical_hours / SIM_HOURS

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
    day_alive_area = 0.0

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

        for requester, request in deficits.items():
            received = 0.0
            for donor in sorted(neighbors(requester, by_cell), key=lambda x: x.id):
                gift = min(surplus.get(donor, 0.0), request - received) * donor.norm
                surplus[donor] = surplus.get(donor, 0.0) - gift
                imports[requester] += gift
                exports[donor] += gift
                received += gift
                if gift > 1e-9:
                    hour_edges.append([donor.id, requester.id])
                if received >= request:
                    break

        for donor, left in surplus.items():
            donor.storage = min(left, battery_capacity_kwh)

        alive_count = 0
        critical_count = 0
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
                if a.health < CRITICAL_HEALTH:
                    critical_count += 1
                    a.critical_hours += 1

        frames.append(frame)
        storage_frames.append([int(round(a.storage * 100)) for a in agents])
        share_edges.append(hour_edges)
        alive_percent = 100.0 * alive_count / len(agents)
        critical_percent = 100.0 * critical_count / len(agents)
        permadead_percent = 100.0 * sum(1 for a in agents if a.permanently_dead) / len(agents)
        q_t = health_sum / len(agents)
        resilience_so_far = sum(row["q_t"] for row in hourly + [{"q_t": q_t}]) / (t + 1)
        current_time = SIM_START + timedelta(hours=t)
        hourly.append(
            {
                "hour": t + 1,
                "time": current_time.strftime("%Y-%m-%d %H:%M"),
                "alive_percent": round(alive_percent, 4),
                "critical_percent": round(critical_percent, 4),
                "permadead_percent": round(permadead_percent, 4),
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
                    "critical_percent": round(sum(row["critical_percent"] for row in hourly[-24:]) / 24.0, 4),
                    "permadead_percent": round(hourly[-1]["permadead_percent"], 4),
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
        "pv_generation_scale": pv_generation_scale,
        "battery_capacity_kwh": battery_capacity_kwh,
        "agents": len(agents),
        "grid": f'{config["grid"]}x{config["grid"]}',
        "simulation_start": SIM_START.strftime("%Y-%m-%d %H:%M"),
        "simulation_end": (SIM_START + timedelta(hours=SIM_HOURS - 1)).strftime("%Y-%m-%d %H:%M"),
        "simulated_hours": SIM_HOURS,
        "simulated_days": SIM_HOURS // 24,
        "no_sun_day": NO_SUN_DAY,
        "no_sun_date": (SIM_START + timedelta(days=NO_SUN_DAY - 1)).strftime("%Y-%m-%d"),
        "dead_hours_until_permanent": DEAD_HOURS_UNTIL_PERMANENT,
        "critical_health_threshold": CRITICAL_HEALTH,
        "alive_percent": round(sum(row["alive_percent"] for row in hourly) / len(hourly), 4),
        "critical_percent": round(sum(row["critical_percent"] for row in hourly) / len(hourly), 4),
        "permadead_percent_final": round(hourly[-1]["permadead_percent"], 4),
        "resilience": round(sum(row["q_t"] for row in hourly) / len(hourly), 6),
        "runtime_seconds": round(time.perf_counter() - start, 3),
    }
    write_outputs(out_dir, agents, hourly, daily, summary)
    return summary, frames, storage_frames, share_edges


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
            "critical_percent": round(a.critical_percent, 4),
            "permanently_dead": a.permanently_dead,
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
                    "building_performance_metrics": ["alive_percent", "critical_percent", "permanently_dead", "resilience"],
                    "hourly_metrics": ["alive_percent", "critical_percent", "permadead_percent", "q_t", "resilience"],
                    "daily_metrics": ["alive_percent", "critical_percent", "permadead_percent", "resilience"],
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
        f"<td>{float(s['critical_percent']):.2f}%</td>"
        f"<td>{float(s['permadead_percent_final']):.2f}%</td>"
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
  <p>Solar generation potential source: cached NASA POWER 2025 hourly <code>ALLSKY_SFC_SW_DWN</code> for San Francisco. Each hourly value is converted from W/m2 to kWh/m2 by dividing by 1000. Generation uses the same fixed PV area and the same global scaling factor for every grid cell: <code>generation_i(t) = solar_kwh_per_m2(t) * 1.0 m2 * 5.0</code>.</p>
  <p>Battery/storage size is fixed for every building: <code>battery_capacity_i = 5.0 kWh</code>. <code>storage_i(t)</code> carries unused surplus energy forward and is capped at 5.0 kWh.</p>
  <p>Building health is continuous: <code>health_i(t) = clip((generation + starting_storage + energy_received - energy_exported) / demand, 0, 1)</code>. A building is dead at hour <code>t</code> when <code>health_i(t) = 0</code>, and is <strong>critical</strong> when <code>0 &lt; health_i(t) &lt; 0.05</code>. If a building stays dead for <strong>24 consecutive hours</strong> it is <strong>permanently dead</strong>: from then on its health is fixed at 0 and it never revives, generates, or shares.</p>
  <p>Energy sharing rule: <code>gift = min(surplus, energy_request) * norm_donor</code>. The two scenarios set every residential building's <code>norm_i</code> to 0 or 1.</p>
  <p>Performance metrics: <strong>% building alive</strong> (<code>health &gt; 0</code>), <strong>% critical</strong> (<code>health &lt; 5%</code>), <strong>% permanently dead</strong>, and <strong>resilience</strong>. Here <code>Q(t)</code> is system performance, defined as the average building health: <code>Q(t) = mean_i health_i(t)</code>. Resilience is normalized area under that curve: <code>R = integral Q(t) dt / integral Q0 dt</code>, where <code>Q0 = 1</code>.</p>
  <table>
    <thead><tr><th>Norm</th><th>Start</th><th>End</th><th>No-sun date</th><th>% Building Alive</th><th>% Critical</th><th>% Perma-dead</th><th>Resilience</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>Files</h2>
  <ul>{links}<li><a href="animation.html">animated health grid</a></li><li><a href="agent_grid.html">building generation/demand/storage hover grid</a></li></ul>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_animation(
    path: Path,
    summaries: list[dict[str, object]],
    frames_by_norm: dict[str, list[list[int]]],
    edges_by_norm: dict[str, list[list[list[int]]]],
) -> None:
    payload = {
        "grid": 36,
        "start": SIM_START.strftime("%Y-%m-%d %H:%M"),
        "frames_by_norm": frames_by_norm,
        "edges_by_norm": edges_by_norm,
        "summaries": summaries,
    }
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Simple ABM Health Animation</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 28px; color: #17202a; background: #f7f8f4; }}
    h1 {{ margin-bottom: 4px; }}
    p.lead {{ margin-top: 4px; max-width: 920px; }}
    .controls {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin: 16px 0; padding: 12px 14px; background: white; border: 1px solid #cfd7c7; border-radius: 6px; }}
    .controls button {{ padding: 7px 14px; border: 1px solid #9aa78d; background: #f3f6ee; border-radius: 4px; cursor: pointer; font: inherit; }}
    .controls button:hover {{ background: #e7ebdf; }}
    input[type="range"] {{ flex: 1 1 320px; min-width: 220px; }}
    #clock {{ font-variant-numeric: tabular-nums; font-weight: bold; min-width: 230px; }}
    .panels {{ display: grid; grid-template-columns: repeat(2, minmax(280px, 1fr)); gap: 24px; align-items: start; }}
    .panel {{ background: white; border: 1px solid #cfd7c7; border-radius: 6px; padding: 14px; }}
    .panel h2 {{ margin: 0 0 8px; font-size: 17px; }}
    canvas {{ width: 100%; aspect-ratio: 1; background: #11150f; border: 1px solid #cfd7c7; display: block; }}
    .stat {{ margin-top: 10px; font-variant-numeric: tabular-nums; }}
    .legend {{ margin-top: 16px; display: flex; gap: 18px; flex-wrap: wrap; }}
    .swatch {{ display: inline-block; width: 14px; height: 14px; vertical-align: -2px; margin-right: 5px; border: 1px solid #888; }}
    code {{ background: #eef1e8; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Building Health Animation</h1>
  <p class="lead">Each square is one residential building in the 36 x 36 grid. Color shows <code>health_i(t)</code> as a 5-band heatmap from deep red (0&ndash;20%) through green (80&ndash;100%). A cell is <strong>black</strong> once a building has been dead for 24 hours and is permanently dead (it never revives). <strong>Black</strong> lines mark donor&ndash;recipient pairs that have shared at any earlier hour; <strong>white</strong> lines show sharing in the current hour. One time slider drives both scenarios so they stay in sync.</p>
  <div class="controls">
    <button id="play">Play</button>
    <button id="pause">Pause</button>
    <button id="step">Step</button>
    <input id="frameSlider" type="range" min="0" max="719" value="0">
    <span id="clock"></span>
  </div>
  <div class="panels">
    <div class="panel">
      <h2>norm = 0 &middot; no sharing</h2>
      <canvas id="grid_norm_0" width="720" height="720"></canvas>
      <div class="stat" id="stat_norm_0"></div>
    </div>
    <div class="panel">
      <h2>norm = 1 &middot; full sharing</h2>
      <canvas id="grid_norm_1" width="720" height="720"></canvas>
      <div class="stat" id="stat_norm_1"></div>
    </div>
  </div>
  <div class="legend">
    <div><strong>health:</strong></div>
    <div><span class="swatch" style="background:#d73027"></span>0&ndash;20%</div>
    <div><span class="swatch" style="background:#fc8d59"></span>20&ndash;40%</div>
    <div><span class="swatch" style="background:#fee08b"></span>40&ndash;60%</div>
    <div><span class="swatch" style="background:#91cf60"></span>60&ndash;80%</div>
    <div><span class="swatch" style="background:#1a9850"></span>80&ndash;100%</div>
    <div><span class="swatch" style="background:#000000"></span>permanently dead (24 h)</div>
    <div><strong>sharing:</strong></div>
    <div><span class="swatch" style="background:#000000"></span>black line = has shared earlier</div>
    <div><span class="swatch" style="background:#ffffff"></span>white line = sharing now</div>
  </div>
  <script>
    const DATA = {json.dumps(payload, separators=(",", ":"))};
    const SCENARIOS = ["norm_0", "norm_1"];
    const grid = DATA.grid;
    const slider = document.getElementById("frameSlider");
    const clock = document.getElementById("clock");
    const views = SCENARIOS.map(key => {{
      const canvas = document.getElementById("grid_" + key);
      return {{ key, canvas, ctx: canvas.getContext("2d"), stat: document.getElementById("stat_" + key), cell: canvas.width / grid }};
    }});
    let timer = null;

    // 5-band heatmap for health 0-100 (red = low, green = high); black = permanently dead.
    const HEAT = [
      {{ max: 20, color: "#d73027" }},   //  0-20  deep red
      {{ max: 40, color: "#fc8d59" }},   // 20-40  orange
      {{ max: 60, color: "#fee08b" }},   // 40-60  yellow
      {{ max: 80, color: "#91cf60" }},   // 60-80  light green
      {{ max: 101, color: "#1a9850" }},  // 80-100 green
    ];

    function color(value) {{
      if (value < 0) return "#000000";   // permanently dead (24 h dead)
      for (const band of HEAT) if (value < band.max) return band.color;
      return "#1a9850";
    }}

    function frameTime(index) {{
      const d = new Date("2025-01-01T00:00:00");
      d.setHours(d.getHours() + index);
      return d.toISOString().slice(0, 16).replace("T", " ");
    }}

    function drawEdge(ctx, cell, from, to) {{
      const fr = Math.floor(from / grid), fc = from % grid;
      const tr = Math.floor(to / grid), tc = to % grid;
      ctx.beginPath();
      ctx.moveTo((fc + 0.5) * cell, (fr + 0.5) * cell);
      ctx.lineTo((tc + 0.5) * cell, (tr + 0.5) * cell);
      ctx.stroke();
    }}

    function drawView(view, i) {{
      const ctx = view.ctx, cell = view.cell, N = grid * grid;
      const frame = DATA.frames_by_norm[view.key][i];
      let q = 0, alive = 0, critical = 0, perma = 0;
      for (let r = 0; r < grid; r++) {{
        for (let c = 0; c < grid; c++) {{
          const v = frame[r * grid + c];
          q += (v < 0 ? 0 : v) / 100;
          if (v < 0) perma++;
          else if (v > 0 && v < 5) critical++;
          if (v > 0) alive++;
          ctx.fillStyle = color(v);
          ctx.fillRect(c * cell, r * cell, cell, cell);
        }}
      }}
      ctx.strokeStyle = "rgba(255,255,255,0.12)";
      ctx.lineWidth = 1;
      for (let k = 0; k <= grid; k++) {{
        ctx.beginPath(); ctx.moveTo(k * cell, 0); ctx.lineTo(k * cell, view.canvas.height); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, k * cell); ctx.lineTo(view.canvas.width, k * cell); ctx.stroke();
      }}
      // black edges: every donor->requester pair that has shared at any hour up to now
      if (view.cum === undefined || i < view.cumUpto) {{ view.cum = new Set(); view.cumUpto = -1; }}
      for (let t = view.cumUpto + 1; t <= i; t++) {{
        for (const [from, to] of DATA.edges_by_norm[view.key][t]) view.cum.add(from * N + to);
      }}
      view.cumUpto = i;
      ctx.strokeStyle = "rgba(0,0,0,0.55)";
      ctx.lineWidth = 1.5;
      for (const key of view.cum) drawEdge(ctx, cell, Math.floor(key / N), key % N);
      // white edges: sharing active in the current hour
      ctx.strokeStyle = "rgba(255,255,255,0.95)";
      ctx.lineWidth = 2.5;
      for (const [from, to] of DATA.edges_by_norm[view.key][i]) drawEdge(ctx, cell, from, to);

      q = q / frame.length;
      const edgeCount = DATA.edges_by_norm[view.key][i].length;
      view.stat.innerHTML = `alive <strong>${{(alive / frame.length * 100).toFixed(1)}}%</strong> &middot; critical <strong>${{(critical / frame.length * 100).toFixed(1)}}%</strong> &middot; perma-dead <strong>${{(perma / frame.length * 100).toFixed(1)}}%</strong> &middot; Q(t) <strong>${{q.toFixed(3)}}</strong> &middot; sharing now ${{edgeCount}}`;
    }}

    function draw() {{
      const i = Number(slider.value);
      clock.textContent = `Hour ${{i + 1}} / 720  |  ${{frameTime(i)}}`;
      for (const view of views) drawView(view, i);
    }}

    function play() {{
      if (timer) return;
      timer = setInterval(() => {{
        slider.value = (Number(slider.value) + 1) % 720;
        draw();
      }}, 90);
    }}

    function pause() {{
      clearInterval(timer);
      timer = null;
    }}

    document.getElementById("play").onclick = play;
    document.getElementById("pause").onclick = pause;
    document.getElementById("step").onclick = () => {{ pause(); slider.value = (Number(slider.value) + 1) % 720; draw(); }};
    slider.oninput = draw;
    draw();
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def write_agent_grid(path: Path, input_path: Path, summaries: list[dict[str, object]], storage_by_norm: dict[str, list[list[int]]]) -> None:
    source = json.loads(input_path.read_text(encoding="utf-8"))
    pv_area_m2 = float(source.get("pv_area_m2", 1.0))
    pv_generation_scale = float(source.get("pv_generation_scale", 1.0))
    generation = [round(float(v) * pv_area_m2 * pv_generation_scale, 4) for v in source["sun"]]
    agents = [
        {
            "id": int(a["id"]),
            "row": int(a["row"]),
            "col": int(a["col"]),
            "building_type": a.get("building_type", "residential"),
            "norm": float(a.get("norm", 0.0)),
            "profile_id": a.get("profile_id", ""),
            "demand": [round(float(v), 4) for v in a["demand"]],
        }
        for a in source["agents"]
    ]
    payload = {
        "grid": int(source["grid"]),
        "hours": int(source["hours"]),
        "start": SIM_START.strftime("%Y-%m-%d %H:%M"),
        "pv_area_m2": pv_area_m2,
        "pv_generation_scale": pv_generation_scale,
        "battery_capacity_kwh": float(source.get("battery_capacity_kwh", 5.0)),
        "generation": generation,
        "agents": agents,
        "storage_by_norm": storage_by_norm,
        "summaries": summaries,
    }
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Building Energy Curves</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 28px; color: #17202a; background: #f7f8f4; }}
    main {{ display: grid; grid-template-columns: minmax(360px, 620px) minmax(340px, 1fr); gap: 24px; align-items: start; }}
    canvas {{ background: white; border: 1px solid #cfd7c7; }}
    #grid {{ width: 100%; max-width: 620px; aspect-ratio: 1; }}
    #chart {{ width: 100%; max-width: 780px; height: 360px; }}
    label, select {{ font: inherit; }}
    .legend span {{ display: inline-block; width: 18px; height: 3px; margin-right: 6px; vertical-align: middle; }}
    code {{ background: #eef1e8; padding: 2px 4px; border-radius: 4px; }}
    .meta {{ line-height: 1.5; }}
  </style>
</head>
<body>
  <h1>Generation / Demand / Storage Grid</h1>
  <p>Hover over a cell in the 36 x 36 grid to show that building's 720-hour curves.</p>
  <main>
    <section>
      <canvas id="grid" width="720" height="720"></canvas>
    </section>
    <section>
      <label>Storage scenario
        <select id="scenario">
          <option value="norm_0">norm = 0</option>
          <option value="norm_1">norm = 1</option>
        </select>
      </label>
      <p class="legend">
        <span style="background:#2f6fbb"></span>generation
        <span style="background:#b4493f"></span>demand
        <span style="background:#2f8f58"></span>storage
      </p>
      <canvas id="chart" width="860" height="360"></canvas>
      <div id="meta" class="meta"></div>
    </section>
  </main>
  <script>
    const DATA = {json.dumps(payload, separators=(",", ":"))};
    const gridCanvas = document.getElementById("grid");
    const gridCtx = gridCanvas.getContext("2d");
    const chart = document.getElementById("chart");
    const chartCtx = chart.getContext("2d");
    const scenario = document.getElementById("scenario");
    const meta = document.getElementById("meta");
    const grid = DATA.grid;
    const cell = gridCanvas.width / grid;
    let selected = 0;

    function drawGrid() {{
      for (const a of DATA.agents) {{
        const peak = Math.max(...a.demand);
        const shade = Math.max(35, 240 - Math.min(180, peak * 30));
        gridCtx.fillStyle = `rgb(${{shade}},${{shade}},${{shade}})`;
        gridCtx.fillRect(a.col * cell, a.row * cell, cell, cell);
      }}
      gridCtx.strokeStyle = "rgba(255,255,255,0.4)";
      for (let i = 0; i <= grid; i++) {{
        gridCtx.beginPath(); gridCtx.moveTo(i * cell, 0); gridCtx.lineTo(i * cell, gridCanvas.height); gridCtx.stroke();
        gridCtx.beginPath(); gridCtx.moveTo(0, i * cell); gridCtx.lineTo(gridCanvas.width, i * cell); gridCtx.stroke();
      }}
      const a = DATA.agents[selected];
      gridCtx.strokeStyle = "#f2c94c";
      gridCtx.lineWidth = 4;
      gridCtx.strokeRect(a.col * cell + 2, a.row * cell + 2, cell - 4, cell - 4);
      gridCtx.lineWidth = 1;
    }}

    function drawLine(values, maxY, color) {{
      chartCtx.beginPath();
      values.forEach((v, i) => {{
        const x = 48 + i / (values.length - 1) * (chart.width - 68);
        const y = 26 + (1 - v / maxY) * (chart.height - 66);
        if (i === 0) chartCtx.moveTo(x, y); else chartCtx.lineTo(x, y);
      }});
      chartCtx.strokeStyle = color;
      chartCtx.lineWidth = 2;
      chartCtx.stroke();
    }}

    function drawChart() {{
      const a = DATA.agents[selected];
      const storageFrames = DATA.storage_by_norm[scenario.value];
      const storage = storageFrames.map(frame => frame[selected] / 100);
      const demand = a.demand;
      const generation = DATA.generation;
      const maxY = Math.max(0.1, ...demand, ...generation, ...storage);

      chartCtx.clearRect(0, 0, chart.width, chart.height);
      chartCtx.fillStyle = "white";
      chartCtx.fillRect(0, 0, chart.width, chart.height);
      chartCtx.strokeStyle = "#cfd7c7";
      chartCtx.strokeRect(48, 26, chart.width - 68, chart.height - 66);
      chartCtx.fillStyle = "#42513d";
      chartCtx.fillText("kWh", 12, 24);
      chartCtx.fillText("hour 1", 48, chart.height - 18);
      chartCtx.fillText("hour 720", chart.width - 88, chart.height - 18);
      chartCtx.fillText(maxY.toFixed(2), 8, 34);
      chartCtx.fillText("0", 28, chart.height - 40);

      drawLine(generation, maxY, "#2f6fbb");
      drawLine(demand, maxY, "#b4493f");
      drawLine(storage, maxY, "#2f8f58");

      meta.innerHTML = `
        <strong>Building ${{a.id}}</strong><br>
        grid row/col: ${{a.row}}, ${{a.col}}<br>
        type: ${{a.building_type}}<br>
        load profile: <code>${{a.profile_id}}</code><br>
        PV area: ${{DATA.pv_area_m2}} m2, PV scale: ${{DATA.pv_generation_scale}}, battery: ${{DATA.battery_capacity_kwh}} kWh<br>
        demand range: ${{Math.min(...demand).toFixed(3)}} to ${{Math.max(...demand).toFixed(3)}} kWh
      `;
      drawGrid();
    }}

    gridCanvas.addEventListener("mousemove", event => {{
      const rect = gridCanvas.getBoundingClientRect();
      const x = (event.clientX - rect.left) * gridCanvas.width / rect.width;
      const y = (event.clientY - rect.top) * gridCanvas.height / rect.height;
      const col = Math.min(grid - 1, Math.max(0, Math.floor(x / cell)));
      const row = Math.min(grid - 1, Math.max(0, Math.floor(y / cell)));
      selected = row * grid + col;
      drawChart();
    }});
    scenario.onchange = drawChart;
    drawGrid();
    drawChart();
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--norm", type=float, choices=[0.0, 1.0], help="Run only one norm scenario.")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    input_path = base / "data" / "agents_initial.json"
    outputs = base / "outputs"
    requested_norm = args.norm
    scenarios = [requested_norm] if requested_norm is not None else [0.0, 1.0]
    summaries = []
    frames_by_norm = {}
    storage_by_norm = {}
    edges_by_norm = {}

    for norm in scenarios:
        scenario_dir = outputs / f"norm_{int(norm)}"
        summary, frames, storage_frames, share_edges = run_scenario(input_path, scenario_dir, norm)
        summaries.append(summary)
        frames_by_norm[f"norm_{int(norm)}"] = frames
        storage_by_norm[f"norm_{int(norm)}"] = storage_frames
        edges_by_norm[f"norm_{int(norm)}"] = share_edges

    if len(summaries) == 2:
        write_csv(outputs / "comparison.csv", summaries)
        write_report(outputs / "report.html", summaries, outputs)
        write_animation(outputs / "animation.html", summaries, frames_by_norm, edges_by_norm)
        write_agent_grid(outputs / "agent_grid.html", input_path, summaries, storage_by_norm)

    for summary in summaries:
        print(
            f"norm={summary['norm']}, "
            f"alive_percent={summary['alive_percent']}, "
            f"resilience={summary['resilience']}, "
            f"time={summary['simulation_start']} to {summary['simulation_end']}"
        )


if __name__ == "__main__":
    main()
