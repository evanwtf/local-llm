"""Build the macOS 26-vs-27 dataset for the M5 Max (#499).

    uv run python scripts/os_compare.py                       # the three #306 stacks
    uv run python scripts/os_compare.py --backend qwen38fnq3  # one stack

Reads the M5 Max ledger through `results.trials()`, gives every row an OS side,
and writes one record per (backend, task) with both sides summarized. The
report `docs/macos-26-vs-27.md` is generated from this file, never from a
hand-read ledger.

## Which side a row is on

A row that carries `env.macos` uses its major version. A row without it is
placed by time: the M5 Max first booted macOS 27.0 at EPOCH_27 (`kern.boottime`,
recorded on #306), so a row that started before that instant is macOS 26.
A row without `env.macos` that started after the epoch has no side; the harness
stamps every new row, so such a row is a defect to look at, not data to guess.

A row whose `env.macos` disagrees with its time (a 27 row before the epoch, a
26 row after it) is a CONFLICT. It is dropped and counted, because one of its
two stamps is wrong and nothing says which.

## What moved with the OS

Engines, the client and the harness were all updated across the boundary --
the operator's rule is the latest of everything, never a pin. So each record
lists the engine and client versions on each side. A difference in a cell
whose versions also changed cannot be credited to the OS alone, and the report
must say so beside the number.

Each side carries two summaries. `latest` is the side's newest engine version
only -- the comparison the report makes. `all` is every placed row, for context.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import statistics
import sys
from typing import Any
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "benchmarks" / "agent"))

import report
import results

import logs

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
# First boot of macOS 27.0 (26A428) on the M5 Max, from kern.boottime (#306).
EPOCH_27 = dt.datetime.fromisoformat("2026-09-18T06:53:09-04:00")
SIDES = ("26", "27")
# The stacks that took the last macOS 26 datapoint on 2026-09-17 (#306).
DEFAULT_BACKENDS = ("qwen38fnq3", "qwen38fnmlxserve", "qwen38fnds4main")
DEFAULT_OUT = (
    HERE.parent
    / "hardware"
    / "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A"
    / "benchmarks"
    / "macos-26-vs-27"
    / "dataset.json"
)


def started_at(row: dict[str, Any]) -> dt.datetime | None:
    """The row's start as an aware time. A naive value is New York (#209)."""
    raw = row.get("started")
    if not raw:
        return None
    t = dt.datetime.fromisoformat(raw)
    return t if t.tzinfo else t.replace(tzinfo=NEW_YORK)


def stamped_major(row: dict[str, Any]) -> str | None:
    """The major version in `env.macos`, or None when the row has none."""
    macos = (row.get("env") or {}).get("macos")
    return str(macos).split(".")[0] if macos else None


def conflict(row: dict[str, Any]) -> bool:
    """True when `env.macos` and the start time put the row on different sides."""
    major, t = stamped_major(row), started_at(row)
    if major is None or t is None or major not in SIDES:
        return False
    by_time = "26" if t < EPOCH_27 else "27"
    return major != by_time


def os_side(row: dict[str, Any]) -> str | None:
    """'26', '27', or None when the row cannot be placed or is a conflict."""
    if conflict(row):
        return None
    major = stamped_major(row)
    if major is not None:
        return major if major in SIDES else None
    t = started_at(row)
    if t is not None and t < EPOCH_27:
        return "26"
    return None


def engine_version(row: dict[str, Any]) -> str | None:
    """The version the harness stamped for this row's own backend."""
    servers = (row.get("env") or {}).get("servers") or {}
    return (servers.get(row.get("backend")) or {}).get("engine_version")


def side_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """n, passes, median and summed wall, and the versions behind them."""
    walls = [r["wall_seconds"] for r in rows if r.get("wall_seconds")]
    return {
        "n": len(rows),
        "passed": sum(1 for r in rows if results.verdict(r)),
        "median_wall_s": round(statistics.median(walls), 1) if walls else None,
        "summed_wall_s": round(sum(walls), 1) if walls else None,
        "macos": sorted(
            {(r.get("env") or {}).get("macos") or "unstamped" for r in rows}
        ),
        "engine_versions": sorted({v for r in rows if (v := engine_version(r))}),
        "client_versions": sorted({v for r in rows if (v := r.get("client_version"))}),
    }


