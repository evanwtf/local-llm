#!/usr/bin/env python3
"""Ternary-Bonsai on the 3080 Ti: is it the quantization, or the engine? #269

The lead was "Q2 beats the Q1 you measured". Three arms on this tier answer it,
and only one of the three differences between them is the quantization:

    dtbonsai27b           Q1_0   ollama
    dtbonsai27bllamacpp   Q1_0   llama.cpp (PrismML)
    dtternarybonsai27b    PQ2_0  llama.cpp (PrismML)

So the engine contrast is the first pair and the quant contrast is the second,
and the naive comparison -- the Ollama Q1_0 rows against the llama.cpp PQ2_0
rows -- crosses both at once. That comparison read as a decisive win on
2026-09-09 and did not survive the control arm.

## What it refuses to do

**It never pools two engine builds into one arm.** The arm key is the backend
*and* the engine build that served it, read from the row. An Ollama upgrade is
what turned a 42% arm into a 54% arm between 2026-09-03 and 2026-09-09, with
the same weights and the same backend name, so a script that keys on the
backend alone reports a version bump as a model difference. A backend served by
two builds prints as two arms and is never averaged.

**It compares only the tasks both arms ran.** An arm that skipped the two
repository tasks would otherwise post a pass rate against an arm that did not.

**It prints the counts beside every rate**, and the per-task medians beside
every pooled median -- a failing arm dies fast, so its pooled median is the
median of shorter work (AGENTS.md, "A failing arm looks fast").

**It asks `results.verdict()` for every verdict.** A timeout carries no
`passed` key and is a failure, not an absence.
"""

from __future__ import annotations

import argparse
import collections
import logging
import pathlib
import statistics as st
import sys
from collections.abc import Sequence
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import results
import strip_ab_report

import logs

logger = logging.getLogger(__name__)

#: The three arms of #269, in the order the contrasts read.
BACKENDS = ("dtbonsai27b", "dtbonsai27bllamacpp", "dtternarybonsai27b")

#: What each backend is, for a reader who does not have tasks.toml open. The
#: engine build is NOT here: it comes from the rows, because that is the field
#: that has changed under a fixed backend name.
WEIGHTS = {
    "dtbonsai27b": "Q1_0",
    "dtbonsai27bllamacpp": "Q1_0",
    "dtternarybonsai27b": "PQ2_0",
}


def engine_of(row: dict[str, Any]) -> str:
    """The engine build that served this row, as a label.

    llama.cpp rows carry `env.servers.<backend>.engine_version`; Ollama rows
    carry no server block at all and name their version in `env.ollama`. Both
    are the same fact -- which build answered -- and the arm key needs it.
    """
    env = row.get("env") or {}
    server = (env.get("servers") or {}).get(row.get("backend")) or {}
    name, version = server.get("engine_name"), server.get("engine_version")
    if name:
        return f"{name} {version}" if version else str(name)
    ollama = env.get("ollama")
    if ollama:
        # "ollama version is 0.33.3" -> "ollama 0.33.3"
        return f"ollama {str(ollama).rsplit(' ', 1)[-1]}"
    return "unrecorded"


def arm_of(row: dict[str, Any]) -> tuple[str, str]:
    return row["backend"], engine_of(row)


def arms(rows: Sequence[dict[str, Any]]) -> dict[tuple[str, str], list[dict]]:
    """Rows grouped by (backend, engine build), in BACKENDS order."""
    grouped: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        if row.get("backend") in BACKENDS:
            grouped[arm_of(row)].append(row)
    order = {b: i for i, b in enumerate(BACKENDS)}
    return dict(sorted(grouped.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])))


