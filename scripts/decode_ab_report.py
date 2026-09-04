"""Summarise a paired decode A/B produced by scripts/decode_ab.sh (#48).

Reports the per-frontier paired ratio, not just two medians: the frontiers
differ from each other by more than the effect we are chasing, so pooling them
would hide it. The paired median ratio is the statistic that answers "did
decode get faster", and the per-frontier spread says whether it held
everywhere or came from one point.

"Paired" means the ratio is taken **within one repetition** -- b's rep-2 rate
over a's rep-2 rate -- and the median is taken over those ratios. Until
2026-09-04 this script took each arm's median independently and divided one
by the other, which is a ratio of medians, not a paired statistic: the two
medians can come from different repetitions, and with ~9% rep-to-rep drift
(#118) the drift re-enters the result as noise. On #118's data that read
+20.0% where the paired statistic is +16.5%. The rep index is kept all the
way through for exactly this reason.
"""

from __future__ import annotations

import csv
import dataclasses
import logging
import pathlib
import statistics as st
import sys
from collections import defaultdict

logger = logging.getLogger(__name__)


def load(
    outdir: pathlib.Path, column: str = "gen_steady_tps"
) -> dict[str, dict[int, dict[int, float]]]:
    """label -> ctx_tokens -> rep -> the chosen CSV column's value.

    The rep number is the pairing key, so it lives in the structure rather
    than being discarded on load -- the original defect discarded it.
    """
    data: dict[str, dict[int, dict[int, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for path in sorted(outdir.glob("*-rep*.csv")):
        label, _, rep_name = path.name.rpartition("-rep")
        try:
            rep = int(rep_name.removesuffix(".csv"))
        except ValueError:
            logger.warning("skipping %s: cannot read a rep number", path.name)
            continue
        with path.open() as fh:
            for row in csv.DictReader(fh):
                data[label][int(row["ctx_tokens"])][rep] = float(row[column])
    return {label: dict(by_ctx) for label, by_ctx in data.items()}


@dataclasses.dataclass(frozen=True)
class Summary:
    a: str
    b: str
    # Paired median b/a per frontier, from the reps both arms share.
    per_frontier: dict[int, float]
    # Frontiers present in both arms but with no repetition in common.
    skipped: list[int]
    # Median of the per-frontier medians: the headline.
    median: float
    # The same ratios pooled over every (frontier, rep) pair, as a cross-check.
    pooled_median: float
    pooled_mean: float
    n_pairs: int
    wins: int


def summarize(data: dict[str, dict[int, dict[int, float]]]) -> Summary:
    """The paired statistics for exactly two labels, `a` sorted before `b`."""
    if len(data) != 2:
        raise ValueError(f"need exactly 2 labels, found {sorted(data)}")
    a, b = sorted(data)
    per_frontier: dict[int, float] = {}
    skipped: list[int] = []
    pooled: list[float] = []
    for ctx in sorted(set(data[a]) & set(data[b])):
        shared = sorted(set(data[a][ctx]) & set(data[b][ctx]))
        if not shared:
            skipped.append(ctx)
            continue
        ratios = [data[b][ctx][rep] / data[a][ctx][rep] for rep in shared]
        per_frontier[ctx] = st.median(ratios)
        pooled.extend(ratios)
    if not per_frontier:
        raise ValueError(
            "no frontier has a repetition in common between the arms; "
            "check the -rep numbering in the CSV filenames"
        )
    return Summary(
        a=a,
        b=b,
        per_frontier=per_frontier,
        skipped=skipped,
        median=st.median(per_frontier.values()),
        pooled_median=st.median(pooled),
        pooled_mean=st.mean(pooled),
        n_pairs=len(pooled),
        wins=sum(1 for r in per_frontier.values() if r > 1),
    )


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    outdir = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path.cwd()
    status = 0
    # Both halves of the A/B claim: decode steady rate, and the prefill
    # interval rate. A PR can move one and not the other (#964 claims decode
    # only), so both get the paired treatment.
    for column in ("gen_steady_tps", "prefill_tps"):
        data = load(outdir, column)
        try:
            got = summarize(data)
        except ValueError as exc:
            logger.error("%s", exc)
            status = 1
            break

        logger.info("== %s ==", column)
        # The arm medians are printed for context only; the ratio column is
        # the paired one and does not equal median(b) / median(a).
        logger.info("%-10s %10s %10s %14s", "ctx", got.a, got.b, "paired b/a")
        for ctx in sorted(got.per_frontier):
            ma = st.median(data[got.a][ctx].values())
            mb = st.median(data[got.b][ctx].values())
            logger.info(
                "%-10d %10.2f %10.2f %14.3f", ctx, ma, mb, got.per_frontier[ctx]
            )
        if got.skipped:
            logger.warning("skipped, no repetition in common: %s", got.skipped)

        logger.info("")
        # Name both directions. Labels sort alphabetically, so which arm
        # lands in the numerator is an accident of naming -- and a bare
        # "0.872" has already been misread once as the wrong way round.
        logger.info(
            "paired median %s/%s across frontiers: %.3f  (%+.1f%%)",
            got.b,
            got.a,
            got.median,
            (got.median - 1) * 100,
        )
        logger.info(
            "paired median %s/%s across frontiers: %.3f  (%+.1f%%)",
            got.a,
            got.b,
            1 / got.median,
            (1 / got.median - 1) * 100,
        )
        values = list(got.per_frontier.values())
        logger.info(
            "range of paired %s/%s across frontiers: %.3f - %.3f",
            got.b,
            got.a,
            min(values),
            max(values),
        )
        logger.info(
            "frontiers where %s > %s: %d of %d", got.b, got.a, got.wins, len(values)
        )
        logger.info(
            "pooled over all %d pairs: median %.3f (%+.1f%%), mean %.3f (%+.1f%%)",
            got.n_pairs,
            got.pooled_median,
            (got.pooled_median - 1) * 100,
            got.pooled_mean,
            (got.pooled_mean - 1) * 100,
        )
        logger.info("")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))