def newest_version_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows that share the engine version of the latest-started row."""
    if not rows:
        return []
    floor = dt.datetime.min.replace(tzinfo=dt.UTC)
    newest = max(rows, key=lambda r: started_at(r) or floor)
    want = engine_version(newest)
    return [r for r in rows if engine_version(r) == want]


def build(
    rows: list[dict[str, Any]],
    backends: tuple[str, ...] | list[str],
    client: str = "opencode",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One record per (backend, task), and counts of the rows left out.

    Rows are split by side FIRST and `report.cells()` runs on each side, so the
    server_argv reduction (#213) never pools a 26 row with a 27 row.

    Each side has two summaries. `latest` holds only the rows of the side's
    newest engine version: the latest of everything on that OS, which is the
    comparison the report makes. `all` holds every placed row, for context.
    The #213 reduction keeps a cell's LARGEST argv-compatible subset, so on
    `all` alone three old rows outvote the one fresh baseline row taken just
    before the upgrade (2026-09-17, llama.cpp 972d2313b against 2092353c8).
    """
    split: dict[str, list[dict[str, Any]]] = {s: [] for s in SIDES}
    dropped = {"conflict": 0, "unplaced": 0}
    for r in rows:
        if r.get("backend") not in backends or r.get("client") != client:
            continue
        if conflict(r):
            dropped["conflict"] += 1
            continue
        side = os_side(r)
        if side is None:
            dropped["unplaced"] += 1
            continue
        split[side].append(r)
    wanted = set(backends)
    everything = {s: report.cells(split[s], wanted, client) for s in SIDES}
    latest = {}
    for s in SIDES:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in split[s]:
            grouped.setdefault((r["backend"], r["task"]), []).append(r)
        newest = [r for cell in grouped.values() for r in newest_version_rows(cell)]
        latest[s] = report.cells(newest, wanted, client)
    keys = sorted(set(everything["26"]) | set(everything["27"]))
    records = []
    for key in keys:
        sides = {
            s: {
                "latest": side_summary(latest[s].get(key, [])),
                "all": side_summary(everything[s].get(key, [])),
            }
            for s in SIDES
        }
        a, b = sides["26"]["latest"], sides["27"]["latest"]
        records.append(
            {
                "backend": key[0],
                "task": key[1],
                "macos26": sides["26"],
                "macos27": sides["27"],
                "engine_changed": a["engine_versions"] != b["engine_versions"],
                "client_changed": a["client_versions"] != b["client_versions"],
            }
        )
    return records, dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--backend", action="append", help="repeatable; default the #306 stacks"
    )
    ap.add_argument("--client", default="opencode")
    ap.add_argument("--results", type=pathlib.Path, default=results.default_path())
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    logs.configure()
    backends = tuple(args.backend or DEFAULT_BACKENDS)
    records, dropped = build(results.trials(args.results), backends, args.client)
    if not records:
        logger.error("no rows for %s under %s", ", ".join(backends), args.client)
        return 1
    doc = {
        "epoch_27": EPOCH_27.isoformat(),
        "client": args.client,
        "backends": list(backends),
        "dropped": dropped,
        "cells": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    for r in records:
        a, b = r["macos26"]["latest"], r["macos27"]["latest"]
        logger.info(
            "%-18s %-28s 26: %d/%d med %s | 27: %d/%d med %s%s",
            r["backend"],
            r["task"],
            a["passed"],
            a["n"],
            a["median_wall_s"],
            b["passed"],
            b["n"],
            b["median_wall_s"],
            "  [engine changed]" if r["engine_changed"] else "",
        )
    logger.info(
        "wrote %s: %d cells; dropped %d conflict, %d unplaced",
        args.out,
        len(records),
        dropped["conflict"],
        dropped["unplaced"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
