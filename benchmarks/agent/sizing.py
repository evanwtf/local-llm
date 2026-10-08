"""How many trials does a backend need? Issue #23.

The issue opened on a filter: applying ">90% pass rate with confidence" to the
2026-08-27 data left **nothing standing**. Nine combinations straddled 90% and
seven were confidently below.

That premise is now spent. `ds4 x claude` is 46/46 and `ds4anthropic x codex` is
36/36, and both clear 90% at 95% confidence. But #26 replaced it with a harder
question. Wall time within a single condition spreads 1.74x at the median and
reaches 7x, because the server samples at temperature 1.0 with a fresh seed per
request. So a three-trial median is not a measurement of speed, and this project
has published many of them.

Two different questions, two different answers:

**Pass rate** is cheap to bound and brutal about failures. An unbroken run has a
Wilson lower bound of exactly `n / (n + z^2)`, so 90% confidence needs 35
consecutive passes and 95% needs 73. One failure costs about twenty trials.

**Wall time** is bounded empirically, because the underlying distribution is
skewed and no closed form describes it. `median_precision` resamples a cell's
own observed times to answer: at n trials, how tightly is the median pinned?

    uv run python benchmarks/agent/sizing.py
"""

from __future__ import annotations

import argparse
import logging
import math
import pathlib
import random
import statistics

import provenance
import results
import variance

logger = logging.getLogger(__name__)

Z95 = 1.959963984540054


def _wilson(passes: int, trials: int, z: float) -> tuple[float, float]:
    p = passes / trials
    z2 = z * z
    centre = (p + z2 / (2 * trials)) / (1 + z2 / trials)
    half = (
        z
        * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
        / (1 + z2 / trials)
    )
    return centre, half


def wilson_lower(passes: int, trials: int, z: float = Z95) -> float:
    """Lower bound of the Wilson score interval.

    Wilson rather than the normal approximation because every interesting cell
    here sits at or near 100%, where the normal approximation gives a zero-width
    interval and claims certainty from fifteen trials.
    """
    if trials <= 0:
        return 0.0
    centre, half = _wilson(passes, trials, z)
    return max(0.0, centre - half)


def wilson_upper(passes: int, trials: int, z: float = Z95) -> float:
    """Upper bound of the Wilson score interval; 1.0 when there are no trials.

    The pair says whether two pass rates can be told apart: where two
    intervals overlap, the ranking between them is not established (#866).
    """
    if trials <= 0 or passes >= trials:
        return 1.0  # exact; the arithmetic lands a hair under it
    centre, half = _wilson(passes, trials, z)
    return min(1.0, centre + half)


def trials_for(target: float, z: float = Z95) -> int:
    """Consecutive passes needed before the lower bound clears `target`.

    Closed form: for an unbroken run the bound is n / (n + z^2), so
    n >= target * z^2 / (1 - target). Returned as the first integer that
    satisfies it.
    """
    if not 0 < target < 1:
        raise ValueError("target must be a probability")
    return math.ceil(target * z * z / (1 - target))


def more_passes_needed(passes: int, trials: int, target: float, z: float = Z95) -> int:
    """Consecutive passes still needed before this cell's lower bound clears
    `target`, counting its failures so far.

    `trials_for(target) - trials` assumed every trial so far had passed: 34/35
    sits at 0.855 and was reported as "needs 0 more" when it needs 18 (code
    review, 2026-10-05). The bound rises toward 1 with every added pass, so
    the search ends.
    """
    if not 0 < target < 1:
        raise ValueError("target must be a probability")
    more = 0
    while wilson_lower(passes + more, trials + more, z) < target:
        more += 1
    return more


