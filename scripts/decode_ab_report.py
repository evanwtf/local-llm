"""Summarise a paired decode A/B produced by scripts/decode_ab.sh (#48).

Give it several directories and it also reports the spread BETWEEN runs
(#136). That axis is invisible from inside one run: on 2026-09-04 four
identical runs of the same A/B returned +16.5%, +21.2%, +17.6% and +17.7%,
and each looked tight from inside -- per-frontier ranges of 1.154-1.205 and
1.207-1.238, exactly the spread a reader would quote as precision. A single
run is not a measurement, and until this took more than one directory
nothing made that visible.

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


def report_across_runs(
    dirs: list[pathlib.Path], column: str
) -> tuple[list[tuple[pathlib.Path, Summary]], int]:
    """Summarise each run, then the spread between them (#136).

    Returns the per-run summaries and a status. A directory that cannot be
    summarised is named and skipped rather than aborting the others: with
    four runs in hand, losing three to one bad directory is the wrong
    trade.
    """
    got: list[tuple[pathlib.Path, Summary]] = []
    status = 0
    for d in dirs:
        try:
            got.append((d, summarize(load(d, column))))
        except (ValueError, OSError) as exc:
            logger.error("%s: %s", d, exc)
            status = 1
    return got, status


def repeat_spread(got: list[tuple[pathlib.Path, Summary]]) -> float | None:
    """Median spread across repetitions at a single frontier.

    This is the repeatability of the measurement -- what a re-run of the same
    thing should reproduce. Deliberately NOT the spread across frontiers,
    which reflects a real dependence on context length and would make any run
    look noisier than it is.
    """
    spreads: list[float] = []
    for d, _ in got:
        data = load(d, "gen_steady_tps")
        if len(data) != 2:
            continue
        a, b = sorted(data)
        for ctx in sorted(set(data[a]) & set(data[b])):
            shared = sorted(set(data[a][ctx]) & set(data[b][ctx]))
            if len(shared) < 2:
                continue
            ratios = [data[b][ctx][r] / data[a][ctx][r] for r in shared]
            spreads.append(max(ratios) - min(ratios))
    return st.median(spreads) if spreads else None


def log_between_run_spread(got: list[tuple[pathlib.Path, Summary]]) -> None:
    """The headline per run, and the spread across them."""
    if len(got) < 2:
        return
    medians = [s.median for _, s in got]
    lo, hi = min(medians), max(medians)
    logger.info("-- between runs --")
    for d, s in got:
        logger.info("%-42s %.3f  (%+.1f%%)", d.name, s.median, (s.median - 1) * 100)
    logger.info(
        "%d runs: median %.3f (%+.1f%%), range %.3f - %.3f, spread %.1f pp",
        len(medians),
        st.median(medians),
        (st.median(medians) - 1) * 100,
        lo,
        hi,
        (hi - lo) * 100,
    )
    # The comparison that matters is between-run spread against REPEAT noise,
    # not against the spread across frontiers. The ratio genuinely differs by
    # context length -- that is signal -- so comparing it to between-run
    # variation flatters the runs. Repeatability is the spread across reps at
    # one frontier, which is what a second run of the same thing should match.
    repeat = repeat_spread(got)
    if repeat is not None:
        logger.info(
            "typical within-run repeat spread at one frontier: %.1f pp -- %s",
            repeat * 100,
            "BETWEEN-run spread is larger, so one run's internal agreement is "
            "not precision (#136)"
            if (hi - lo) > repeat
            else "repeat noise dominates; runs agree as well as reps do",
        )


def per_rep_ratio(data: dict[str, dict[int, dict[int, float]]]) -> dict[int, float]:
    """Paired b/a per repetition: median over the frontiers in that rep.

    Answers "does the ratio narrow within a session", which we told
    antirez/ds4#952 that it does. Six datasets say otherwise, and re-deriving
    that by hand each time is how a ratio-of-medians slips back in.
    """
    if len(data) != 2:
        return {}
    a, b = sorted(data)
    out: dict[int, float] = {}
    reps = sorted({r for ctx in data[a].values() for r in ctx})
    for rep in reps:
        ratios = [
            data[b][ctx][rep] / data[a][ctx][rep]
            for ctx in sorted(set(data[a]) & set(data[b]))
            if rep in data[a].get(ctx, {})
            and rep in data[b].get(ctx, {})
            and data[a][ctx][rep]
        ]
        if ratios:
            out[rep] = st.median(ratios)
    return out


def per_arm_drift(data: dict[str, dict[int, dict[int, float]]]) -> dict[str, float]:
    """First rep to last, per arm: median of the per-frontier drift ratios.

    Deliberately per frontier and then medianed. Taking each rep's median
    across frontiers and dividing those is a ratio of medians -- the defect
    corrected in 98bc79b, which is easy to reintroduce here because the shape
    of the question invites it.

    This is the claim we made on antirez/ds4#952: "q4 loses more to drift than
    q8". On both datasets we can still recompute, q8 moves more.
    """
    out: dict[str, float] = {}
    for arm, by_ctx in data.items():
        reps = sorted({r for ctx in by_ctx.values() for r in ctx})
        if len(reps) < 2:
            continue
        first, last = reps[0], reps[-1]
        drifts = [
            by_ctx[ctx][last] / by_ctx[ctx][first]
            for ctx in sorted(by_ctx)
            if first in by_ctx[ctx] and last in by_ctx[ctx] and by_ctx[ctx][first]
        ]
        if drifts:
            out[arm] = st.median(drifts)
    return out


def log_within_run_structure(data: dict[str, dict[int, dict[int, float]]]) -> None:
    """Per-rep ratio and per-arm drift: the two questions #952 got wrong."""
    ratios = per_rep_ratio(data)
    if ratios:
        a, b = sorted(data)
        # Name the direction. The headline above prints both ways round for
        # exactly this reason -- a bare "0.872" has been misread once already
        # -- and printing one direction here reintroduced the ambiguity.
        logger.info(
            "paired ratio by rep, %s/%s: %s",
            b,
            a,
            "  ".join(f"rep{r}={v:.3f}" for r, v in sorted(ratios.items())),
        )
        logger.info(
            "paired ratio by rep, %s/%s: %s",
            a,
            b,
            "  ".join(f"rep{r}={1 / v:.3f}" for r, v in sorted(ratios.items())),
        )
    drift = per_arm_drift(data)
    if drift:
        # Per arm, so no direction to confuse -- but say which arm moved more,
        # because that is the claim ds4#952 made and it is easy to eyeball
        # backwards from two signed percentages.
        worst = max(drift, key=lambda k: abs(drift[k] - 1))
        logger.info("arm that drifts most: %s", worst)
        logger.info(
            "drift first->last rep, per arm: %s",
            "  ".join(f"{a}={(v - 1) * 100:+.1f}%" for a, v in sorted(drift.items())),
        )


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    dirs = [pathlib.Path(a) for a in argv[1:]] or [pathlib.Path.cwd()]
    status = 0
    # Both halves of the A/B claim: decode steady rate, and the prefill
    # interval rate. A PR can move one and not the other (#964 claims decode
    # only), so both get the paired treatment.
    for column in ("gen_steady_tps", "prefill_tps"):
        runs, run_status = report_across_runs(dirs, column)
        status = status or run_status
        if not runs:
            break
        logger.info("== %s ==", column)
        if len(runs) > 1:
            log_between_run_spread(runs)
            logger.info("")
        # Per-run detail below. With one directory this is the whole report.
        outdir, got = runs[0]
        data = load(outdir, column)
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
        log_within_run_structure(data)
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
