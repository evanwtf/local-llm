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

## Three states: 26, 27 on first boot, 27 settled (#834)

#499 took its macOS 27 rows on first boot, 2026-09-18, while the upgrade's
indexing daemons used more than 3 CPU cores. #834 reran the same stacks and
tasks once the machine had settled, from SETTLED_27 on. So a 27 row is "first
boot" when it started before FIRST_BOOT_END, "settled" from SETTLED_27, and
"interim" in between. An interim row is other work on 27, neither state, and
it is counted, not used. The #834 rows ran on fresh engine builds (the
operator's call, 2026-09-29), so `settled_engine_changed` is set wherever the
engine moved between first boot and settled.

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
# Every #499 macOS 27 row ran on first-boot day, 2026-09-18.
FIRST_BOOT_END = dt.datetime.fromisoformat("2026-09-19T00:00:00-04:00")
# The #834 settled rerun's launch.
SETTLED_27 = dt.datetime.fromisoformat("2026-09-29T07:51:52-04:00")
# State key -> the record key it is written under.
STATES = {"26": "macos26", "27": "macos27", "27s": "macos27_settled"}
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
DEFAULT_REPORT = HERE.parent / "docs" / "macos-26-vs-27.md"


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


def state(row: dict[str, Any]) -> str | None:
    """'26', '27' (first boot), '27s' (settled), or None (unplaced or interim)."""
    side = os_side(row)
    if side != "27":
        return side
    t = started_at(row)
    if t is None:
        return None
    if t < FIRST_BOOT_END:
        return "27"
    if t >= SETTLED_27:
        return "27s"
    return None


def engine_version(row: dict[str, Any]) -> str | None:
    """The version the harness stamped for this row's own backend."""
    servers = (row.get("env") or {}).get("servers") or {}
    return (servers.get(row.get("backend")) or {}).get("engine_version")


def side_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """n, passes, median and summed wall, and the versions behind them."""
    walls = [r["wall_seconds"] for r in rows if r.get("wall_seconds")]
    times = [t.astimezone(NEW_YORK) for r in rows if (t := started_at(r))]
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
        "first_started": min(times).isoformat() if times else None,
        "last_started": max(times).isoformat() if times else None,
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
    split: dict[str, list[dict[str, Any]]] = {s: [] for s in STATES}
    dropped = {"conflict": 0, "unplaced": 0, "interim": 0}
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
        st = state(r)
        if st is None:
            dropped["interim"] += 1
            continue
        split[st].append(r)
    wanted = set(backends)
    everything = {s: report.cells(split[s], wanted, client) for s in STATES}
    latest = {}
    for s in STATES:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in split[s]:
            grouped.setdefault((r["backend"], r["task"]), []).append(r)
        newest = [r for cell in grouped.values() for r in newest_version_rows(cell)]
        latest[s] = report.cells(newest, wanted, client)
    keys = sorted(set().union(*(everything[s] for s in STATES)))
    records = []
    for key in keys:
        sides = {
            s: {
                "latest": side_summary(latest[s].get(key, [])),
                "all": side_summary(everything[s].get(key, [])),
            }
            for s in STATES
        }
        a, b, c = (sides[s]["latest"] for s in ("26", "27", "27s"))
        records.append(
            {
                "backend": key[0],
                "task": key[1],
                **{STATES[s]: sides[s] for s in STATES},
                "engine_changed": a["engine_versions"] != b["engine_versions"],
                "client_changed": a["client_versions"] != b["client_versions"],
                "settled_engine_changed": (
                    b["engine_versions"] != c["engine_versions"]
                ),
            }
        )
    return records, dropped


def dataset(
    records: list[dict[str, Any]],
    dropped: dict[str, int],
    backends: tuple[str, ...],
    client: str,
) -> dict[str, Any]:
    """The document written to dataset.json."""
    return {
        "epoch_27": EPOCH_27.isoformat(),
        "first_boot_end": FIRST_BOOT_END.isoformat(),
        "settled_27": SETTLED_27.isoformat(),
        "client": client,
        "backends": list(backends),
        "dropped": dropped,
        "cells": records,
    }


