"""Summarise and compare measured cells, with the resolution rule applied.

Written because the same analysis was hand-rolled three times in one evening --
per-task medians, pass rates, spreads, and a two-backend comparison -- and each
hand-roll is a chance to filter wrongly. `gen_tables.load()` did exactly that:
it filtered on `is_excluded()` alone and counted 127 `--dry-run` control checks
as failures in the published tables.

So this calls `results.trials()` and `results.verdict()` and nothing else, and
it refuses to compare what three trials cannot separate. #23 measured a 3-trial
median at +/-27.9%, which means two stacks must differ by roughly 56% before the
difference is real -- a rule that changed the conclusion of the Qwen generation
comparison the night this was written.

    uv run python scripts/report.py --backend gemma426
    uv run python scripts/report.py --backend qwen --backend qwen36
"""

from __future__ import annotations

import argparse
import collections
import logging
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "benchmarks" / "agent"))

import provenance
import results
import summarize

logger = logging.getLogger(__name__)

RESULTS = results.default_path()

# #23: a 3-trial median carries +/-27.9%, so two medians must differ by about
# this much before the gap is real rather than sampling.
RESOLUTION = 0.56
SCRIPT_PREFIX = "script-"


def cells(rows, backends, client="opencode"):
    """{(backend, task): [row, ...]} for the backends asked for."""
    got = collections.defaultdict(list)
    for r in rows:
        if r.get("client") != client:
            continue
        if backends and r.get("backend") not in backends:
            continue
        got[(r["backend"], r["task"])].append(r)
    return got


def summarise(rows):
    """(passed, n, median, worst, spread) for one cell, or None if empty."""
    if not rows:
        return None
    times = [r["wall_seconds"] for r in rows if r.get("wall_seconds")]
    passed = sum(1 for r in rows if results.verdict(r))
    if not times:
        return passed, len(rows), None, None, None
    return (
        passed,
        len(rows),
        statistics.median(times),
        max(times),
        round(max(times) / min(times), 1),
    )


def distinguishable(a: float, b: float) -> bool:
    """Whether two medians differ by enough for three trials to tell them apart."""
    if not a or not b:
        return False
    lo, hi = sorted((a, b))
    return (hi - lo) / lo >= RESOLUTION


def untouched_cells(by_cell):
    """Cells where every trial failed with the same oracle output (#55 A3).

    An excision is applied and every trial produces the same failure message ->
    the agent never touched the file. The oracle is the excised control, and
    the failure is what a virgin tree gives. This is a distinct diagnosis from
    "the model wrote wrong code": a wrong fix produces a different failure.

    Identical PASS output is filtered out. `script-reverse` and friends emit a
    terse fixed string on success ("3/3 checks passed"), which is not the
    signal this check is looking for.
    """
    suspect = []
    for (backend, task), rows in by_cell.items():
        if len(rows) < 2:
            continue
        outs = {r.get("pytest", "") for r in rows if not r.get("passed")}
        n_failed = sum(1 for r in rows if not results.verdict(r))
        if n_failed == len(rows) and len(outs) == 1 and outs != {""}:
            suspect.append((backend, task, next(iter(outs))))
    return suspect


def render(by_cell, backends) -> list[str]:
    tasks = sorted({task for _, task in by_cell})
    out = [f"| task | {' | '.join(backends)} |", "|---" * (len(backends) + 1) + "|"]
    for task in tasks:
        cols = []
        for b in backends:
            got = summarise(by_cell.get((b, task), []))
            cols.append(
                "-"
                if not got
                else f"{got[2]:.1f}s ({got[0]}/{got[1]})"
                if got[2]
                else f"({got[0]}/{got[1]})"
            )
        out.append(f"| `{task}` | {' | '.join(cols)} |")

    # Excision only, matching the published tables: script tasks are a
    # different class and pooling them flatters whoever is good at boilerplate.
    out.append("")
    for b in backends:
        ex = [
            r
            for (bb, task), rows in by_cell.items()
            if bb == b and not task.startswith(SCRIPT_PREFIX)
            for r in rows
        ]
        got = summarise(ex)
        if got:
            median = f"{got[2]:.1f}s" if got[2] else "n/a"
            out.append(
                f"**{b}** excision: {got[0]}/{got[1]} passed, median {median}, "
                f"worst {got[3]:.1f}s, spread {got[4]}x"
            )

    if len(backends) == 2:
        out += ["", "**Can three trials tell them apart?**", ""]
        a, b = backends
        for task in tasks:
            ga = summarise(by_cell.get((a, task), []))
            gb = summarise(by_cell.get((b, task), []))
            if not (ga and gb and ga[2] and gb[2]):
                continue
            lo, hi = sorted((ga[2], gb[2]))
            gap = (hi - lo) / lo
            verdict = "YES" if distinguishable(ga[2], gb[2]) else "no"
            out.append(
                f"- `{task}`: {gap:.0%} apart -- **{verdict}** "
                f"(needs {RESOLUTION:.0%}; #23)"
            )
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", action="append", required=True)
    p.add_argument("--client", default="opencode")
    p.add_argument("--since", help="ISO timestamp; only rows started after it")
    args = p.parse_args()

    provenance.configure()
    log_file = provenance.tee("report", machine_specific=True)
    provenance.banner(logger, engines=True)
    # summarize.load() is the tested reader: it drops dry runs, drops rows
    # whose control did not fail (an excision the tests could not see), and
    # normalises `passed` through verdict() so a timeout lands as False rather
    # than vanishing from the denominator. Reading results.jsonl any other way
    # is how fourteen legacy-keyed rows got counted (#29).
    rows, discarded, retired, cheats = summarize.load(RESULTS)
    if discarded or cheats:
        logger.info(
            "  dropped: %d control-did-not-fail, %d touched tests; %d excluded/dry-run",
            discarded,
            cheats,
            retired,
        )
    if args.since:
        rows = [r for r in rows if (r.get("started") or "") > args.since]
    by_cell = cells(rows, set(args.backend), args.client)
    if not by_cell:
        logger.info("no trials for %s under %s", args.backend, args.client)
        return 1
    for line in render(by_cell, args.backend):
        logger.info(line)

    # #55 A3: a cell where every trial fails with the same oracle output is a
    # cell where the tree was never touched. Model wrote wrong code produces a
    # DIFFERENT failure; a virgin excision produces the SAME one every time.
    untouched = untouched_cells(by_cell)
    if untouched:
        logger.warning("")
        logger.warning(
            "%d cell(s) look UNTOUCHED -- every trial failed with the same "
            "oracle output (#55):", len(untouched),
        )
        for backend, task, out in untouched:
            logger.warning("  %s %s: %s", backend, task, out[:80])

    logger.info("log: %s", log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
