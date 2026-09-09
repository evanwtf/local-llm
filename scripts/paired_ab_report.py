#!/usr/bin/env python3
"""Read out a paired two-arm A/B from the ledger. #240

There are five report scripts in `scripts/` -- `decode_ab_report`,
`route_ab_report`, `strip_ab_report`, `stack_agent_report`,
`stack_agent_report_191` -- totalling 2,911 lines with **no shared code**.
Each re-implements ledger loading, arm selection, pairing by task, wall
statistics, and the void checks. That is the same duplication as the twelve
shell drivers in #235, and it has the same consequence: a lesson learned in
one read-out does not reach the other four.

This is the general one. It takes a batch and two arm names:

    uv run python scripts/paired_ab_report.py \
        --batch greedy-mtp-ab-paired \
        --treatment qwen38fnds4mtp7greedy --control qwen38fnds4greedy

## The void checks run before any statistic

A number computed on rows that should never have been pooled is worse than no
number, because it is quotable. Each check prints its name and refuses.

- **One harness head.** Two heads are normally two harnesses. `stack_agent_report`
  already refuses this and takes a written reason to override; that refusal is
  reproduced here rather than re-invented a sixth time.
- **`harness_dirty` symmetric across arms.** New, and from 2026-09-08: reading
  `peer_status.py` wrote `.claude/peer/status.json` into the tree mid-run, and
  the flag flipped at exactly the treatment/control boundary -- 15 clean
  treatment rows, then a dirty control arm. `harness_head` was identical, so
  the code provably did not change, but no existing read-out would have
  mentioned it. A flag that differs across precisely the comparison it
  qualifies must be said out loud, not silently averaged over (#238).
- **Both arms present, and paired by task.** A task counts only when both arms
  produced a wall-eligible trial for it. An unpaired task is dropped and named.

## Wall eligibility

Wall-eligible := the trial produced a solution. A turn-1 death has a short wall
that measures the harness giving up, not the model working. Wrong-code failures
and timeouts are **included**: their walls are long and real.

## What it does not do

It does not decide whether the effect is real. It prints the paired ratio, a
bootstrap interval, the per-task direction and the counts behind them. Three
datapoints is this project's minimum for a claim and one run is not three.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import random
import statistics
import sys

logger = logging.getLogger(__name__)

BOOTSTRAP = 10000
SEED = 20260909


def load(path: pathlib.Path, batch: str) -> list[dict]:
    """Rows of one batch that were not excluded."""
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            logger.warning("skipping a line that is not JSON")
            continue
        if row.get("batch") == batch and not row.get("excluded"):
            rows.append(row)
    return rows


def wall_eligible(row: dict) -> bool:
    """Did the trial produce a solution? A turn-1 death did not.

    `solution_empty` is None on rows whose harness predates the field. None is
    treated as eligible: dropping a row because an older harness did not record
    the flag would silently shrink an arm.
    """
    return not row.get("solution_empty")


def check_one_harness_head(rows: list[dict]) -> str | None:
    heads = {r["env"].get("harness_head") for r in rows}
    if len(heads) > 1:
        return f"VOID: rows span {len(heads)} harness heads: {sorted(map(str, heads))}"
    return None


def dirty_by_arm(rows: list[dict], arms: tuple[str, str]) -> dict[str, set[bool]]:
    return {
        arm: {bool(r["env"].get("harness_dirty")) for r in rows if r["backend"] == arm}
        for arm in arms
    }


def check_dirty_symmetry(rows: list[dict], arms: tuple[str, str]) -> str | None:
    """Warn when the arms disagree about harness_dirty. Not a void by itself.

    The flag says the working tree had uncommitted changes, which is a fact
    about the tree and not necessarily about the code that ran -- 2026-09-08's
    split came from an agent status file. But an asymmetry across exactly the
    two arms being compared is the reader's business, so it is always printed.
    """
    seen = dirty_by_arm(rows, arms)
    if seen[arms[0]] != seen[arms[1]]:
        return (
            f"harness_dirty differs across the arms: "
            f"{arms[0]}={sorted(seen[arms[0]])} {arms[1]}={sorted(seen[arms[1]])}. "
            "The heads agree, so the harness code was identical; say so "
            "explicitly when quoting this run (#238)."
        )
    return None


def pair(rows: list[dict], arms: tuple[str, str]) -> tuple[dict, list[str]]:
    """{task: (treatment_median, control_median)} and the unpaired task names."""
    walls: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        if row["backend"] not in arms or not wall_eligible(row):
            continue
        seconds = row.get("wall_seconds")
        if seconds is None:
            continue
        walls.setdefault(row["task"], {}).setdefault(row["backend"], []).append(
            float(seconds)
        )
    paired, unpaired = {}, []
    for task, by_arm in sorted(walls.items()):
        if all(by_arm.get(arm) for arm in arms):
            paired[task] = tuple(statistics.median(by_arm[arm]) for arm in arms)
        else:
            unpaired.append(task)
    return paired, unpaired


def ratio_ci(
    ratios: list[float], reps: int = BOOTSTRAP, seed: int = SEED
) -> tuple[float, float]:
    """Percentile bootstrap interval for the median of per-task ratios."""
    rng = random.Random(seed)
    n = len(ratios)
    medians = sorted(
        statistics.median([ratios[rng.randrange(n)] for _ in range(n)])
        for _ in range(reps)
    )
    return medians[int(0.025 * reps)], medians[int(0.975 * reps)]


def render(rows: list[dict], arms: tuple[str, str]) -> tuple[list[str], int]:
    """(lines, exit code). 2 when a void check refuses."""
    out: list[str] = []
    void = check_one_harness_head(rows)
    if void:
        return [void], 2

    for arm in arms:
        n = [r for r in rows if r["backend"] == arm]
        passed = sum(1 for r in n if r.get("passed"))
        out.append(f"{arm:26} {len(n):3} rows  {passed:3} passed")
    if not all(any(r["backend"] == arm for r in rows) for arm in arms):
        return out + ["VOID: an arm produced no rows at all"], 2

    warning = check_dirty_symmetry(rows, arms)
    if warning:
        out.append(f"NOTE: {warning}")

    paired, unpaired = pair(rows, arms)
    if unpaired:
        out.append(f"unpaired, dropped: {', '.join(unpaired)}")
    if not paired:
        return out + ["VOID: no task has a wall-eligible trial in both arms"], 2

    ratios = [t / c for t, c in paired.values()]
    faster = sum(1 for r in ratios if r < 1.0)
    lo, hi = ratio_ci(ratios)
    out.append("")
    out.append(f"{'task':32} {arms[0][:12]:>12} {arms[1][:12]:>12}  ratio")
    for task, (t, c) in paired.items():
        out.append(f"{task:32} {t:12.1f} {c:12.1f}  {t / c:.3f}")
    out.append("")
    out.append(
        f"paired tasks {len(paired)}; median wall ratio "
        f"{statistics.median(ratios):.3f} (95% CI {lo:.3f}-{hi:.3f}); "
        f"{faster} of {len(paired)} favor {arms[0]}"
    )
    out.append(
        "One run is not three. This project's minimum for a claim is three "
        "datapoints; treat this as one."
    )
    return out, 0


def main(argv: list[str] | None = None) -> int:
    repo = pathlib.Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", required=True)
    p.add_argument("--treatment", required=True)
    p.add_argument("--control", required=True)
    p.add_argument(
        "--ledger",
        type=pathlib.Path,
        default=repo
        / "hardware"
        / "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A"
        / "results.jsonl",
    )
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    rows = load(args.ledger, args.batch)
    if not rows:
        logger.error("no rows in batch %r", args.batch)
        return 2
    lines, code = render(rows, (args.treatment, args.control))
    for line in lines:
        logger.info("%s", line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
