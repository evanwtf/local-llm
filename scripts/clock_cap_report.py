"""Report the #1319 clock-cap A/B: throughput, power and heat per arm.

Reads the run rows `clock_cap_ab.py` wrote, asks Grafana for each run's
window, and writes one Markdown report:

  per run   the arm, the three phases' throughput, the measured SM clock and
            GPU temperature and power on each node, the head's fan RPM, both
            outlet plugs and the inlet air
  per arm   the mean of its runs, the spread (min to max), and the mean as a
            share of the uncapped arm's

    uv run python scripts/clock_cap_report.py \\
        --runs ~/bench-logs/1319-clockcap/runs.jsonl --out report.md

Every number is absolute; shares of uncapped stand beside them, never alone.
The Grafana reads are the only untested part (the subprocess boundary in
`dgx_metrics`); everything that turns them into the report is tested.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dgx_metrics

import logs

logger = logging.getLogger(__name__)

UNCAPPED = "uncapped"
HEAD, WORKER = "dgx.internal", "dgx2.internal"

#: (column, reducer, metric, label selector). Built by `promql`, which pins
#: each query to the run's window with the `@` modifier.
PROM: tuple[tuple[str, str, str, str], ...] = (
    ("head_clock_avg", "avg", "DCGM_FI_DEV_SM_CLOCK", f'instance="{HEAD}:9400"'),
    ("head_clock_max", "max", "DCGM_FI_DEV_SM_CLOCK", f'instance="{HEAD}:9400"'),
    ("worker_clock_avg", "avg", "DCGM_FI_DEV_SM_CLOCK", f'instance="{WORKER}:9400"'),
    ("worker_clock_max", "max", "DCGM_FI_DEV_SM_CLOCK", f'instance="{WORKER}:9400"'),
    ("head_temp_max", "max", "DCGM_FI_DEV_GPU_TEMP", f'instance="{HEAD}:9400"'),
    ("worker_temp_max", "max", "DCGM_FI_DEV_GPU_TEMP", f'instance="{WORKER}:9400"'),
    ("head_gpu_w_avg", "avg", "DCGM_FI_DEV_POWER_USAGE", f'instance="{HEAD}:9400"'),
    ("worker_gpu_w_avg", "avg", "DCGM_FI_DEV_POWER_USAGE", f'instance="{WORKER}:9400"'),
    ("head_fan1_rpm", "avg", "node_hwmon_fan_rpm", f'instance="{HEAD}:9100",sensor="fan1"'),
    ("head_fan2_rpm", "avg", "node_hwmon_fan_rpm", f'instance="{HEAD}:9100",sensor="fan2"'),
)  # fmt: skip


def promql(reducer: str, metric: str, selector: str, w: int, t: int) -> str:
    return f"{reducer}_over_time({metric}{{{selector}}}[{w}s] @ {t})"


#: (column, Home Assistant entity, unit, Flux reducer, seconds to look back
#: before the run's start). Home Assistant writes the inlet sensor only when
#: its value changes, so a quiet room leaves a 25-min window empty: the inlet
#: is the last reading at or before the run's end, from up to 2 h before.
FLUX: tuple[tuple[str, str, str, str, int], ...] = (
    ("head_outlet_w_avg", dgx_metrics.OUTLET_ENTITY, "W", "mean", 0),
    ("head_outlet_w_max", dgx_metrics.OUTLET_ENTITY, "W", "max", 0),
    ("worker_outlet_w_avg", dgx_metrics.WORKER_OUTLET_ENTITY, "W", "mean", 0),
    ("worker_outlet_w_max", dgx_metrics.WORKER_OUTLET_ENTITY, "W", "max", 0),
    ("inlet_f", dgx_metrics.AMBIENT_ENTITY, "°F", "last", 7200),
)


def parse_utc(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.UTC
    )


def window(row: Mapping[str, Any]) -> tuple[int, int]:
    """(seconds, end as a Unix time) of one run."""
    start, end = parse_utc(row["start_utc"]), parse_utc(row["end_utc"])
    return int((end - start).total_seconds()), int(end.timestamp())


def absolute_flux(entity: str, unit: str, start: str, stop: str, agg: str) -> str:
    return (
        'from(bucket: "home_assistant/autogen")'
        f" |> range(start: {start}, stop: {stop})"
        f' |> filter(fn: (r) => r["_measurement"] == "{unit}"'
        f' and r["entity_id"] == "{entity}"'
        ' and r["_field"] == "value")'
        f" |> {agg}()"
    )


def sensors(
    row: Mapping[str, Any],
    prom: Callable[[str], float | None],
    flux: Callable[[str], float | None],
) -> dict[str, float | None]:
    w, t = window(row)
    out: dict[str, float | None] = {
        name: prom(promql(red, metric, sel, w, t)) for name, red, metric, sel in PROM
    }
    for name, entity, unit, agg, back in FLUX:
        start = parse_utc(row["start_utc"]) - datetime.timedelta(seconds=back)
        out[name] = flux(
            absolute_flux(
                entity, unit, start.strftime("%Y-%m-%dT%H:%M:%SZ"), row["end_utc"], agg
            )
        )
    head, worker = out["head_outlet_w_avg"], out["worker_outlet_w_avg"]
    pair = head + worker if head is not None and worker is not None else None
    out["pair_outlet_w_avg"] = pair
    out["pair_wh"] = pair * w / 3600 if pair is not None else None
    return out


def flatten(row: Mapping[str, Any], sens: Mapping[str, float | None]) -> dict[str, Any]:
    """One run as flat columns: throughput by phase, wall time, sensors."""
    flat: dict[str, Any] = {"round": row["round"], "arm": row["arm"]}
    total = 0.0
    for ph in row["phases"]:
        total += ph["wall_s"]
        if ph["phase"] == "prefill":
            flat["prefill_tps"] = ph["prompt_tps"]
        else:
            flat[f"{ph['phase']}_tps"] = ph["output_tps"]
    flat["wall_s"] = round(total, 1)
    flat.update(sens)
    return flat


def per_arm(
    runs: Sequence[Mapping[str, Any]], arms: Sequence[str], cols: Sequence[str]
) -> dict[str, dict[str, tuple[float, float, float] | None]]:
    """arm -> column -> (mean, min, max) over that arm's runs."""
    out: dict[str, dict[str, tuple[float, float, float] | None]] = {}
    for arm in arms:
        mine = [r for r in runs if r["arm"] == arm]
        out[arm] = {}
        for c in cols:
            vals = [float(r[c]) for r in mine if r.get(c) is not None]
            out[arm][c] = (
                (statistics.fmean(vals), min(vals), max(vals)) if vals else None
            )
    return out


