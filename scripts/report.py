"""Summarize and compare measured cells, with the resolution rule applied.

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
    """{(backend, task): [row, ...]} for the backends asked for.

    Each cell is reduced to its largest server_argv-compatible subset (#213):
    a cell that mixes graph-changing server_argv is two different models, and
    pooling them would call the difference a result. The dropped rows are
    holes in n, not passes or fails.
    """
    got = collections.defaultdict(list)
    for r in rows:
        if r.get("client") != client:
            continue
        if backends and r.get("backend") not in backends:
            continue
        got[(r["backend"], r["task"])].append(r)
    for key, cell in list(got.items()):
        kept = results.compatible_subset(cell)
        if len(kept) != len(cell):
            logger.warning(
                "%s %s: dropped %d row(s) with a different server_argv graph",
                key[0],
                key[1],
                len(cell) - len(kept),
            )
        elif results.unknown_argv(kept):
            # All-unknown pools are allowed, but not silent: this cell may
            # span configurations and no row can say which (#213).
            logger.warning(
                "%s %s: %d row(s), none records server_argv;"
                " cannot verify they ran one configuration",
                key[0],
                key[1],
                len(kept),
            )
        got[key] = kept
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


def turns(rows) -> float | None:
    """Median turn count for a cell, or None when no row records one.

    A count of agent actions, carrying no timing. It needs no precondition to
    be readable, which is why it is the half of #353 that always applies: a
    treatment that moves it changed what the agent DID, and one that leaves it
    at 1.0 changed only how fast the engine went.
    """
    v = [r["num_turns"] for r in rows if r.get("num_turns")]
    return statistics.median(v) if v else None


def seconds_per_turn(rows) -> float | None:
    """Median seconds per turn, or None when it cannot be computed.

    Within one backend, wall time is mostly turn count -- r(num_turns,
    wall_seconds) = 0.932 over 90 rows of qwen38fnq3nothinkdgx, where wall
    spread is 25.0x and seconds-per-turn spread is 2.8x (#353). Dividing
    removes the count.

    It does NOT remove the per-turn size, which is why `homogeneous()` gates
    it: on a cell where a turn costs 6.9 s on one task and 47.9 s on another,
    the quotient is still carrying workload and a median over it describes
    nothing.
    """
    v = [
        r["wall_seconds"] / r["num_turns"]
        for r in rows
        if r.get("num_turns") and r.get("wall_seconds")
    ]
    return statistics.median(v) if v else None


#: Above this ratio between a cell's cheapest and dearest per-task turn,
#: seconds-per-turn is not isolating the engine and should not be read as
#: though it were. 2.0 is where the measured cells separate cleanly:
#: qwen38fnq3nothinkdgx sits at 1.6x and every vLLM NVFP4 cell at 3.9x or
#: worse, up to 8.4x (#353).
HOMOGENEITY_LIMIT = 2.0


def homogeneous(rows_by_task: dict) -> tuple[bool, float | None]:
    """(is seconds-per-turn readable here, the cell's own spread).

    Takes a cell split by task, because the question is whether a turn costs
    about the same whatever the task -- which is a property of the cell, not
    of the metric.
    """
    per_task = [s for rows in rows_by_task.values() if (s := seconds_per_turn(rows))]
    if len(per_task) < 2:
        return True, None
    spread = max(per_task) / min(per_task)
    return spread <= HOMOGENEITY_LIMIT, spread


def retries(rows):
    """(total prefill-failure retries, trials that recorded a count) for a cell.

    #266: an MTP arm re-prefills on an HTTP 500 (`prefill failed at position
    N`) the control arm never sees; the shim's retry usually succeeds, so the
    row reads `passed: true` and a reader sees a slightly slow arm, not a
    failing one. The extra re-prefill is cycles that do not draft, so it is a
    confound in every MTP-versus-control wall-time comparison and it moves the
    `drafting / cycles` denominator #235 reads.

    `prefill_failures` is stamped on the row by the harness
    (`benchmarks/agent/prefill_failures.Probe`). `None` means no server log was
    read -- unknown, not zero -- so a row without the field does not count as a
    measured zero. The return carries both the summed retries and how many rows
    carried a number, so a reader sees the count and that it was measured, not
    assumed: `0 across 3` is a real control result, `0 across 0` is unknown.
    """
    measured = [
        r["prefill_failures"] for r in rows if r.get("prefill_failures") is not None
    ]
    return sum(measured), len(measured)


def distinguishable(a: float, b: float) -> bool:
    """Whether two medians differ by enough for three trials to tell them apart."""
    if not a or not b:
        return False
    lo, hi = sorted((a, b))
    return (hi - lo) / lo >= RESOLUTION


def saturated_cells(by_cell, min_trials: int = 3):
    """Cells where every trial passed. Saturation is not excellence (#55 A4).

    A 100% cell says the task is too easy for this backend to fail, which is a
    property of the TASK -- not of the backend. Ranking backends on saturated
    tasks flatters whoever met the low bar first, and #4 exists because the
    whole task set is close to saturated.

    n=3 is the minimum reported here for the same reason #23 gives: below it,
    100% is 100% of a very small denominator. A 3/3 cell clears >37% by exact
    binomial, but not 90%; a 15/15 cell clears >85%. Both are worth flagging,
    with the caveat proportional to sample size.
    """
    saturated = []
    for (backend, task), rows in by_cell.items():
        if len(rows) < min_trials:
            continue
        n_passed = sum(1 for r in rows if results.verdict(r))
        if n_passed == len(rows):
            saturated.append((backend, task, len(rows)))
    return sorted(saturated)


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
        # #266: say the re-prefills this arm carried that a control arm does
        # not. Absolute count, never a ratio; silent when no row measured it.
        total, measured = retries(ex)
        if measured:
            out.append(
                f"  prefill-failure retries: {total} across {measured}/{len(ex)} "
                f"trials (#266)"
            )

    if len(backends) == 2:
        # #353: a wall ratio between two arms of the same model and engine is a
        # composite of an engine effect and a behavioural one, and wall time
        # alone cannot say which moved. Read turns first -- it is a count, so it
        # needs no precondition -- then the rates.
        a, b = backends
        rows_a = {t: by_cell.get((a, t), []) for t in tasks}
        rows_b = {t: by_cell.get((b, t), []) for t in tasks}
        flat_a = [r for rows in rows_a.values() for r in rows]
        flat_b = [r for rows in rows_b.values() for r in rows]
        ta, tb = turns(flat_a), turns(flat_b)
        sa, sb = seconds_per_turn(flat_a), seconds_per_turn(flat_b)
        ok_a, spread_a = homogeneous(rows_a)
        ok_b, spread_b = homogeneous(rows_b)
        if ta and tb:
            out += ["", "**What moved: the agent, or the engine? (#353)**", ""]
            out.append(f"- turns: {ta:.0f} -> {tb:.0f}, ratio **{tb / ta:.3f}**")
            if abs(tb / ta - 1) < 0.05:
                out.append(
                    "  - at ~1.0 the treatment did not change what the agent did, "
                    "so any wall difference is the engine"
                )
            else:
                out.append(
                    "  - away from 1.0 the treatment changed the agent's "
                    "BEHAVIOUR; a wall ratio here is not an engine measurement"
                )
            if sa and sb:
                gate = "readable" if (ok_a and ok_b) else "NOT readable"
                out.append(
                    f"- seconds/turn: {sa:.2f}s -> {sb:.2f}s, "
                    f"ratio **{sb / sa:.3f}** -- {gate}"
                )
                for name, ok, spread in ((a, ok_a, spread_a), (b, ok_b, spread_b)):
                    if spread and not ok:
                        out.append(
                            f"  - `{name}` per-task turn cost spans {spread:.1f}x "
                            f"(limit {HOMOGENEITY_LIMIT:.1f}x), so this quotient "
                            "still carries workload -- report the per-task "
                            "disagreement, not a median over it"
                        )

        out += ["", "**Can three trials tell them apart?**", ""]
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

        # #266: a wall-time A/B is not honest until it says the treatment arm
        # carried re-prefills the control did not. Report each arm's count so a
        # difference is visible beside the timing, not buried in a log.
        counts = []
        for arm in (a, b):
            ex = [
                r
                for (bb, task), rows in by_cell.items()
                if bb == arm and not task.startswith(SCRIPT_PREFIX)
                for r in rows
            ]
            total, measured = retries(ex)
            counts.append((arm, total, measured))
        if any(m for _, _, m in counts):
            out += ["", "**Prefill-failure retries (#266 confound):**", ""]
            for name, total, measured in counts:
                seen = f"{measured} trial(s) measured" if measured else "none measured"
                out.append(f"- **{name}**: {total} retries ({seen})")
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
    # normalizes `passed` through verdict() so a timeout lands as False rather
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
            "oracle output (#55):",
            len(untouched),
        )
        for backend, task, out in untouched:
            logger.warning("  %s %s: %s", backend, task, out[:80])

    # #55 A4: saturation is not excellence. A 100% cell says the task is too
    # easy for this backend to fail, which is a property of the task, not the
    # backend. Flagged as info so rankings do not read the wrong signal off it.
    saturated = saturated_cells(by_cell)
    if saturated:
        logger.info("")
        logger.info(
            "%d cell(s) at 100%% -- SATURATED, not necessarily excellent (#55 / #4):",
            len(saturated),
        )
        for backend, task, n in saturated:
            logger.info("  %s %s: %d/%d", backend, task, n, n)

    logger.info("log: %s", log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
