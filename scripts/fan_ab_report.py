#!/usr/bin/env python3
"""Read a `fan_ab.py` run: does forced cooling change anything? #276 (parent #116)

Two questions, and they are not the same finding:

- **throughput** -- does fan mode change tok/s at all?
- **within-phase drift** -- first rep to last, inside one phase. #274 measured
  decode falling 4.2-8.4% and prefill 8.5-14.7% within a single run, and 5-11%
  across 24 minutes of continuous work. If forced cooling flattens that, every
  paired A/B on this machine gets cheaper. Reporting only a median answers the
  first question and misses the second.

## What it refuses to do

**It prints absolutes before it prints any ratio.** A ratio is a quotient of
two numbers it then discards, and on #274 the discarded half was the larger
finding: paired ratios read 1.002 / 1.001 / 1.006 -- flat -- while the
absolutes underneath fell 567.71 -> 527.98 -> 507.57 tok/s. Every derived
figure here has its inputs in the same output.

**It medians the per-frontier ratios; it never divides two medians.** That is
the defect corrected in 98bc79b, and the shape of the question invites it back
every time somebody writes one of these.

**It pairs phases positionally.** The run is A,B,A,B,A,B, so (1,2), (3,4) and
(5,6) are adjacent in time and a linear drift cancels inside each pair. Taking
all A against all B does not cancel it -- that is the whole reason the run
interleaves rather than running three of each.

**It reports ambient as given, and never invents it.** The office is
independently air-conditioned and the room moved during the first attempt at
this run. Absolute die and absolute ambient are both printed; DeltaT is
computed only where both exist for the same phase.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import pathlib
import statistics as st
import sys
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import decode_ab_report

import logs

logger = logging.getLogger(__name__)

DECODE = "gen_steady_tps"
PREFILL = "prefill_tps"


def condition_of(label: str) -> str | None:
    """`01-auto` -> `auto`. None when the label is not a phase directory name."""
    _, _, rest = label.partition("-")
    return rest if rest in ("auto", "max") else None


def index_of(label: str) -> int | None:
    head, _, _ = label.partition("-")
    try:
        return int(head)
    except ValueError:
        return None


def phase_order(data: dict[str, dict[int, dict[int, float]]]) -> list[str]:
    """Phase labels in run order. Anything unparseable is dropped, loudly."""
    out = []
    for label in data:
        if index_of(label) is None or condition_of(label) is None:
            logger.warning("ignoring %r: not a phase label like '01-auto'", label)
            continue
        out.append(label)
    return sorted(out, key=lambda s: index_of(s) or 0)


def drop_partial_reps(
    data: dict[str, dict[int, dict[int, float]]],
) -> dict[str, dict[int, dict[int, float]]]:
    """Remove reps that cover fewer frontiers than their phase's fullest rep.

    A killed run leaves a truncated CSV, and it is always the LAST rep -- which
    is exactly the rep `per_arm_drift` divides by the first. The aborted run of
    2026-09-09 wrote a rep 2 with 4 frontiers against rep 1's 8, and the drift
    came out -5.5% from a rep that never finished. Nothing about the number
    looked wrong.

    Frontier count is the test, not file size: a sweep writes one row per
    frontier and stops where it stopped.
    """
    out: dict[str, dict[int, dict[int, float]]] = {}
    for label, by_ctx in data.items():
        counts: dict[int, int] = {}
        for reps in by_ctx.values():
            for rep in reps:
                counts[rep] = counts.get(rep, 0) + 1
        if not counts:
            out[label] = by_ctx
            continue
        full = max(counts.values())
        partial = {rep for rep, n in counts.items() if n < full}
        if partial:
            logger.warning(
                "%s: dropping rep(s) %s -- %s frontiers against %d for a full "
                "rep; a truncated rep is usually the last one, which is the "
                "one drift divides by",
                label,
                sorted(partial),
                sorted(counts[r] for r in partial),
                full,
            )
        out[label] = {
            ctx: {r: v for r, v in reps.items() if r not in partial}
            for ctx, reps in by_ctx.items()
        }
        out[label] = {ctx: reps for ctx, reps in out[label].items() if reps}
    return out


def frontier_medians(by_ctx: dict[int, dict[int, float]]) -> dict[int, float]:
    """ctx -> median across this phase's reps. Per frontier, never pooled."""
    return {ctx: st.median(reps.values()) for ctx, reps in by_ctx.items() if reps}


def pairs(labels: Sequence[str]) -> list[tuple[str, str]]:
    """Adjacent (auto, max) phases, in order. Positional, so drift cancels.

    A pair is only formed from two phases that differ in condition. A run that
    was cut short mid-phase, or one whose order was edited, produces fewer
    pairs rather than a mismatched one.
    """
    out = []
    for first, second in itertools.pairwise(labels):
        a, b = condition_of(first), condition_of(second)
        if a == "auto" and b == "max":
            out.append((first, second))
    return out


def paired_ratio(
    data: dict[str, dict[int, dict[int, float]]], auto: str, mx: str
) -> float | None:
    """median over frontiers of max/auto. A median of ratios, not a ratio of
    medians -- see the module docstring."""
    left, right = (
        frontier_medians(data.get(auto, {})),
        frontier_medians(data.get(mx, {})),
    )
    shared = sorted(set(left) & set(right))
    ratios = [right[c] / left[c] for c in shared if left[c]]
    return st.median(ratios) if ratios else None


def manifest_of(outdir: pathlib.Path) -> dict[str, object] | None:
    path = outdir / "fan-ab-manifest.json"
    if not path.exists():
        logger.warning("no manifest at %s -- temperatures unavailable", path)
        return None
    try:
        got = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("manifest unreadable (%s); temperatures unavailable", exc)
        return None
    return got if isinstance(got, dict) else None


