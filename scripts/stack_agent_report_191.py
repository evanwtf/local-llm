"""Read out for the #191 stack A/B: mlx-serve against ds4, one screen.

Implements the pre-registered recipe from #191. The one difference from #138's
reporter (scripts/stack_agent_report.py) is the wall filter: #191 pre-registered
the wall statistic on tasks BOTH ARMS PASSED, where #138 included wrong-code
failures and timeouts. Everything else is shared and imported from
stack_agent_report -- the sweep windows, the void checks, the pass pairing, the
screen verdict, and the bars are #138's, and they are #191's too.

The arms are read from the environment the same way stack_agent_report reads
its own, with #191 defaults. An unset environment reproduces #191; the four
names NEW_BACKEND, OLD_BACKEND, REFERENCE_ARM, QUESTION override it. The
configuration happens in main(), not at import, so importing this module has
no side effect on stack_agent_report's own defaults.

    uv run python scripts/stack_agent_report_191.py \\
        --ledger hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/results.jsonl \\
        --run-dir ~/bench-logs/191-mlx-ab
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import os
import pathlib
import statistics
import sys
from typing import Any

import stack_agent_report as rep

logger = logging.getLogger(__name__)


def configure() -> None:
    """Point stack_agent_report's arms at #191, from the environment.

    Called at the top of main(), not at import, so importing this module
    leaves stack_agent_report's own #138 defaults untouched. Each name falls
    back to its #191 value when unset, so an unset environment reproduces
    #191 exactly.
    """
    rep.NEW_BACKEND = os.environ.get("NEW_BACKEND", "qwen38fnmlxserve")
    rep.OLD_BACKEND = os.environ.get("OLD_BACKEND", "qwen38fnds4kimat")
    rep.REFERENCE_ARM = os.environ.get("REFERENCE_ARM", "the ds4 stack")
    rep.QUESTION = os.environ.get("QUESTION", "#191's agent question")
    rep.BACKENDS = {rep.NEW_BACKEND: "new", rep.OLD_BACKEND: "old"}


def passed_wall(rows: list[dict[str, Any]]) -> float | None:
    """Geometric-mean wall over PASSED trials only (#191 pre-registration).

    #138's task_wall includes wrong-code failures and timeouts -- their walls
    are long and real. #191 pre-registered the wall on tasks both arms PASSED,
    so a trial that did not pass contributes no wall here. A task with no
    passed trial has no wall, and pairs with nothing.
    """
    walls = [
        r.get("wall_seconds")
        for r in rows
        if rep.passes(r)
        and isinstance(r.get("wall_seconds"), (int, float))
        and r["wall_seconds"] > 0
    ]
    if not walls:
        return None
    return math.exp(statistics.fmean(math.log(w) for w in walls))


def pairs_by_task(sweeps: list[rep.Sweep]) -> list[tuple[str, float, float]]:
    """(task, wall_new, wall_old) for every task with a PASSED wall on both arms.

    The same pairing #138 uses, with the passed-only wall filter. A task that
    failed on either arm contributes no wall pair; it is fully counted in the
    pass and death tallies.
    """
    new: dict[str, list[dict[str, Any]]] = {}
    old: dict[str, list[dict[str, Any]]] = {}
    for sweep in sweeps:
        bucket = new if sweep.arm == "new" else old
        for row in sweep.rows:
            bucket.setdefault(row.get("task") or "?", []).append(row)
    for task, bucket in list(new.items()):
        new[task] = rep.results_mod.compatible_subset(bucket)
        rep.log_if_unknown(rep.NEW_BACKEND, task, new[task])
    for task, bucket in list(old.items()):
        old[task] = rep.results_mod.compatible_subset(bucket)
        rep.log_if_unknown(rep.OLD_BACKEND, task, old[task])
    out = []
    for task in sorted(new):
        if task in old:
            wn, wo = passed_wall(new[task]), passed_wall(old[task])
            if wn is not None and wo is not None:
                out.append((task, wn, wo))
    return out


def main(argv: list[str] | None = None) -> int:
    configure()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    collapsed = rep.check_arms()
    if collapsed:
        logger.info("%s", collapsed)
        return 2
    repo = pathlib.Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--ledger",
        type=pathlib.Path,
        default=repo
        / "hardware"
        / "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A"
        / "results.jsonl",
    )
    p.add_argument(
        "--run-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / "bench-logs" / "191-mlx-ab",
    )
    p.add_argument(
        "--cut",
        default=None,
        help="rows before this instant are not tonight's; default: the"
        " started line of run-record.txt in --run-dir",
    )
    p.add_argument(
        "--allow-harness-split",
        default="",
        metavar="REASON",
        help="pool rows taken at different harness_head values. Requires a "
        "reason, which is printed beside the override. Use ONLY when the diff "
        "between the heads provably touches nothing the benchmark executes. "
        "The default refusal is right: two harness heads are normally two "
        "harnesses.",
    )
    args = p.parse_args(argv)

    if args.cut is not None:
        cut = dt.datetime.fromisoformat(args.cut)
        logger.info("cut: %s (--cut)", args.cut)
    else:
        started_at = rep.run_started(args.run_dir)
        if started_at is None:
            logger.info(
                "VOID: %s has no parseable started line and no --cut given;"
                " refusing to guess which rows are tonight's",
                args.run_dir / "run-record.txt",
            )
            return 2
        cut = started_at
        logger.info("cut: %s (run-record.txt)", started_at.isoformat(sep=" "))

    raw = rep.load_raw(args.ledger, rep.BACKENDS, cut)
    per_backend = {b: sum(1 for r in raw if r["backend"] == b) for b in rep.BACKENDS}
    excluded = sum(1 for r in raw if r.get("excluded"))
    dry = sum(1 for r in raw if r.get("dry_run"))
    logger.info(
        "raw rows: %d (new %d, old %d); excluded %d; dry %d",
        len(raw),
        per_backend[rep.NEW_BACKEND],
        per_backend[rep.OLD_BACKEND],
        excluded,
        dry,
    )
    if excluded or dry:
        logger.info(
            "dropped rows are visible above, not inferred; they are"
            " holes in n, not passes or fails"
        )

    sweeps = rep.sweep_windows(args.run_dir)
    if sweeps is None:
        logger.info("VOID: sweep windows unavailable")
        return 2
    usable = [r for r in raw if not r.get("excluded") and not r.get("dry_run")]
    leftover = rep.assign(usable, sweeps)
    failures = rep.void_checks(raw, sweeps, leftover, args.allow_harness_split)
    for s in sweeps:
        logger.info(
            "sweep %-12s %s  rows %2d  %s",
            s.tag,
            s.start.strftime("%H:%M:%S"),
            len(s.rows),
            rep.tally(s.rows),
        )
    if failures:
        for f in failures:
            logger.info("VOID: %s", f)
        return 2
    logger.info("void checks: all pass")

    new = rep.tally([r for s in sweeps if s.arm == "new" for r in s.rows])
    old = rep.tally([r for s in sweeps if s.arm == "old" for r in s.rows])
    logger.info("new arm: %s", new)
    logger.info("old arm: %s", old)

    # Pass before wall, as pre-registered.
    passes = rep.pass_report(rep.pass_pairs(sweeps))
    logger.info(
        "paired pass, by TASK not by row: %d down, %d up, %d tied of %d"
        "  sign test p=%.3f",
        len(passes["down"]),
        len(passes["up"]),
        passes["ties"],
        passes["n_tasks"],
        passes["p"],
    )
    for task, pn, tn, po, to in rep.pass_pairs(sweeps):
        if pn != po:
            logger.info("    %-28s new %d/%d  old %d/%d", task, pn, tn, po, to)

    paired = pairs_by_task(sweeps)
    wall = rep.wall_report(paired)
    if wall["verdict"] != "OK":
        logger.info("wall endpoint: %s (n_pairs %d)", wall["verdict"], wall["n_pairs"])
    else:
        logger.info(
            "wall: n_pairs %d  ratio %.2f (95%% CI %.2f-%.2f)  median %.2f  "
            "win/loss/tie %d/%d/%d",
            wall["n_pairs"],
            wall["ratio"],
            wall["ci_lo"],
            wall["ci_hi"],
            wall["median_ratio"],
            wall["wins"],
            wall["losses"],
            wall["ties"],
        )
        logger.info("  per-task log ratios (new/old, passed trials only):")
        for task, wn, wo in paired:
            logger.info(
                "    %-28s %7.1f %7.1f  d %+0.3f", task, wn, wo, math.log(wn / wo)
            )

    for line in rep.screen_verdict(new, old, wall):
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