def _secs(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def _pct(new: float | None, old: float | None) -> str:
    if new is None or not old:
        return "—"
    return f"{round(100 * new / old)}%"


SETTLED_KEY = STATES["27s"]
_EMPTY: dict[str, Any] = {
    "n": 0,
    "passed": 0,
    "median_wall_s": None,
    "summed_wall_s": None,
    "macos": [],
    "engine_versions": [],
    "client_versions": [],
    "first_started": None,
    "last_started": None,
}


def _settled(cell: dict[str, Any]) -> dict[str, Any]:
    """The settled state, empty for a dataset written before #834."""
    return cell.get(SETTLED_KEY) or {"latest": _EMPTY, "all": _EMPTY}


def _union(cells: list[dict[str, Any]], side: str, field: str) -> str:
    vals = sorted(
        {v for c in cells for v in (c.get(side) or _settled(c))["latest"][field]}
    )
    return ", ".join(f"`{v}`" for v in vals) or "—"


def _span(cells: list[dict[str, Any]], side: str) -> str:
    got = [(c.get(side) or _settled(c))["latest"] for c in cells]
    firsts = [f for g in got if (f := g["first_started"])]
    lasts = [f for g in got if (f := g["last_started"])]
    if not firsts:
        return "—"
    return f"{min(firsts, key=dt.datetime.fromisoformat)} to {max(lasts, key=dt.datetime.fromisoformat)}"


def render(doc: dict[str, Any]) -> str:
    """The comparison as markdown: a totals table, then one table per backend.

    Numbers only. Each percent is the macOS 27 median as a share of the macOS
    26 median, with both in seconds beside it (measurement-discipline.md).
    """
    by_backend: dict[str, list[dict[str, Any]]] = {}
    for c in doc["cells"]:
        by_backend.setdefault(c["backend"], []).append(c)
    out = [
        "# macOS 26 vs macOS 27 on the M5 Max",
        "",
        "<!-- Generated by scripts/os_compare.py from dataset.json. Do not edit. -->",
        "",
        (
            "Machine: MacBook Pro M5 Max, 128 GB. "
            f"Client: {doc['client']}. Issue: "
            "[#499](https://github.com/evanwtf/local-llm/issues/499)."
        ),
        "",
        (
            f"- macOS 27 epoch: `{doc['epoch_27']}` (first boot, `kern.boottime`, "
            "[#306](https://github.com/evanwtf/local-llm/issues/306))."
        ),
        (
            "- Data: [`dataset.json`](../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/"
            "benchmarks/macos-26-vs-27/dataset.json)."
        ),
        (
            "- Each side uses only its newest engine version (`latest`). "
            "The last column shows every macOS 26 row, for context."
        ),
        (
            "- **27 / 26** is the macOS 27 median wall time as a percent of the "
            "macOS 26 median. Wall times are in seconds."
        ),
        (
            "- **27s** is macOS 27 settled: the same stacks and tasks rerun from "
            f"`{doc['settled_27']}` ([#834](https://github.com/evanwtf/local-llm/"
            "issues/834)), on fresh engine builds. **27s / 26** and **27s / 27** "
            "compare it with macOS 26 and with 27 on first boot. The 27 column is "
            f"first boot only, rows started before `{doc['first_boot_end']}`."
        ),
        (
            f"- Rows dropped: {doc['dropped']['conflict']} conflict, "
            f"{doc['dropped']['unplaced']} unplaced, "
            f"{doc['dropped'].get('interim', 0)} interim (macOS 27 rows from "
            "other work between first boot and the settled rerun)."
        ),
        "",
        "## Totals",
        "",
        (
            "Sums of the per-task medians. The 26 and 27 sums cover tasks with a "
            "median on both; the 27s columns cover tasks with a median in all three."
        ),
        "",
        (
            "| backend | 26 passed | 27 passed | 26 sum (s) | 27 sum (s) | 27 / 26 "
            "| 27s passed | 27s sum (s) | 27s / 26 | 27s / 27 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for backend, cells in by_backend.items():
        a = [c["macos26"]["latest"] for c in cells]
        b = [c["macos27"]["latest"] for c in cells]
        z = [_settled(c)["latest"] for c in cells]
        three = [
            (x["median_wall_s"], y["median_wall_s"], w["median_wall_s"])
            for x, y, w in zip(a, b, z, strict=True)
            if None not in (x["median_wall_s"], y["median_wall_s"], w["median_wall_s"])
        ]
        t26 = round(sum(x for x, _, _ in three), 1) if three else None
        t27 = round(sum(y for _, y, _ in three), 1) if three else None
        t27s = round(sum(w for _, _, w in three), 1) if three else None
        both = [
            (x["median_wall_s"], y["median_wall_s"])
            for x, y in zip(a, b, strict=True)
            if x["median_wall_s"] is not None and y["median_wall_s"] is not None
        ]
        s26 = round(sum(x for x, _ in both), 1) if both else None
        s27 = round(sum(y for _, y in both), 1) if both else None
        out.append(
            f"| {backend} "
            f"| {sum(x['passed'] for x in a)}/{sum(x['n'] for x in a)} "
            f"| {sum(y['passed'] for y in b)}/{sum(y['n'] for y in b)} "
            f"| {_secs(s26)} | {_secs(s27)} | {_pct(s27, s26)} "
            f"| {sum(w['passed'] for w in z)}/{sum(w['n'] for w in z)} "
            f"| {_secs(t27s)} | {_pct(t27s, t26)} | {_pct(t27s, t27)} |"
        )
    for backend, cells in by_backend.items():
        changed = any(c["engine_changed"] for c in cells)
        out += [
            "",
            f"## {backend}",
            "",
            "| | macOS 26 | macOS 27, first boot | macOS 27, settled |",
            "|---|---|---|---|",
            (
                f"| macOS | {_union(cells, 'macos26', 'macos')} "
                f"| {_union(cells, 'macos27', 'macos')} "
                f"| {_union(cells, SETTLED_KEY, 'macos')} |"
            ),
            (
                f"| engine{' (engine changed)' if changed else ''} "
                f"| {_union(cells, 'macos26', 'engine_versions')} "
                f"| {_union(cells, 'macos27', 'engine_versions')} "
                f"| {_union(cells, SETTLED_KEY, 'engine_versions')} |"
            ),
            (
                f"| client | {_union(cells, 'macos26', 'client_versions')} "
                f"| {_union(cells, 'macos27', 'client_versions')} "
                f"| {_union(cells, SETTLED_KEY, 'client_versions')} |"
            ),
            (
                f"| ran | {_span(cells, 'macos26')} | {_span(cells, 'macos27')} "
                f"| {_span(cells, SETTLED_KEY)} |"
            ),
            "",
            (
                "| task | 26 passed | 26 median (s) | 27 passed | 27 median (s) "
                "| 27 / 26 | 27s passed | 27s median (s) | 27s / 26 | 27s / 27 "
                "| 26 all rows: median (s), n |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for c in cells:
            x, y, w, h = (
                c["macos26"]["latest"],
                c["macos27"]["latest"],
                _settled(c)["latest"],
                c["macos26"]["all"],
            )
            out.append(
                f"| {c['task']} | {x['passed']}/{x['n']} | {_secs(x['median_wall_s'])} "
                f"| {y['passed']}/{y['n']} | {_secs(y['median_wall_s'])} "
                f"| {_pct(y['median_wall_s'], x['median_wall_s'])} "
                f"| {w['passed']}/{w['n']} | {_secs(w['median_wall_s'])} "
                f"| {_pct(w['median_wall_s'], x['median_wall_s'])} "
                f"| {_pct(w['median_wall_s'], y['median_wall_s'])} "
                f"| {_secs(h['median_wall_s'])}, {h['n']} |"
            )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--backend", action="append", help="repeatable; default the #306 stacks"
    )
    ap.add_argument("--client", default="opencode")
    ap.add_argument("--results", type=pathlib.Path, default=results.default_path())
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--report", type=pathlib.Path, default=DEFAULT_REPORT)
    args = ap.parse_args(argv)
    logs.configure()
    backends = tuple(args.backend or DEFAULT_BACKENDS)
    records, dropped = build(results.trials(args.results), backends, args.client)
    if not records:
        logger.error("no rows for %s under %s", ", ".join(backends), args.client)
        return 1
    doc = dataset(records, dropped, backends, args.client)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(doc))
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
        "wrote %s and %s: %d cells; dropped %d conflict, %d unplaced, %d interim",
        args.out,
        args.report,
        len(records),
        dropped["conflict"],
        dropped["unplaced"],
        dropped["interim"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