def tally(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """(passes, n) over `results.verdict`."""
    return sum(1 for r in rows if results.verdict(r)), len(rows)


def by_task(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        out[row["task"]].append(row)
    return dict(sorted(out.items()))


def median_wall(rows: Sequence[dict[str, Any]]) -> float | None:
    walls = [r["wall_seconds"] for r in rows if r.get("wall_seconds") is not None]
    return st.median(walls) if walls else None


def describe(arm: tuple[str, str], rows: Sequence[dict[str, Any]]) -> None:
    backend, engine = arm
    passes, n = tally(rows)
    logger.info("%s -- %s on %s", backend, WEIGHTS.get(backend, "?"), engine)
    logger.info("  pooled %d/%d", passes, n)
    for task, task_rows in by_task(rows).items():
        t_pass, t_n = tally(task_rows)
        wall = median_wall(task_rows)
        logger.info(
            "    %-18s %d/%d   median wall %s",
            task,
            t_pass,
            t_n,
            f"{wall:.1f}s" if wall is not None else "unrecorded",
        )


def shared_tasks(
    a_rows: Sequence[dict[str, Any]], b_rows: Sequence[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """(tasks both arms ran, tasks only one of them ran).

    The second list is what the contrast must drop. An arm that ran four tasks
    and an arm that ran five do not have comparable pass rates, and the gap is
    invisible in two pooled totals.
    """
    a_tasks, b_tasks = set(by_task(a_rows)), set(by_task(b_rows))
    shared = a_tasks & b_tasks
    return sorted(shared), sorted((a_tasks | b_tasks) - shared)


def compare(
    a_rows: Sequence[dict[str, Any]], b_rows: Sequence[dict[str, Any]]
) -> tuple[tuple[int, int], tuple[int, int], float]:
    """((a passes, a n), (b passes, b n), Fisher p) over the shared tasks."""
    shared, _ = shared_tasks(a_rows, b_rows)
    a_pass, a_n = tally([r for r in a_rows if r["task"] in shared])
    b_pass, b_n = tally([r for r in b_rows if r["task"] in shared])
    p = strip_ab_report.fisher_exact(a_pass, a_n - a_pass, b_pass, b_n - b_pass)
    return (a_pass, a_n), (b_pass, b_n), p


def contrast(
    name: str,
    fixed: str,
    a: tuple[str, str],
    a_rows: Sequence[dict[str, Any]],
    b: tuple[str, str],
    b_rows: Sequence[dict[str, Any]],
) -> None:
    """One 2x2 over the tasks both arms ran, pooled and per task."""
    shared, dropped = shared_tasks(a_rows, b_rows)
    logger.info("%s (%s fixed): %s vs %s", name, fixed, a[1] or a[0], b[1] or b[0])
    if dropped:
        logger.info("  not run by both arms, excluded: %s", ", ".join(dropped))
    a_shared = [r for r in a_rows if r["task"] in shared]
    b_shared = [r for r in b_rows if r["task"] in shared]
    (a_pass, a_n), (b_pass, b_n), p = compare(a_rows, b_rows)
    logger.info(
        "  %s %d/%d  vs  %s %d/%d   Fisher exact two-sided p = %.4f",
        a[0],
        a_pass,
        a_n,
        b[0],
        b_pass,
        b_n,
        p,
    )
    for task in shared:
        ta_pass, ta_n = tally([r for r in a_shared if r["task"] == task])
        tb_pass, tb_n = tally([r for r in b_shared if r["task"] == task])
        tp = strip_ab_report.fisher_exact(
            ta_pass, ta_n - ta_pass, tb_pass, tb_n - tb_pass
        )
        logger.info(
            "    %-18s %d/%d vs %d/%d   p = %.4f",
            task,
            ta_pass,
            ta_n,
            tb_pass,
            tb_n,
            tp,
        )


def report(path: pathlib.Path) -> int:
    grouped = arms(results.trials(path))
    if not grouped:
        logger.error("no rows for %s in %s", "/".join(BACKENDS), path)
        return 1
    logger.info("ledger: %s", path)
    for arm, rows in grouped.items():
        describe(arm, rows)
    llamacpp = {a: r for a, r in grouped.items() if a[1].startswith("llama.cpp")}
    ollama = {a: r for a, r in grouped.items() if a[1].startswith("ollama")}

    # The engine contrast holds the weights fixed: Q1_0 both sides. Where the
    # Ollama arm spans two builds, the newest is the honest control -- the
    # older one measures a sampler this tier no longer serves (#84).
    q1_llamacpp = [a for a in llamacpp if WEIGHTS.get(a[0]) == "Q1_0"]
    q1_ollama = sorted(a for a in ollama if WEIGHTS.get(a[0]) == "Q1_0")
    if q1_llamacpp and q1_ollama:
        newest = q1_ollama[-1]
        if len(q1_ollama) > 1:
            logger.info(
                "Ollama Q1_0 spans %d builds; the contrast uses the newest (%s)",
                len(q1_ollama),
                newest[1],
            )
        contrast(
            "engine",
            "Q1_0 weights",
            newest,
            grouped[newest],
            q1_llamacpp[0],
            grouped[q1_llamacpp[0]],
        )

    # The quant contrast holds the engine fixed: llama.cpp both sides.
    q1 = [a for a in llamacpp if WEIGHTS.get(a[0]) == "Q1_0"]
    q2 = [a for a in llamacpp if WEIGHTS.get(a[0]) == "PQ2_0"]
    if q1 and q2:
        if len({a[1] for a in q1 + q2}) > 1:
            logger.warning(
                "the two llama.cpp arms were served by different builds (%s); "
                "this is not a quantization contrast",
                ", ".join(sorted({a[1] for a in q1 + q2})),
            )
        contrast("quant", "llama.cpp", q1[0], grouped[q1[0]], q2[0], grouped[q2[0]])
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--results",
        type=pathlib.Path,
        default=None,
        help="default: this machine's ledger, per results.default_path()",
    )
    args = p.parse_args(argv)
    logs.configure()
    return report(args.results or results.default_path())


if __name__ == "__main__":
    raise SystemExit(main())
