"""Evidence pack for the #1319 clock-cap A/B: sensor time series and charts.

  export   read Grafana with gcx over the whole A/B and write, into --out:
             timeseries.csv  one row per 15 s step: SM clock, GPU temperature
                             and GPU power on each node, the head's two fans,
                             and the run, arm and phase the step falls in
             outlets.csv     both wall plugs at their own 5 s resolution
             inlet.csv       the inlet-air sensor (it records only on change)
             phases.csv      each run's three phase windows, start and end
  summary  rebuild every per-run figure from those committed CSVs alone
           (runs.csv): energy integrated from the raw plug readings, means and
           peaks from the 15 s series, the inlet at the run's end; and the
           report's tables from it (tables.md)
  charts   render the report's PNG charts from runs.csv and the series,
           including the curve: energy per run against the time the work
           took, one point per arm

    uv run python scripts/clock_cap_evidence.py export \\
        --runs benchmarks/clock-cap/1319/runs.jsonl --out docs/clock-cap-1319/data
    uv run python scripts/clock_cap_evidence.py summary \\
        --runs benchmarks/clock-cap/1319/runs.jsonl --data docs/clock-cap-1319/data
    uv run python scripts/clock_cap_evidence.py charts \\
        --data docs/clock-cap-1319/data --out docs/clock-cap-1319/charts

The arms come from the runs themselves (uncapped first, then the highest cap
down), so one script serves both the first A/B and the 100 MHz curve.

Every timestamp is written twice: UTC, and America/New_York with its offset.
The gcx calls are the only untested part; the parsing, the phase windows and
the grid that joins the series are tested.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import itertools
import json
import logging
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dgx_metrics

import logs

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
HEAD, WORKER = "dgx.internal", "dgx2.internal"
STEP_S = 15
PAD_S = 600  # idle before the first run and after the last, for a baseline
# gcx thins a range query to about 100 points, so a long window comes back at
# a 5-minute step. 1,500 s at 15 s is 101 points, the most that keeps the step.
PROM_CHUNK_S = 1500
# gcx refuses an Influx answer over 1,000 points; the plugs report every ~5 s.
FLUX_CHUNK_S = 3600
UNCAPPED = "uncapped"

#: (column, PromQL selector). Each is read as a range at STEP_S.
SERIES: tuple[tuple[str, str], ...] = (
    ("head_sm_clock_mhz", f'DCGM_FI_DEV_SM_CLOCK{{instance="{HEAD}:9400"}}'),
    ("worker_sm_clock_mhz", f'DCGM_FI_DEV_SM_CLOCK{{instance="{WORKER}:9400"}}'),
    ("head_gpu_temp_c", f'DCGM_FI_DEV_GPU_TEMP{{instance="{HEAD}:9400"}}'),
    ("worker_gpu_temp_c", f'DCGM_FI_DEV_GPU_TEMP{{instance="{WORKER}:9400"}}'),
    ("head_gpu_power_w", f'DCGM_FI_DEV_POWER_USAGE{{instance="{HEAD}:9400"}}'),
    ("worker_gpu_power_w", f'DCGM_FI_DEV_POWER_USAGE{{instance="{WORKER}:9400"}}'),
    ("head_fan1_rpm", f'node_hwmon_fan_rpm{{instance="{HEAD}:9100",sensor="fan1"}}'),
    ("head_fan2_rpm", f'node_hwmon_fan_rpm{{instance="{HEAD}:9100",sensor="fan2"}}'),
)  # fmt: skip

#: (file stem, Home Assistant entity, unit).
OUTLETS: tuple[tuple[str, str, str], ...] = (
    ("head_outlet_w", dgx_metrics.OUTLET_ENTITY, "W"),
    ("worker_outlet_w", dgx_metrics.WORKER_OUTLET_ENTITY, "W"),
)
INLET = ("inlet_f", dgx_metrics.AMBIENT_ENTITY, "°F")
INLET_LOOKBACK_S = 7200  # the sensor is sparse; take its last reading before


# ---------------------------------------------------------------- arms ----


def arm_order(arms: Iterable[str]) -> list[str]:
    """Uncapped first, then the caps from the highest clock down."""
    found = set(arms)
    caps = sorted((a for a in found if a != UNCAPPED), key=int, reverse=True)
    return ([UNCAPPED] if UNCAPPED in found else []) + caps


def arm_label(arm: str) -> str:
    return arm if arm == UNCAPPED else f"{int(arm):,} MHz"


# ---------------------------------------------------------------- time ----


def parse_utc(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.UTC
    )


def utc_text(t: datetime.datetime) -> str:
    return t.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ny_text(t: datetime.datetime) -> str:
    return t.astimezone(NY).strftime("%Y-%m-%dT%H:%M:%S%z")


# -------------------------------------------------------------- phases ----


def phase_windows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each run's phases as windows. The driver runs them back to back, so a
    phase starts where the one before it ended; the last ends at `end_utc`."""
    out: list[dict[str, Any]] = []
    for row in rows:
        t = parse_utc(row["start_utc"])
        end = parse_utc(row["end_utc"])
        phases = row["phases"]
        for i, ph in enumerate(phases):
            stop = (
                end
                if i == len(phases) - 1
                else t + datetime.timedelta(seconds=ph["wall_s"])
            )
            out.append(
                {
                    "run_id": row["run_id"],
                    "round": row["round"],
                    "arm": row["arm"],
                    "phase": ph["phase"],
                    "start": t,
                    "end": stop,
                    "wall_s": ph["wall_s"],
                }
            )
            t = stop
    return out


