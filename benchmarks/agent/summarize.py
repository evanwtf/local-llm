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
import json
import pathlib
import statistics
from collections import defaultdict

HERE = pathlib.Path(__file__).parent


def load(path):
    rows, discarded, cheats = [], 0, 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if not r.get("control_fails_as_expected"):
            discarded += 1
            continue
        if r.get("touched_tests"):
            cheats += 1
            r["passed"] = False
        rows.append(r)
    return rows, discarded, cheats


def med(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def fmt(value, suffix="", nd=1):
    return "-" if value is None else f"{value:.{nd}f}{suffix}"


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--results", default=str(HERE / "results.jsonl"))
    p.add_argument("--markdown", action="store_true", help="emit a markdown table")
    args = p.parse_args()

    path = pathlib.Path(args.results)
    if not path.exists():
        raise SystemExit(f"no results at {path}")
    rows, discarded, cheats = load(path)
    if not rows:
        raise SystemExit("no usable rows")

    if discarded:
        print(f"WARNING: discarded {discarded} row(s) whose control did not fail")
    if cheats:
        print(f"WARNING: {cheats} row(s) edited the tests; counted as failures")

    backends = sorted({r["backend"] for r in rows})
    tasks = list(dict.fromkeys(r["task"] for r in rows))
    by = defaultdict(list)
    for r in rows:
        by[(r["task"], r["backend"])].append(r)

    sep = " | " if args.markdown else "  "
    head = ["task"] + [f"{b} pass" for b in backends] + [f"{b} median s" for b in backends]
    if args.markdown:
        print("| " + " | ".join(head) + " |")
        print("|" + "|".join(["---"] * len(head)) + "|")
    else:
        print(sep.join(f"{h:<22}" if i == 0 else f"{h:<14}" for i, h in enumerate(head)))

    for task in tasks:
        cells = [task]
        for b in backends:
            rs = by.get((task, b), [])
            cells.append(f"{sum(bool(r.get('passed')) for r in rs)}/{len(rs)}" if rs else "-")
        for b in backends:
            rs = by.get((task, b), [])
            cells.append(fmt(med([r.get("wall_seconds") for r in rs]), "s"))
        if args.markdown:
            print("| " + " | ".join(cells) + " |")
        else:
            print(sep.join(f"{c:<22}" if i == 0 else f"{c:<14}" for i, c in enumerate(cells)))

    print()
    for b in backends:
        rs = [r for r in rows if r["backend"] == b]
        passed = sum(bool(r.get("passed")) for r in rs)
        timeouts = sum(r.get("error") == "timeout" for r in rs)
        print(
            f"{b:<6} {passed}/{len(rs)} passed"
            f"   median {fmt(med([r.get('wall_seconds') for r in rs]), 's')}"
            f"   median turns {fmt(med([r.get('num_turns') for r in rs]), '', 0)}"
            f"   timeouts {timeouts}"
        )


if __name__ == "__main__":
    main()
