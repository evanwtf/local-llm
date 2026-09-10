#!/usr/bin/env python3
"""One tidy CSV for a fan A/B run: every datapoint, on every row. #276

`fan_ab_report.py` answers the question. This produces the table the answer
came from, so a later reader -- or a write-up -- can recompute it without this
repo's code, and can ask questions nobody thought of tonight.

## One row per (phase, rep, ctx frontier)

A row carries its own throughput, its own thermal context, its own fan state
and its own provenance. Nothing here needs another row or the manifest to be
read. That matters because the alternative -- throughput in one file,
temperature in another -- gets joined by hand, once, by whoever is in a hurry.

## What is exact and what is attributed

`ds4-bench` writes no per-row timestamp. The manifest records exact ISO 8601
start and end for each REP, and a rep covers 8 frontiers in ~55 s. So:

- **rep-level** columns are exact for the rep and attributed to each of its
  rows. Every such name says `rep`.
- no per-frontier timestamp is invented to join against. Manufacturing one
  would manufacture precision the instrument never produced.

Sensor aggregates come from monitord at 1 Hz -- about 49 samples per rep -- so
`die_c_max` here is the real peak, not the end-of-rep reading the driver logs.
The sample count rides along: a rep backed by four samples is a different
claim from one backed by forty-nine.

## Fan rpm is the arm, measured

`condition` says what was commanded. `fan_rpm_mean_both` says what the fans
did. They should agree, and the column exists for the run where they do not.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import pathlib
import subprocess
import sys
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))

import monitord

import logs

logger = logging.getLogger(__name__)

AMBIENT_DS = "p9FyUovVk"
AMBIENT_ENTITY = "evan_s_pws_inside_temperature"


def ambient_series(minutes: int) -> list[tuple[dt.datetime, float]]:
    """(sample time, celsius) ascending, from Grafana. Empty on any failure.

    Never raises: a sensor that is down must not cost a report about a
    benchmark that already ran.
    """
    flux = (
        f'from(bucket: "home_assistant/autogen")\n'
        f"  |> range(start: -{minutes}m)\n"
        f'  |> filter(fn: (r) => r.entity_id == "{AMBIENT_ENTITY}")\n'
        f'  |> filter(fn: (r) => r._field == "value")\n'
        f'  |> keep(columns: ["_time", "_value"])'
    )
    try:
        got = subprocess.run(
            [
                "gcx",
                "datasources",
                "query",
                AMBIENT_DS,
                flux,
                "--from",
                f"now-{minutes}m",
                "--to",
                "now",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        rows = json.loads(got.stdout.strip().splitlines()[-1])["rows"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("ambient unavailable (%s); those columns stay empty", exc)
        return []
    return sorted(
        (dt.datetime.fromtimestamp(ms / 1000).astimezone(), (f - 32.0) * 5.0 / 9.0)
        for ms, f in rows
    )


def ambient_at(series: Sequence[tuple[dt.datetime, float]], when: dt.datetime):
    """Ambient at `when`, linear between the bracketing samples.

    Returns (celsius, "lo/hi" stamps, gap seconds). The room is a slow linear
    thing at this timescale and the sensor writes about once a minute while
    anything is moving, so interpolating across a minute is arithmetic rather
    than a precision claim. The gap rides along so a wide one is visible.
    """
    if not series:
        return None, None, None
    before = [row for row in series if row[0] <= when]
    after = [row for row in series if row[0] >= when]
    if not before or not after:
        edge = before[-1] if before else after[0]
        return round(edge[1], 3), edge[0].strftime("%H:%M:%S"), None
    lo, hi = before[-1], after[0]
    span = (hi[0] - lo[0]).total_seconds()
    value = (
        lo[1]
        if span <= 0
        else lo[1] + (hi[1] - lo[1]) * ((when - lo[0]).total_seconds() / span)
    )
    return round(value, 3), f"{lo[0]:%H:%M:%S}/{hi[0]:%H:%M:%S}", int(span)


def parse_iso(text: object) -> dt.datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


FIELDS = [
    # identity
    "phase",
    "segment",
    "condition",
    "rep",
    "ctx_tokens",
    # throughput -- the measurement
    "prefill_tps",
    "gen_tps",
    "gen_steady_tps",
    "gen_first_ms",
    "prefill_tokens",
    "gen_tokens",
    "gen_steady_tokens",
    "kvcache_bytes",
    # when
    "rep_start_iso",
    "rep_end_iso",
    "rep_seconds",
    # thermal, from monitord at 1 Hz over the rep
    "monitord_samples",
    "die_c_max",
    "die_c_mean",
    "die_c_min",
    "cpu_c_max",
    "cpu_c_mean",
    "enclosure_c_mean",
    "power_soc_w_mean",
    "power_soc_w_max",
    "power_input_w_mean",
    "power_input_w_max",
    "fan1_rpm_mean",
    "fan2_rpm_mean",
    "fan_rpm_mean_both",
    "gpu_util_mean",
    "vram_b_mean",
    # the driver's own readings
    "die_rep_end_c_driver",
    "phase_start_die_c",
    # ambient
    "ambient_rep_start_c",
    "ambient_rep_start_bracket",
    "ambient_rep_start_gap_s",
    "ambient_rep_end_c",
    "ambient_rep_end_bracket",
    "ambient_rep_end_gap_s",
    "delta_t_die_max_over_ambient_c",
    # how the phase began
    "cooldown_outcome",
    "cooldown_waited_s",
    "cooldown_slope_c_per_min",
    "cooled_on",
    "settle_in_s",
    # provenance
    "tree_sha",
    "gguf",
    "run_started_iso",
]


def rows_for(outdir: pathlib.Path, manifest: dict, series, sensors):
    phases = manifest.get("phases") or []
    arms = len(manifest.get("arms") or ["auto", "max"])
    for phase in phases:
        cooled = phase.get("cooldown") or {}
        index = phase.get("phase")
        segment = ((index - 1) // arms + 1) if isinstance(index, int) else None
        for rep in phase.get("reps") or []:
            began, ended = (
                parse_iso(rep.get("started_iso")),
                parse_iso(rep.get("ended_iso")),
            )
            agg = monitord.aggregate(sensors, began, ended) if began and ended else {}
            a0, a0b, a0g = ambient_at(series, began) if began else (None, None, None)
            a1, a1b, a1g = ambient_at(series, ended) if ended else (None, None, None)
            die_max = agg.get("die_c_max")
            path = outdir / str(rep.get("csv", ""))
            if not path.exists():
                logger.warning("missing %s; that rep contributes no rows", path.name)
                continue
            with path.open() as fh:
                for row in csv.DictReader(fh):
                    yield {
                        "phase": index,
                        "segment": segment,
                        "condition": phase.get("condition"),
                        "rep": rep.get("rep"),
                        "ctx_tokens": row.get("ctx_tokens"),
                        "prefill_tps": row.get("prefill_tps"),
                        "gen_tps": row.get("gen_tps"),
                        "gen_steady_tps": row.get("gen_steady_tps"),
                        "gen_first_ms": row.get("gen_first_ms"),
                        "prefill_tokens": row.get("prefill_tokens"),
                        "gen_tokens": row.get("gen_tokens"),
                        "gen_steady_tokens": row.get("gen_steady_tokens"),
                        "kvcache_bytes": row.get("kvcache_bytes"),
                        "rep_start_iso": rep.get("started_iso"),
                        "rep_end_iso": rep.get("ended_iso"),
                        "rep_seconds": (
                            int((ended - began).total_seconds())
                            if began and ended
                            else None
                        ),
                        "monitord_samples": agg.get("monitord_samples"),
                        "die_c_max": die_max,
                        "die_c_mean": agg.get("die_c_mean"),
                        "die_c_min": agg.get("die_c_min"),
                        "cpu_c_max": agg.get("cpu_c_max"),
                        "cpu_c_mean": agg.get("cpu_c_mean"),
                        "enclosure_c_mean": agg.get("enclosure_c_mean"),
                        "power_soc_w_mean": agg.get("power_soc_w_mean"),
                        "power_soc_w_max": agg.get("power_soc_w_max"),
                        "power_input_w_mean": agg.get("power_input_w_mean"),
                        "power_input_w_max": agg.get("power_input_w_max"),
                        "fan1_rpm_mean": agg.get("fan1_rpm_mean"),
                        "fan2_rpm_mean": agg.get("fan2_rpm_mean"),
                        "fan_rpm_mean_both": agg.get("fan_rpm_mean_both"),
                        "gpu_util_mean": agg.get("gpu_util_mean"),
                        "vram_b_mean": agg.get("vram_b_mean"),
                        "die_rep_end_c_driver": rep.get("die_c_after"),
                        "phase_start_die_c": phase.get("start_die_c"),
                        "ambient_rep_start_c": a0,
                        "ambient_rep_start_bracket": a0b,
                        "ambient_rep_start_gap_s": a0g,
                        "ambient_rep_end_c": a1,
                        "ambient_rep_end_bracket": a1b,
                        "ambient_rep_end_gap_s": a1g,
                        "delta_t_die_max_over_ambient_c": (
                            round(die_max - a0, 3)
                            if die_max is not None and a0 is not None
                            else None
                        ),
                        "cooldown_outcome": cooled.get("outcome"),
                        "cooldown_waited_s": cooled.get("waited_s"),
                        "cooldown_slope_c_per_min": cooled.get("last_slope_c_per_min"),
                        "cooled_on": cooled.get("cooled_on"),
                        "settle_in_s": cooled.get("settle_in_s"),
                        "tree_sha": str(manifest.get("tree_sha", ""))[:12],
                        "gguf": manifest.get("gguf"),
                        "run_started_iso": manifest.get("started_iso"),
                    }


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("outdir", type=pathlib.Path)
    p.add_argument(
        "--out", type=pathlib.Path, default=None, help="default: <outdir>/rows.csv"
    )
    p.add_argument("--ambient-minutes", type=int, default=600)
    args = p.parse_args(argv)
    logs.configure()

    outdir = args.outdir.expanduser().resolve()
    manifest_path = outdir / "fan-ab-manifest.json"
    if not manifest_path.exists():
        logger.error("no manifest at %s", manifest_path)
        return 2
    manifest = json.loads(manifest_path.read_text())
    phases = manifest.get("phases") or []
    if not phases:
        logger.error("manifest records no phases")
        return 2

    lo = parse_iso(phases[0].get("started_iso"))
    hi = parse_iso(phases[-1].get("ended_iso"))
    csv_path = monitord.latest_csv()
    sensors = monitord.load(csv_path, lo, hi) if (csv_path and lo and hi) else []
    logger.info("monitord: %s rows from %s", len(sensors), csv_path)
    series = ambient_series(args.ambient_minutes)
    logger.info("ambient: %d samples", len(series))

    target = args.out or (outdir / "rows.csv")
    written = 0
    with target.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows_for(outdir, manifest, series, sensors):
            writer.writerow(row)
            written += 1
    logger.info("wrote %d rows to %s", written, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
