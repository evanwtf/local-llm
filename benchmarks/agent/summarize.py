#!/usr/bin/env python3
"""Summarize results.jsonl into a table you can paste into a report.

Reports pass rate, median wall time and median turns per (task, backend), plus
a per-backend total. Medians rather than means: these runs have a fat right
tail -- an agent that thrashes can take five times as long as one that does
not, and a single such run drags a mean somewhere unrepresentative.

    uv run benchmarks/agent/summarize.py
    uv run benchmarks/agent/summarize.py --results other.jsonl --markdown

Rows whose control did not fail are discarded with a warning: the excision was
invisible to the test suite, so the trial proves nothing either way. Rows where
the agent edited the tests are counted as failures regardless of what pytest
said, and called out separately -- that is a cheat, not a pass.
"""

import argparse
import logging
import pathlib
import statistics
from collections import defaultdict

import provenance
import results

logger = logging.getLogger(__name__)

HERE = pathlib.Path(__file__).parent


def load(path):
    """Read rows, dropping the ones that cannot support a conclusion.

    Delegates to `results.py`, which is the only supported reader of
    results.jsonl. It used to filter exclusions here with `r.get("excluded")`,
    and missed the fourteen rows marked with the legacy `confound` and
    `contaminated` keys -- every published OpenCode number was computed over
    them. Do not reintroduce a hand-rolled filter (#29).

    A row is dropped when:
      - it is a --dry-run row, which records the control check and no agent;
      - its control did not fail, so the excision was invisible to the tests;
      - it is marked excluded by any of the keys `results.py` knows about.

    Excluded rows stay in results.jsonl. Deleting them would falsify the
    record; the reason travels with the data instead.
    """
    rows, discarded, retired, cheats = [], 0, 0, 0
    everything = results.load(path)
    retired = sum(1 for r in everything if r["excluded"] or r.get("dry_run"))
    for r in results.trials(path):
        if r.get("control_fails_as_expected") is False:
            discarded += 1
            continue
        if r.get("touched_tests"):
            cheats += 1
        # One place decides what a pass is; a timeout has no `passed` key and
        # must land here as False rather than vanish from the denominator.
        r["passed"] = results.verdict(r)
        rows.append(r)
    return rows, discarded, retired, cheats


def med(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def fmt(value, suffix="", nd=1):
    return "-" if value is None else f"{value:.{nd}f}{suffix}"


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--results", default=str(results.default_path()))
    p.add_argument("--markdown", action="store_true", help="emit a markdown table")
    args = p.parse_args()
    provenance.configure()
    # Captured, not discarded: the log path is reported at the end of main(),
    # the way hardware_id.py / report.py / hf_sweep.py all do. It was dropped in
    # 54fd3dd to clear ruff F841 -- correctly, at the time, because nothing used
    # it. Nothing used it because main() crashed before it could: `logger.info()`
    # with no argument, between the table and the totals. Fixing the crash makes
    # the variable live again.
    log_file = provenance.tee("summarize", machine_specific=True)
    provenance.banner(logger, engines=True)

    path = pathlib.Path(args.results)
    if not path.exists():
        raise SystemExit(f"no results at {path}")
    rows, discarded, retired, cheats = load(path)
    if not rows:
        raise SystemExit("no usable rows")

    if discarded:
        logger.info(f"WARNING: discarded {discarded} row(s) whose control did not fail")
    if retired:
        logger.info(
            f"note: {retired} row(s) set aside (marked excluded, or dry runs); "
            "see RESULTS.md provenance"
        )
    if cheats:
        logger.info(f"WARNING: {cheats} row(s) edited the tests; counted as failures")

    backends = sorted({r["backend"] for r in rows})
    tasks = list(dict.fromkeys(r["task"] for r in rows))
    by = defaultdict(list)
    for r in rows:
        by[(r["task"], r["backend"])].append(r)

    sep = " | " if args.markdown else "  "
    head = (
        ["task"] + [f"{b} pass" for b in backends] + [f"{b} median s" for b in backends]
    )
    if args.markdown:
        logger.info("| " + " | ".join(head) + " |")
        logger.info("|" + "|".join(["---"] * len(head)) + "|")
    else:
        logger.info(
            sep.join(f"{h:<22}" if i == 0 else f"{h:<14}" for i, h in enumerate(head))
        )

    for task in tasks:
        cells = [task]
        for b in backends:
            rs = by.get((task, b), [])
            cells.append(
                f"{sum(bool(r.get('passed')) for r in rs)}/{len(rs)}" if rs else "-"
            )
        for b in backends:
            rs = by.get((task, b), [])
            cells.append(fmt(med([r.get("wall_seconds") for r in rs]), "s"))
        if args.markdown:
            logger.info("| " + " | ".join(cells) + " |")
        else:
            logger.info(
                sep.join(
                    f"{c:<22}" if i == 0 else f"{c:<14}" for i, c in enumerate(cells)
                )
            )

    # A blank separator line. This was `print()` before the house-rules
    # conversion in 500491a; `logger.info()` with no message is a TypeError,
    # so every run crashed after emitting the table.
    logger.info("")
    for b in backends:
        rs = [r for r in rows if r["backend"] == b]
        passed = sum(bool(r.get("passed")) for r in rs)
        timeouts = sum(r.get("error") == "timeout" for r in rs)
        logger.info(
            f"{b:<6} {passed}/{len(rs)} passed"
            f"   median {fmt(med([r.get('wall_seconds') for r in rs]), 's')}"
            f"   median turns {fmt(med([r.get('num_turns') for r in rs]), '', 0)}"
            f"   timeouts {timeouts}"
        )

    # Every other script that tees its output says where it went --
    # `logger.info("log: %s", log_file)` in hardware_id.py, report.py,
    # hf_sweep.py. This one captured the path and never reported it, which is
    # the same broken tail: the crash above meant nothing after the table ran,
    # so the variable sat unused and the trailing comment claimed a behavior
    # that did not exist.
    logger.info("log: %s", log_file)


if __name__ == "__main__":
    main()