def share(value: float, base: float | None) -> str:
    return f"{100 * value / base:.1f}%" if base else "n/a"


SUMMARY_COLS = (
    ("prefill_tps", "prefill tok/s", 0),
    ("c1_tps", "1-stream tok/s", 1),
    ("c2_tps", "2-stream tok/s", 1),
    ("wall_s", "run wall s", 0),
    ("head_clock_avg", "head SM MHz avg", 0),
    ("worker_clock_avg", "worker SM MHz avg", 0),
    ("head_temp_max", "head GPU °C max", 0),
    ("worker_temp_max", "worker GPU °C max", 0),
    ("pair_outlet_w_avg", "outlets W avg (pair)", 0),
    ("pair_wh", "outlets Wh per run", 0),
    ("head_gpu_w_avg", "head GPU W avg", 0),
    ("worker_gpu_w_avg", "worker GPU W avg", 0),
    ("head_fan2_rpm", "head fan2 RPM", 0),
    ("inlet_f", "inlet °F", 1),
)


def render(runs: Sequence[Mapping[str, Any]], arms: Sequence[str]) -> str:
    stats = per_arm(runs, arms, [c for c, _, _ in SUMMARY_COLS])
    base = stats.get(UNCAPPED, {})
    lines = [
        "| metric | " + " | ".join(arms) + " |",
        "|---|" + "---|" * len(arms),
    ]
    for col, label, nd in SUMMARY_COLS:
        cells = []
        for arm in arms:
            s = stats[arm][col]
            if s is None:
                cells.append("n/a")
                continue
            mean, lo, hi = s
            b = base.get(col)
            cell = f"{mean:,.{nd}f} ({lo:,.{nd}f}–{hi:,.{nd}f})"
            if arm != UNCAPPED and b is not None:
                cell += f", {share(mean, b[0])}"
            cells.append(cell)
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "Per run:", ""]
    run_cols = [c for c, _, _ in SUMMARY_COLS]
    lines.append("| round | arm | " + " | ".join(run_cols) + " |")
    lines.append("|---|---|" + "---|" * len(run_cols))
    for r in sorted(runs, key=lambda r: (r["round"], arms.index(r["arm"]))):
        vals = ["n/a" if r.get(c) is None else f"{float(r[c]):,.1f}" for c in run_cols]
        lines.append(f"| {r['round']} | {r['arm']} | " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def flux_value(gcx_json: str) -> float | None:
    """The first row's value from a gcx Influx table. `max()` and `last()`
    keep the column `value`; `mean()` names it `_value`."""
    try:
        table = json.loads(gcx_json)
    except ValueError:
        return None
    if not isinstance(table, dict):
        return None
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    col = "_value" if "_value" in columns else "value"
    if not rows or col not in columns:
        return None
    try:
        return float(rows[0][columns.index(col)])
    except (IndexError, TypeError, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--runs", type=pathlib.Path, required=True)
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--json-out", type=pathlib.Path)
    args = p.parse_args(argv)
    logs.configure()

    rows = [json.loads(line) for line in args.runs.read_text().splitlines() if line]
    arms: list[str] = []
    for r in rows:
        if r["arm"] not in arms:
            arms.append(r["arm"])
    if UNCAPPED in arms:
        arms.remove(UNCAPPED)
        arms.insert(0, UNCAPPED)

    def prom(q: str) -> float | None:
        return dgx_metrics.scalar(dgx_metrics.query(q))

    def flux(q: str) -> float | None:
        return flux_value(dgx_metrics.flux_query(q))

    runs = [flatten(r, sensors(r, prom, flux)) for r in rows]
    args.out.write_text(render(runs, arms))
    if args.json_out:
        args.json_out.write_text(json.dumps(runs, indent=1))
    logger.info("wrote %s (%d runs, arms %s)", args.out, len(runs), arms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