def label_at(
    t: datetime.datetime, windows: Sequence[Mapping[str, Any]]
) -> tuple[str, str, str]:
    """(run_id, arm, phase) for the window holding t, or ("", "", "between")."""
    for w in windows:
        if w["start"] <= t < w["end"]:
            return w["run_id"], w["arm"], w["phase"]
    return "", "", "between"


# ------------------------------------------------------------- parsing ----


def matrix_points(gcx_json: str) -> list[tuple[int, float]]:
    """(unix seconds, value) from a gcx range query; empty when it failed."""
    try:
        result = json.loads(gcx_json)["data"]["result"]
    except (ValueError, KeyError, TypeError):
        return []
    if not result:
        return []
    out: list[tuple[int, float]] = []
    for ts, value in result[0].get("values", []):
        try:
            out.append((int(ts), float(value)))
        except (TypeError, ValueError):
            continue
    return out


def flux_points(gcx_json: str) -> list[tuple[datetime.datetime, float]]:
    """(UTC time, value) rows from a gcx Influx table of Time and Value."""
    try:
        table = json.loads(gcx_json)
        columns = [c.lower() for c in table["columns"]]
        ti, vi = columns.index("time"), columns.index("value")
    except (ValueError, KeyError, TypeError):
        return []
    out: list[tuple[datetime.datetime, float]] = []
    for row in table.get("rows") or []:
        try:
            out.append((parse_utc(row[ti]), float(row[vi])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def chunks(start: int, end: int, size: int) -> list[tuple[int, int]]:
    """[start, end] cut into back-to-back spans of at most `size` seconds."""
    out: list[tuple[int, int]] = []
    s = start
    while s < end:
        out.append((s, min(s + size, end)))
        s += size
    return out


def grid(
    series: Mapping[str, Sequence[tuple[int, float]]], start: int, end: int, step: int
) -> list[dict[str, Any]]:
    """One row per step from start to end; a series missing at a step is None."""
    lookup = {name: dict(points) for name, points in series.items()}
    rows: list[dict[str, Any]] = []
    for ts in range(start, end + 1, step):
        row: dict[str, Any] = {"ts": ts}
        for name, values in lookup.items():
            row[name] = values.get(ts)
        rows.append(row)
    return rows


# -------------------------------------------------------------- export ----


def _gcx(args: list[str]) -> str:
    """Untested subprocess boundary, like dgx_metrics.query."""
    env = dict(os.environ)
    try:
        if dgx_metrics.TOKEN_PATH.exists():
            env["GRAFANA_TOKEN"] = dgx_metrics.TOKEN_PATH.read_text().strip()
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["gcx", *args, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout


def range_query(selector: str, start: int, end: int, step: int) -> str:
    return _gcx(
        [
            "metrics",
            "query",
            "-d",
            dgx_metrics.DATASOURCE,
            selector,
            "--from",
            str(start),
            "--to",
            str(end),
            "--step",
            f"{step}s",
        ]
    )


def raw_flux(entity: str, unit: str, start: str, stop: str) -> str:
    return (
        'from(bucket: "home_assistant/autogen")'
        f" |> range(start: {start}, stop: {stop})"
        f' |> filter(fn: (r) => r["_measurement"] == "{unit}"'
        f' and r["entity_id"] == "{entity}"'
        ' and r["_field"] == "value")'
        ' |> keep(columns: ["_time", "_value"])'
    )


def export(
    rows: Sequence[Mapping[str, Any]],
    out: pathlib.Path,
    prom: Callable[[str, int, int, int], str] = range_query,
    flux: Callable[[str], str] = lambda q: _gcx(
        ["datasources", "query", dgx_metrics.INFLUX_DATASOURCE, q]
    ),
) -> dict[str, int]:
    """Write the four CSVs; return the row count of each, for the log."""
    out.mkdir(parents=True, exist_ok=True)
    windows = phase_windows(rows)
    first = min(parse_utc(r["start_utc"]) for r in rows)
    last = max(parse_utc(r["end_utc"]) for r in rows)
    start = int(first.timestamp()) - PAD_S
    end = int(last.timestamp()) + PAD_S
    start -= start % STEP_S
    counts: dict[str, int] = {}

    series = {
        name: [
            pt
            for a, b in chunks(start, end, PROM_CHUNK_S)
            for pt in matrix_points(prom(sel, a, b, STEP_S))
        ]
        for name, sel in SERIES
    }
    for name, points in series.items():
        if not points:
            raise SystemExit(f"no data for {name}: refusing to write a partial file")
    table = grid(series, start, end, STEP_S)
    names = [n for n, _ in SERIES]
    with (out / "timeseries.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", "time_new_york", "run_id", "arm", "phase", *names])
        for r in table:
            t = datetime.datetime.fromtimestamp(r["ts"], datetime.UTC)
            run_id, arm, phase = label_at(t, windows)
            w.writerow(
                [utc_text(t), ny_text(t), run_id, arm, phase]
                + ["" if r[n] is None else r[n] for n in names]
            )
    counts["timeseries.csv"] = len(table)

    t0 = datetime.datetime.fromtimestamp(start, datetime.UTC)

    def flux_range(
        entity: str, unit: str, a: int, b: int
    ) -> list[tuple[datetime.datetime, float]]:
        seen: dict[datetime.datetime, float] = {}
        for x, y in chunks(a, b, FLUX_CHUNK_S):
            xs = datetime.datetime.fromtimestamp(x, datetime.UTC)
            ys = datetime.datetime.fromtimestamp(y, datetime.UTC)
            for t, v in flux_points(
                flux(raw_flux(entity, unit, utc_text(xs), utc_text(ys)))
            ):
                seen[t] = v
        return sorted(seen.items())

    outlet_points = {
        stem: flux_range(entity, unit, start, end) for stem, entity, unit in OUTLETS
    }
    for stem, plug in outlet_points.items():
        if not plug:
            raise SystemExit(f"no data for {stem}: refusing to write a partial file")
    with (out / "outlets.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["time_utc", "time_new_york", "run_id", "arm", "phase", "outlet", "watts"]
        )
        merged = sorted(
            (t, stem, v) for stem, points in outlet_points.items() for t, v in points
        )
        for t, stem, v in merged:
            run_id, arm, phase = label_at(t, windows)
            w.writerow(
                [
                    utc_text(t),
                    ny_text(t),
                    run_id,
                    arm,
                    phase,
                    stem.removesuffix("_outlet_w"),
                    v,
                ]
            )
    counts["outlets.csv"] = len(merged)

    stem, entity, unit = INLET
    t_back = t0 - datetime.timedelta(seconds=INLET_LOOKBACK_S)
    inlet = flux_range(entity, unit, int(t_back.timestamp()), end)
    with (out / "inlet.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", "time_new_york", "inlet_f"])
        for t, v in inlet:
            w.writerow([utc_text(t), ny_text(t), v])
    counts["inlet.csv"] = len(inlet)

    with (out / "phases.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["run_id", "round", "arm", "phase", "start_utc", "end_utc",
             "start_new_york", "end_new_york", "wall_s"]
        )  # fmt: skip
        for p in windows:
            w.writerow(
                [p["run_id"], p["round"], p["arm"], p["phase"], utc_text(p["start"]),
                 utc_text(p["end"]), ny_text(p["start"]), ny_text(p["end"]), p["wall_s"]]
            )  # fmt: skip
    counts["phases.csv"] = len(windows)
    return counts