def cooldown_outcomes(manifest: dict[str, object] | None) -> dict[int, str]:
    """phase index -> how its cooldown ended.

    A phase preceded by a `timeout` began on a machine still shedding heat and
    is not comparable to one preceded by a `plateau`. The report says so rather
    than letting a reader assume every wait succeeded.
    """
    if not manifest:
        return {}
    out = {}
    for phase in manifest.get("phases") or []:
        if not isinstance(phase, dict):
            continue
        index, cooled = phase.get("phase"), phase.get("cooldown")
        if isinstance(index, int) and isinstance(cooled, dict):
            out[index] = str(cooled.get("outcome", "?"))
    return out


def start_die(manifest: dict[str, object] | None) -> dict[int, float]:
    if not manifest:
        return {}
    out = {}
    for phase in manifest.get("phases") or []:
        if not isinstance(phase, dict):
            continue
        index, die = phase.get("phase"), phase.get("start_die_c")
        if isinstance(index, int) and isinstance(die, int | float):
            out[index] = float(die)
    return out


def log_absolutes(
    data: dict[str, dict[int, dict[int, float]]], labels: Sequence[str], column: str
) -> None:
    """Every phase's per-frontier median. The inputs to everything below."""
    logger.info("--- %s, absolute tok/s per frontier (median of reps) ---", column)
    frontiers = sorted({c for label in labels for c in data.get(label, {})})
    logger.info("ctx        %s", "  ".join(f"{label:>10}" for label in labels))
    for ctx in frontiers:
        cells = []
        for label in labels:
            value = frontier_medians(data.get(label, {})).get(ctx)
            cells.append(f"{value:>10.2f}" if value is not None else f"{'-':>10}")
        logger.info("%-9d %s", ctx, "  ".join(cells))


def log_drift(
    data: dict[str, dict[int, dict[int, float]]], labels: Sequence[str], column: str
) -> None:
    """First rep to last, per phase. The finding a median hides."""
    drift = decode_ab_report.per_arm_drift(data)
    logger.info("--- %s, within-phase drift (last rep / first rep) ---", column)
    for label in labels:
        got = drift.get(label)
        if got is None:
            logger.info("  %-10s  (needs two reps)", label)
            continue
        logger.info("  %-10s  %.4f  (%+.1f%%)", label, got, (got - 1.0) * 100.0)


def report(outdir: pathlib.Path, column: str) -> int:
    data = decode_ab_report.load(outdir, column)
    if not data:
        logger.error("no *-rep*.csv under %s", outdir)
        return 2
    data = drop_partial_reps(data)
    labels = phase_order(data)
    if not labels:
        logger.error("no phase-shaped labels under %s", outdir)
        return 2

    manifest = manifest_of(outdir)
    cooled, die = cooldown_outcomes(manifest), start_die(manifest)

    logger.info("=== %s :: %s ===", outdir, column)
    log_absolutes(data, labels, column)

    logger.info("--- how each phase started ---")
    for label in labels:
        index = index_of(label) or 0
        logger.info(
            "  %-10s  cooldown=%-9s start_die=%s",
            label,
            cooled.get(index, "unrecorded"),
            f"{die[index]:.2f}C" if index in die else "unrecorded",
        )
    if len(die) >= 2:
        values = [die[i] for i in sorted(die)]
        spread = max(values) - min(values)
        # The gate's own audit. It exists to make phases start from equivalent
        # thermal states; this is whether it did. A wide spread means the
        # comparison rests on phases that were not alike at t=0, whatever
        # every cooldown reported about itself.
        logger.info(
            "  start_die spread across %d phases: %.2fC (min %.2f, max %.2f)",
            len(values),
            spread,
            min(values),
            max(values),
        )
        if spread > 3.0:
            logger.warning(
                "start_die spread %.2fC is wide -- the phases did not begin "
                "from equivalent thermal states, so a difference between "
                "conditions may be a difference between starting points",
                spread,
            )
    if any(v == "timeout" for v in cooled.values()):
        logger.warning(
            "at least one phase began after a cooldown TIMEOUT -- it started on "
            "a machine still shedding heat and is not comparable to a phase "
            "that began from a plateau"
        )

    log_drift(data, labels, column)

    logger.info("--- paired, adjacent in time (max / auto) ---")
    found = pairs(labels)
    if not found:
        logger.warning("no adjacent auto->max pair; nothing to compare")
    for auto, mx in found:
        ratio = paired_ratio(data, auto, mx)
        if ratio is None:
            logger.info("  %s vs %s: no shared frontier", auto, mx)
            continue
        logger.info(
            "  %-10s -> %-10s  %.4f  (%+.1f%%)",
            auto,
            mx,
            ratio,
            (ratio - 1.0) * 100.0,
        )
    values = [r for r in (paired_ratio(data, a, m) for a, m in found) if r is not None]
    if values:
        logger.info(
            "  median of %d pairs: %.4f (%+.1f%%); spread %.1f pp",
            len(values),
            st.median(values),
            (st.median(values) - 1.0) * 100.0,
            (max(values) - min(values)) * 100.0,
        )
    if len(values) < 3:
        logger.warning(
            "%d pair(s): fewer than the three this repo requires before a claim",
            len(values),
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("outdir", type=pathlib.Path)
    p.add_argument("--column", default=None, help="default: both decode and prefill")
    args = p.parse_args(argv)
    logs.configure()
    outdir = args.outdir.expanduser().resolve()
    columns = [args.column] if args.column else [DECODE, PREFILL]
    rc = 0
    for column in columns:
        rc = report(outdir, column) or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