def median_precision(
    samples: list[float], n: int, draws: int = 2000, seed: int = 20260828
) -> float | None:
    """How tightly do `n` trials pin the median? Returns a relative half-width.

    Resamples `n` values from the observed distribution, `draws` times, and
    reports half the 5th-95th percentile span of the resulting medians, divided
    by the median of the full sample. 0.20 means "a run of n trials lands within
    +/-20% of the true median, 90% of the time".

    Relative, not absolute: backends here differ tenfold in speed, and seconds
    would not compare across them.

    None when there are fewer than `n` observations to resample from -- an
    answer drawn from less data than it claims to model is worse than no answer.
    """
    if len(samples) < n or n < 1:
        return None
    rng = random.Random(seed)
    point = statistics.median(samples)
    if point <= 0:
        return None
    medians = sorted(statistics.median(rng.choices(samples, k=n)) for _ in range(draws))
    lo = medians[int(draws * 0.05)]
    hi = medians[int(draws * 0.95)]
    return (hi - lo) / 2 / point


def suite_precision(
    samples: list[float], tasks: int, n: int, draws: int = 2000, seed: int = 20260828
) -> float | None:
    """Same question for a whole suite, which is what RESULTS.md actually reports.

    A suite total is the sum of one median per task, so it averages over `tasks`
    independent draws and is tighter than any single task's median by roughly
    the square root of that count. Reporting the per-task figure as though it
    applied to the suite would overstate the noise and retire findings that are
    real.
    """
    if len(samples) < n or n < 1 or tasks < 1:
        return None
    rng = random.Random(seed)
    totals = sorted(
        sum(statistics.median(rng.choices(samples, k=n)) for _ in range(tasks))
        for _ in range(draws)
    )
    point = statistics.median(totals)
    if point <= 0:
        return None
    return (totals[int(draws * 0.95)] - totals[int(draws * 0.05)]) / 2 / point


def report(path: pathlib.Path) -> None:
    rows = results.trials(path)

    logger.info("=== pass rate: consecutive passes needed ===")
    for target in (0.80, 0.90, 0.95, 0.99):
        logger.info(
            "  %.0f%% confident of >%.0f%%: %d trials",
            95,
            target * 100,
            trials_for(target),
        )

    logger.info("=== where each measured combination stands ===")
    cells: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        cells.setdefault((r["backend"], r.get("client") or "claude"), []).append(r)
    ranked = []
    for (backend, client), cell in cells.items():
        n = len(cell)
        k = sum(results.verdict(r) for r in cell)
        ranked.append((wilson_lower(k, n), backend, client, k, n))
    for lower, backend, client, k, n in sorted(ranked, reverse=True):
        need = (
            ""
            if lower >= 0.90
            else f"  (needs {more_passes_needed(k, n, 0.90)} more if unbroken)"
        )
        logger.info(
            "  %-14s x %-8s %3d/%-3d  lower bound %.3f%s",
            backend,
            client,
            k,
            n,
            lower,
            need,
        )

    logger.info("=== wall time: how much does a trial count buy? ===")
    grouped = variance.cells(rows)
    pooled: list[float] = []
    for cell in grouped.values():
        times = [r["wall_seconds"] for r in cell if r.get("wall_seconds")]
        if len(times) >= 6:
            med = statistics.median(times)
            pooled.extend(t / med for t in times)  # normalize, then pool
    logger.info(
        "  pooled from %d observations across cells with >=6 trials", len(pooled)
    )
    logger.info("  per task, and for a 5-task suite total:")
    for n in (3, 5, 10, 20, 35):
        one = median_precision(pooled, n)
        suite = suite_precision(pooled, 5, n)
        if one is not None and suite is not None:
            logger.info(
                "  n=%-3d task median +/- %4.1f%%   suite total +/- %4.1f%%",
                n,
                one * 100,
                suite * 100,
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # This machine's ledger. The old default, benchmarks/agent/results.jsonl,
    # no longer exists, and an absent ledger reads as no history at all.
    p.add_argument("--results", default=None, help="default: this machine's ledger")
    args = p.parse_args(argv)
    provenance.configure()
    report(pathlib.Path(args.results) if args.results else results.default_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