# ------------------------------------------------------------- summary ----


def energy_wh(
    points: Sequence[tuple[datetime.datetime, float]],
    start: datetime.datetime,
    end: datetime.datetime,
) -> float | None:
    """Watt-hours from start to end by the trapezoid rule over the readings
    inside the window, held flat to its edges. None with fewer than two."""
    inside = [(t, v) for t, v in points if start <= t <= end]
    if len(inside) < 2:
        return None
    pts = [(start, inside[0][1]), *inside, (end, inside[-1][1])]
    joules = sum(
        (b[0] - a[0]).total_seconds() * (a[1] + b[1]) / 2
        for a, b in itertools.pairwise(pts)
    )
    return joules / 3600


def held_value(
    points: Sequence[tuple[datetime.datetime, float]], t: datetime.datetime
) -> float | None:
    """The last reading at or before t: a sensor that records only on change
    holds its value until the next record."""
    prior = [v for when, v in points if when <= t]
    return prior[-1] if prior else None


def mean(vals: Sequence[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


SUMMARY_COLUMNS = (
    "run_id", "round", "arm", "start_utc", "end_utc", "start_new_york",
    "end_new_york", "start_head_gpu_c", "start_worker_gpu_c", "wall_s",
    "prefill_s", "c1_s", "c2_s", "prefill_tps", "c1_tps", "c2_tps",
    "head_sm_clock_avg", "worker_sm_clock_avg", "head_sm_clock_max",
    "worker_sm_clock_max", "head_gpu_temp_max", "worker_gpu_temp_max",
    "head_gpu_w_avg", "worker_gpu_w_avg", "head_fan1_rpm_avg",
    "head_fan2_rpm_avg", "head_outlet_w_avg", "worker_outlet_w_avg",
    "pair_outlet_w_avg", "head_outlet_w_max", "worker_outlet_w_max",
    "head_wh", "worker_wh", "pair_wh", "inlet_f",
)  # fmt: skip


TEXT_COLUMNS = frozenset(
    ("run_id", "round", "arm", "start_utc", "end_utc", "start_new_york", "end_new_york")
)


def summarize(
    rows: Sequence[Mapping[str, Any]],
    series: Sequence[Mapping[str, str]],
    outlets: Mapping[str, Sequence[tuple[datetime.datetime, float]]],
    inlet: Sequence[tuple[datetime.datetime, float]],
) -> list[dict[str, Any]]:
    """One row per run, every figure from the raw files."""
    out: list[dict[str, Any]] = []
    for row in rows:
        start, end = parse_utc(row["start_utc"]), parse_utc(row["end_utc"])
        inside = [r for r in series if start <= parse_utc(r["time_utc"]) <= end]

        def col(name: str, rows_: Sequence[Mapping[str, str]] = inside) -> list[float]:
            return [float(r[name]) for r in rows_ if r[name] != ""]

        ph = {p["phase"]: p for p in row["phases"]}
        rec: dict[str, Any] = {
            "run_id": row["run_id"],
            "round": row["round"],
            "arm": row["arm"],
            "start_utc": row["start_utc"],
            "end_utc": row["end_utc"],
            "start_new_york": ny_text(start),
            "end_new_york": ny_text(end),
            "start_head_gpu_c": row["start_gpu_temps_c"][0],
            "start_worker_gpu_c": row["start_gpu_temps_c"][1],
            "wall_s": round((end - start).total_seconds(), 1),
            "prefill_s": ph["prefill"]["wall_s"],
            "c1_s": ph["c1"]["wall_s"],
            "c2_s": ph["c2"]["wall_s"],
            "prefill_tps": ph["prefill"]["prompt_tps"],
            "c1_tps": ph["c1"]["output_tps"],
            "c2_tps": ph["c2"]["output_tps"],
        }
        for node in ("head", "worker"):
            clock = col(f"{node}_sm_clock_mhz")
            rec[f"{node}_sm_clock_avg"] = mean(clock)
            rec[f"{node}_sm_clock_max"] = max(clock, default=None)
            rec[f"{node}_gpu_temp_max"] = max(col(f"{node}_gpu_temp_c"), default=None)
            rec[f"{node}_gpu_w_avg"] = mean(col(f"{node}_gpu_power_w"))
            plug = [v for t, v in outlets[node] if start <= t <= end]
            rec[f"{node}_outlet_w_avg"] = mean(plug)
            rec[f"{node}_outlet_w_max"] = max(plug, default=None)
            rec[f"{node}_wh"] = energy_wh(outlets[node], start, end)
        rec["head_fan1_rpm_avg"] = mean(col("head_fan1_rpm"))
        rec["head_fan2_rpm_avg"] = mean(col("head_fan2_rpm"))
        h, w = rec["head_outlet_w_avg"], rec["worker_outlet_w_avg"]
        rec["pair_outlet_w_avg"] = h + w if h is not None and w is not None else None
        hw, ww = rec["head_wh"], rec["worker_wh"]
        rec["pair_wh"] = hw + ww if hw is not None and ww is not None else None
        rec["inlet_f"] = held_value(inlet, end)
        out.append(rec)
    return out


def load_points(path: pathlib.Path, value: str, key: str | None = None) -> Any:
    """CSV readings as (UTC time, value), grouped by `key` when given."""
    grouped: dict[str, list[tuple[datetime.datetime, float]]] = {}
    for r in read_csv(path):
        grouped.setdefault(r[key] if key else "", []).append(
            (parse_utc(r["time_utc"]), float(r[value]))
        )
    for pts in grouped.values():
        pts.sort()
    return grouped if key else grouped.get("", [])


#: (column, label, decimals) for the per-arm table in tables.md.
ARM_TABLE = (
    ("wall_s", "run wall time (s)", 0),
    ("pair_wh", "energy per run, both outlets (Wh)", 1),
    ("pair_outlet_w_avg", "average power, both outlets (W)", 0),
    ("head_outlet_w_avg", "head outlet average (W)", 0),
    ("worker_outlet_w_avg", "worker outlet average (W)", 0),
    ("head_outlet_w_max", "head outlet peak (W)", 0),
    ("worker_outlet_w_max", "worker outlet peak (W)", 0),
    ("prefill_tps", "prefill (prompt tok/s)", 0),
    ("c1_tps", "1 stream (output tok/s)", 1),
    ("c2_tps", "2 streams (output tok/s)", 1),
    ("head_sm_clock_avg", "head SM clock average (MHz)", 0),
    ("worker_sm_clock_avg", "worker SM clock average (MHz)", 0),
    ("head_gpu_temp_max", "head GPU peak (°C)", 0),
    ("worker_gpu_temp_max", "worker GPU peak (°C)", 0),
    ("head_gpu_w_avg", "head GPU power average (W)", 1),
    ("worker_gpu_w_avg", "worker GPU power average (W)", 1),
    ("head_fan1_rpm_avg", "head fan1 average (RPM)", 0),
    ("head_fan2_rpm_avg", "head fan2 average (RPM)", 0),
    ("inlet_f", "inlet air at run end (°F)", 1),
)

#: (column, label, decimals) for the per-run table in tables.md.
RUN_TABLE = (
    ("start_new_york", "start (New York)", None),
    ("end_new_york", "end", None),
    ("wall_s", "wall s", 0),
    ("prefill_tps", "prefill tok/s", 0),
    ("c1_tps", "1-stream tok/s", 1),
    ("c2_tps", "2-stream tok/s", 1),
    ("head_sm_clock_avg", "SM MHz head", 0),
    ("worker_sm_clock_avg", "SM MHz worker", 0),
    ("head_gpu_temp_max", "GPU °C max head", 0),
    ("worker_gpu_temp_max", "GPU °C max worker", 0),
    ("head_gpu_w_avg", "GPU W head", 1),
    ("worker_gpu_w_avg", "GPU W worker", 1),
    ("head_fan2_rpm_avg", "fan2 RPM", 0),
    ("pair_outlet_w_avg", "outlets W", 1),
    ("pair_wh", "Wh", 1),
    ("inlet_f", "inlet °F", 1),
)


def arm_means(
    recs: Sequence[Mapping[str, Any]], arms: Sequence[str], col: str
) -> dict[str, float]:
    """arm -> the mean of col over its runs; an arm with no value is left out."""
    out: dict[str, float] = {}
    for arm in arms:
        vals = [
            float(r[col]) for r in recs if r["arm"] == arm and r[col] not in (None, "")
        ]
        if vals:
            out[arm] = sum(vals) / len(vals)
    return out


def curve_steps(
    recs: Sequence[Mapping[str, Any]], arms: Sequence[str]
) -> list[dict[str, Any]]:
    """Each step down the clock, between neighboring arms: the seconds the
    same work gained, the energy it saved, and the energy saved per second
    added. A step that added no time has no rate (None)."""
    wh, wall = arm_means(recs, arms, "pair_wh"), arm_means(recs, arms, "wall_s")
    have = [a for a in arms if a in wh and a in wall]
    out: list[dict[str, Any]] = []
    for a, b in itertools.pairwise(have):
        added = wall[b] - wall[a]
        saved = wh[a] - wh[b]
        out.append(
            {"from": a, "to": b, "added_s": added, "saved_wh": saved,
             "wh_per_s": saved / added if added > 0 else None}
        )  # fmt: skip
    return out


def render_tables(
    recs: Sequence[Mapping[str, Any]], arms: Sequence[str] | None = None
) -> str:
    """Per-arm mean (min–max) with the share of uncapped, each step down the
    clock, then every run."""
    order = arm_order(r["arm"] for r in recs) if arms is None else list(arms)

    def f(v: Any, nd: int | None) -> str:
        if v is None or v == "":
            return "n/a"
        return str(v) if nd is None else f"{float(v):,.{nd}f}"

    lines = [
        "| measure | " + " | ".join(order) + " |",
        "|---|" + "---|" * len(order),
    ]
    for col, label, nd in ARM_TABLE:
        base: float | None = None
        cells = []
        for arm in order:
            vals = [
                float(r[col])
                for r in recs
                if r["arm"] == arm and r[col] not in (None, "")
            ]
            if not vals:
                cells.append("n/a")
                continue
            m = sum(vals) / len(vals)
            cell = f"{m:,.{nd}f} ({min(vals):,.{nd}f}–{max(vals):,.{nd}f})"
            if arm == order[0]:
                base = m
            elif base:
                cell += f", {100 * m / base:.1f}%"
            cells.append(cell)
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "| step | time added (s) | energy saved per run (Wh) | Wh saved per added second |",
        "|---|---|---|---|",
    ]
    for st in curve_steps(recs, order):
        rate = "n/a" if st["wh_per_s"] is None else f"{st['wh_per_s']:.3f}"
        lines.append(
            f"| {arm_label(st['from'])} → {arm_label(st['to'])} | {st['added_s']:,.0f}"
            f" | {st['saved_wh']:,.1f} | {rate} |"
        )
    lines += ["", "| round | arm | " + " | ".join(lb for _, lb, _ in RUN_TABLE) + " |"]
    lines.append("|---|---|" + "---|" * len(RUN_TABLE))
    for r in recs:
        row_cells = [f(r[c], nd) for c, _, nd in RUN_TABLE]
        lines.append(f"| {r['round']} | {r['arm']} | " + " | ".join(row_cells) + " |")
    return "\n".join(lines) + "\n"


def write_summary(rows: Sequence[Mapping[str, Any]], data: pathlib.Path) -> int:
    recs = summarize(
        rows,
        read_csv(data / "timeseries.csv"),
        load_points(data / "outlets.csv", "watts", "outlet"),
        load_points(data / "inlet.csv", "inlet_f"),
    )
    with (data / "runs.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_COLUMNS)
        w.writeheader()
        for rec in recs:
            w.writerow({k: "" if rec[k] is None else rec[k] for k in SUMMARY_COLUMNS})
    (data / "tables.md").write_text(render_tables(recs))
    return len(recs)


# -------------------------------------------------------------- charts ----

# Reference palette (dataviz skill), validated 2026-10-31: the node pair
# passes CVD and contrast. The arms are an ordinal one-hue ramp, darker =
# higher clock; adjacent steps sit close (OKLab ΔE about 8), so every arm is
# also labeled by name, never by color alone.
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
NODE = {"head": "#2a78d6", "worker": "#eb6834"}
ARM_RAMP = ("#104281", "#1d5aa8", "#2f74cc", "#4d8fe0", "#7aaeea", "#a9cbf2")
PHASE_COLOR = {"prefill_tps": "#2a78d6", "c1_tps": "#eb6834", "c2_tps": "#1f9e74"}


def arm_colors(arms: Sequence[str]) -> dict[str, str]:
    """Each arm's ramp step, in clock order. More arms than steps is refused:
    a generated seventh shade would not be told apart."""
    if len(arms) > len(ARM_RAMP):
        raise SystemExit(f"{len(arms)} arms; the ramp has {len(ARM_RAMP)} steps")
    return dict(zip(arm_order(arms), ARM_RAMP, strict=False))


def read_csv(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def num(s: str) -> float | None:
    return float(s) if s not in ("", None) else None


def charts(
    data: pathlib.Path, out: pathlib.Path, title: str = "#1319 clock-cap A/B"
) -> list[pathlib.Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK2,
            "axes.titlecolor": INK,
            "axes.titleweight": "bold",
            "axes.titlesize": 11,
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "font.size": 9,
            "legend.frameon": False,
            "lines.linewidth": 1.2,
        }
    )
    out.mkdir(parents=True, exist_ok=True)
    made: list[pathlib.Path] = []
    ts = read_csv(data / "timeseries.csv")
    outlets = read_csv(data / "outlets.csv")
    phases = read_csv(data / "phases.csv")
    runs: list[dict[str, Any]] = [
        {
            k: (v if v == "" or not k[0].isalpha() or k in TEXT_COLUMNS else float(v))
            for k, v in r.items()
        }
        for r in read_csv(data / "runs.csv")
    ]

    arms = arm_order(r["arm"] for r in runs)
    ARM_COLOR = arm_colors(arms)
    ARM_LABEL = {a: arm_label(a) for a in arms}
    rounds = sorted({p["round"] for p in phases}, key=int)
    # The round whose runs show one per arm: the second when it is complete.
    full = [
        rd for rd in rounds
        if {p["arm"] for p in phases if p["round"] == rd} >= set(arms)
    ]  # fmt: skip
    shown_round = "2" if "2" in full else full[0]
    n_per_arm = sorted({sum(r["arm"] == a for r in runs) for a in arms})
    runs_text = (
        f"{n_per_arm[0]} runs" if len(n_per_arm) == 1
        else f"{n_per_arm[0]}-{n_per_arm[-1]} runs"
    )  # fmt: skip

    def t_of(s: str) -> datetime.datetime:
        return parse_utc(s).astimezone(NY)

    run_spans: dict[str, tuple[datetime.datetime, datetime.datetime, str]] = {}
    for p in phases:
        s, e = t_of(p["start_utc"]), t_of(p["end_utc"])
        old = run_spans.get(p["run_id"])
        run_spans[p["run_id"]] = (
            min(s, old[0]) if old else s,
            max(e, old[1]) if old else e,
            p["arm"],
        )

    def shade(ax: Any, label: bool = False) -> None:
        for s, e, arm in run_spans.values():
            ax.axvspan(s, e, color=ARM_COLOR[arm], alpha=0.14, lw=0)
            if label:
                ax.text(s + (e - s) / 2, 1.02, ARM_LABEL[arm].replace(" MHz", ""),
                        transform=ax.get_xaxis_transform(), ha="center", va="bottom",
                        fontsize=7, color=INK2)  # fmt: skip

    def save(fig: Any, name: str) -> None:
        path = out / name
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        made.append(path)

    times = [t_of(r["time_utc"]) for r in ts]

    # 1. The whole A/B: one panel per measure, both nodes, runs shaded by arm.
    panels = (
        ("SM clock (MHz)", "sm_clock_mhz"),
        ("GPU temperature (°C)", "gpu_temp_c"),
        ("GPU power (W)", "gpu_power_w"),
    )
    fig, axes = plt.subplots(5, 1, figsize=(12, 13), sharex=True)
    for ax, (panel_title, key) in zip(axes, panels, strict=False):
        for node, color in NODE.items():
            ax.plot(
                times, [num(r[f"{node}_{key}"]) for r in ts], color=color, label=node
            )
        ax.set_title(panel_title, pad=16 if ax is axes[0] else 6)
        shade(ax, label=ax is axes[0])
    axes[0].legend(loc="lower right", ncol=2)
    ax = axes[3]
    for node, color in NODE.items():
        pts = [
            (t_of(r["time_utc"]), float(r["watts"]))
            for r in outlets
            if r["outlet"] == node
        ]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, label=node)
    ax.set_title("Wall outlet power, per node (W)")
    shade(ax)
    ax = axes[4]
    for fan, color in (("fan1", NODE["head"]), ("fan2", NODE["worker"])):
        ax.plot(
            times,
            [num(r[f"head_{fan}_rpm"]) for r in ts],
            color=color,
            label=f"head {fan}",
        )
    ax.set_title("Head node fans (RPM); the worker reports no fan speed")
    ax.legend(loc="lower right", ncol=2)
    shade(ax)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=NY))
    days = sorted({f"{t:%Y-%m-%d}" for t in (times[0], times[-1])})
    axes[-1].set_xlabel(
        f"New York time, {' to '.join(days)}; shaded bands are runs, darker = higher clock"
    )
    fig.suptitle(f"{title}: every sensor, whole test", x=0.01, ha="left",
                 fontsize=13, fontweight="bold", color=INK)  # fmt: skip
    fig.tight_layout()
    save(fig, "timeline.png")

    # 2. One run per arm, aligned at its start: pair outlet power.
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for arm in arms:
        rid = next(
            p["run_id"] for p in phases if p["arm"] == arm and p["round"] == shown_round
        )
        s, e, _ = run_spans[rid]
        by_plug: dict[str, list[tuple[float, float]]] = {"head": [], "worker": []}
        for r in outlets:
            t = t_of(r["time_utc"])
            if s <= t <= e:
                by_plug[r["outlet"]].append(
                    ((t - s).total_seconds() / 60, float(r["watts"]))
                )
        # The plugs report on their own clocks; pair each head reading with
        # the worker's latest reading at or before it.
        wk = by_plug["worker"]
        pair = []
        for m, v in by_plug["head"]:
            prior = [w for w in wk if w[0] <= m]
            if prior:
                pair.append((m, v + prior[-1][1]))
        ax.plot(
            [p[0] for p in pair],
            [p[1] for p in pair],
            color=ARM_COLOR[arm],
            label=ARM_LABEL[arm],
        )
    first_run = next(
        p for p in phases if p["round"] == shown_round and p["arm"] == arms[0]
    )
    ends = [p for p in phases if p["run_id"] == first_run["run_id"]]
    t_start = t_of(ends[0]["start_utc"])
    names = {"prefill": "prefill", "c1": "1 stream", "c2": "2 streams"}
    for p in ends:
        a = (t_of(p["start_utc"]) - t_start).total_seconds() / 60
        b = (t_of(p["end_utc"]) - t_start).total_seconds() / 60
        if p is not ends[-1]:
            ax.axvline(b, color=AXIS, lw=0.8, ls="--")
        ax.text((a + b) / 2, 0.03, names[p["phase"]], ha="center",
                transform=ax.get_xaxis_transform(), color=INK2)  # fmt: skip
    ax.set_title(f"Both outlets together during one run per arm (round {shown_round})")
    ax.set_xlabel(
        f"minutes since the run started (dashed: phase ends in the {arms[0]} run)"
    )
    ax.set_ylabel("watts, head + worker")
    ax.legend(loc="center right", ncol=2)
    fig.tight_layout()
    save(fig, "run_power_by_arm.png")

    # 3-5. Per-arm summaries: a bar for the mean, a dot for each run.
    def arm_panel(ax: Any, key: str, title: str, fmt: str) -> None:
        for i, arm in enumerate(arms):
            vals = [float(r[key]) for r in runs if r["arm"] == arm and r.get(key) != ""]
            mean = sum(vals) / len(vals)
            ax.bar(
                i, mean, width=0.6, color=ARM_COLOR[arm], edgecolor=SURFACE, linewidth=2
            )
            ax.scatter([i] * len(vals), vals, s=22, color=INK, zorder=3)
            # Above the bar and its highest run, so the label never sits on a dot.
            ax.annotate(format(mean, fmt), (i, max([mean, *vals])), xytext=(0, 5),
                        textcoords="offset points", ha="center", va="bottom",
                        fontsize=8, color=INK)  # fmt: skip
        ax.set_xticks(range(len(arms)), [ARM_LABEL[a] for a in arms])
        ax.set_title(title)
        ax.grid(axis="x", visible=False)

    def arm_figure(
        name: str, specs: Sequence[tuple[str, str, str]], title: str, zero: bool = True
    ) -> None:
        width = max(4.2, 0.95 * len(arms) + 0.6)
        fig, axes = plt.subplots(1, len(specs), figsize=(width * len(specs), 4))
        for ax, (key, sub, fmt) in zip(axes, specs, strict=True):
            arm_panel(ax, key, sub, fmt)
            if not zero:
                vals = [float(r[key]) for r in runs if r.get(key) != ""]
                lo, hi = min(vals), max(vals)
                ax.set_ylim(lo - (hi - lo) * 0.8, hi + (hi - lo) * 0.6)
            else:
                ax.set_ylim(top=ax.get_ylim()[1] * 1.06)
        fig.suptitle(
            title, x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK
        )
        fig.tight_layout()
        save(fig, name)

    arm_figure(
        "arms_energy_time.png",
        (("pair_wh", "Energy per run, both outlets (Wh)", ",.0f"),
         ("pair_outlet_w_avg", "Average power, both outlets (W)", ",.0f"),
         ("wall_s", "Time for the same work (s)", ",.0f")),
        f"Same work at each clock cap: bars are the mean of {runs_text}, dots are runs",
    )  # fmt: skip
    arm_figure(
        "arms_throughput.png",
        (("prefill_tps", "Prefill (prompt tok/s)", ",.0f"),
         ("c1_tps", "1 stream (output tok/s)", ".1f"),
         ("c2_tps", "2 streams (output tok/s)", ".1f")),
        "Throughput by phase (y-axes start near the data, not at zero)",
        zero=False,
    )  # fmt: skip
    arm_figure(
        "arms_thermal.png",
        (("head_gpu_temp_max", "Head GPU peak (°C)", ".0f"),
         ("worker_gpu_temp_max", "Worker GPU peak (°C)", ".0f"),
         ("head_fan2_rpm_avg", "Head fan2 average (RPM)", ",.0f")),
        "Heat and fans by clock cap",
    )  # fmt: skip

    # 6. The curve: what each step down the clock buys and costs.
    fig, (left, right) = plt.subplots(1, 2, figsize=(13, 5))

    def means(key: str) -> dict[str, float]:
        return arm_means(runs, arms, key)

    wall, wh = means("wall_s"), means("pair_wh")
    on_curve = [arm for arm in arms if arm in wall and arm in wh]
    left.plot([wall[arm] for arm in on_curve], [wh[arm] for arm in on_curve],
              color=AXIS, lw=1.2, zorder=1)  # fmt: skip
    for i, arm in enumerate(on_curve):
        mine = [r for r in runs if r["arm"] == arm and r["pair_wh"] != ""]
        left.scatter([float(r["wall_s"]) for r in mine],
                     [float(r["pair_wh"]) for r in mine],
                     s=14, color=ARM_COLOR[arm], alpha=0.55, lw=0, zorder=2)  # fmt: skip
        left.scatter([wall[arm]], [wh[arm]], s=70, color=ARM_COLOR[arm],
                     edgecolor=SURFACE, linewidth=2, zorder=3)  # fmt: skip
        # Name only, alternately above and below: neighbors sit ~30 s apart.
        # The values are in tables.md.
        below = i % 2 == 0 and i > 0
        left.annotate(ARM_LABEL[arm], (wall[arm], wh[arm]),
                      xytext=(8 if i == 0 else 0, -12 if below else 10),
                      textcoords="offset points", ha="left" if i == 0 else "center",
                      va="top" if below else "bottom", fontsize=8, color=INK)  # fmt: skip
    left.set_xlabel("time for the same work (s)")
    left.set_ylabel("energy per run, both outlets (Wh)")
    left.set_title("Energy against time: big dots are arm means, small dots runs")
    xs = [wall[arm] for arm in on_curve]
    ys = [wh[arm] for arm in on_curve]
    left.set_xlim(min(xs) - 20, max(xs) + 30)
    left.set_ylim(min(ys) - 4, max(ys) + 4)

    clock = means("head_sm_clock_avg")
    base = {k: means(k).get(arms[0]) for k in PHASE_COLOR}
    names = {"prefill_tps": "prefill", "c1_tps": "1 stream", "c2_tps": "2 streams"}
    for key, color in PHASE_COLOR.items():
        rate, ref = means(key), base[key]
        if not ref:
            continue
        line_pts = [(clock[arm], 100 * rate[arm] / ref) for arm in arms
                    if arm in clock and arm in rate]  # fmt: skip
        right.plot([p[0] for p in line_pts], [p[1] for p in line_pts], color=color, lw=2,
                   marker="o", ms=6, markeredgecolor=SURFACE, label=names[key])  # fmt: skip
        right.annotate(names[key], line_pts[-1], xytext=(8, 0), textcoords="offset points",
                       ha="left", va="center", fontsize=8, color=INK2)  # fmt: skip
    for arm in arms:
        if arm in clock:
            right.annotate(ARM_LABEL[arm].replace(" MHz", ""), (clock[arm], 1.0),
                           xycoords=("data", "axes fraction"), xytext=(0, -2),
                           textcoords="offset points", ha="center", va="top",
                           fontsize=7, color=MUTED)  # fmt: skip
    right.axhline(100, color=AXIS, lw=0.8)
    right.set_xlabel(
        "measured head SM clock, mean over the run (MHz); arm names on top"
    )
    right.set_ylabel(f"% of the {arms[0]} arm's mean")
    right.set_title("Throughput by phase, as arm share of uncapped", pad=14)
    lo, hi = right.get_xlim()
    right.set_xlim(hi, lo - (hi - lo) * 0.12)  # inverted, with room for the labels
    fig.suptitle(f"{title}: what each step down the clock costs and saves",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)  # fmt: skip
    fig.tight_layout()
    save(fig, "curve.png")
    return made


# ---------------------------------------------------------------- main ----


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--runs", type=pathlib.Path, required=True)
    e.add_argument("--out", type=pathlib.Path, required=True)
    m = sub.add_parser("summary")
    m.add_argument("--runs", type=pathlib.Path, required=True)
    m.add_argument("--data", type=pathlib.Path, required=True)
    c = sub.add_parser("charts")
    c.add_argument("--data", type=pathlib.Path, required=True)
    c.add_argument("--out", type=pathlib.Path, required=True)
    c.add_argument("--title", default="#1319 clock-cap A/B")
    args = p.parse_args(argv)
    logs.configure()
    if args.cmd in ("export", "summary"):
        rows = [json.loads(line) for line in args.runs.read_text().splitlines() if line]
    if args.cmd == "export":
        for name, n in export(rows, args.out).items():
            logger.info("wrote %s: %d rows", args.out / name, n)
    elif args.cmd == "summary":
        n = write_summary(rows, args.data)
        logger.info("wrote %s: %d runs", args.data / "runs.csv", n)
    else:
        for path in charts(args.data, args.out, args.title):
            logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
