"""Read out the #112 strip-toggle A/B.

The experiment: does the shim removing its own `<tool_call>` scaffolding from
returned content change how often the tool-call degeneration loop happens?
`SHIM_NO_STRIP=1` is the off arm and toggles nothing else.

Two numbers come out of the same runs, and they are not equally strong:

**The conditional** -- the chance a tool call fails given >=1 prior tool error
in the same conversation -- is the pre-registered primary. It was chosen
because the analysis on #112 showed the outcome variable was out of reach at a
feasible trial count. It is a MECHANISM PROXY: the link from its slope to
actual trial deaths has never been measured, and that sentence belongs
wherever the number is quoted.

**The outcome** -- `solution_empty` deaths -- is reported second and was NOT
the pre-registered primary. It is here because the earlier power calculation
assumed a small effect, and a large one would show up here first. Read it as
the thing that motivates a properly designed follow-up, not as a result this
experiment was built to deliver.

    uv run python scripts/strip_ab_report.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import tool_error_conditional as tec

logger = logging.getLogger(__name__)

#: The bar from the pre-registration. Below it the answer is "could not tell",
#: whatever the arms happen to show.
MEANINGFUL_FAILURES = tec.MEANINGFUL_FAILURES


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]].

    Exact and dependency-free rather than a chi-square: the failure counts
    here are small enough that the approximation is the wrong tool, and
    scipy is not a dependency of this repo.
    """
    n = a + b + c + d
    row1, col1 = a + b, a + c

    def prob(x: int) -> float:
        return math.comb(row1, x) * math.comb(n - row1, col1 - x) / math.comb(n, col1)

    observed = prob(a)
    lo = max(0, col1 - (n - row1))
    hi = min(row1, col1)
    # Sum every table at least as extreme as the observed one, with a rounding
    # guard: exact arithmetic on floats would otherwise drop the observed
    # table itself when an equally likely arrangement differs in the last bit.
    return min(
        1.0,
        sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed * (1 + 1e-9)),
    )


def arm_calls(paths: list[pathlib.Path]) -> list[tec.Call]:
    out: list[tec.Call] = []
    for p in paths:
        out.extend(tec.calls(p.read_text()))
    return out


def split(items: list[tec.Call]) -> tuple[int, int, int, int]:
    """(clean failures, clean calls, after-error failures, after-error calls).

    "After an error" means at least one earlier failed call in the same
    session, ordered by timestamp. Sessions are kept separate: an error in one
    conversation says nothing about the next.
    """
    table = tec.conditional(items)
    clean_f, clean_n, after_f, after_n = 0, 0, 0, 0
    for prior, (failures, total) in table.items():
        if prior == 0:
            clean_f += failures
            clean_n += total
        else:
            after_f += failures
            after_n += total
    return clean_f, clean_n, after_f, after_n


def epoch(stamp: str) -> float | None:
    """Seconds since the epoch, from either clock this batch writes.

    `run.py` stamps a row with naive LOCAL time (`2026-09-06T03:26:46`) and
    the driver stamps the manifest in UTC (`...T07:26:35Z`). Comparing the two
    as strings put every row outside every window and mapped 118 of 120 rows
    to no arm at all. Returns None for anything unparseable rather than a
    guess, so a bad stamp shows up as unmapped instead of as an arm.
    """
    if not stamp:
        return None
    try:
        if stamp.endswith("Z"):
            return (
                datetime.datetime.strptime(stamp + "+0000", "%Y-%m-%dT%H:%M:%SZ%z")
            ).timestamp()
        return datetime.datetime.fromisoformat(stamp).astimezone().timestamp()
    except ValueError:
        return None


def arm_of(started: str, manifest: list[dict]) -> str | None:
    """Which arm was running when a trial started.

    By the run's own time window rather than by counting rows in order: a run
    that dies early writes fewer than fifteen rows, and a positional guess
    would then attribute every later row to the wrong arm -- silently, and in
    a way that reverses the result rather than weakening it.
    """
    when = epoch(started)
    if when is None:
        return None
    for entry in manifest:
        lo, hi = epoch(entry["started"]), epoch(entry["ended"])
        if lo is not None and hi is not None and lo <= when <= hi:
            return str(entry["arm"])
    return None


def outcomes(
    rows: list[dict], manifest: list[dict]
) -> tuple[dict[str, dict[str, int]], int]:
    """Per-arm trial counts, and how many rows matched no run window.

    The unmapped count is returned rather than dropped: a row in this file
    that belongs to no run of this batch means the file has picked up
    something else, and that is worth seeing before the numbers are read.
    """
    out: dict[str, dict[str, int]] = {}
    unmapped = 0
    for row in rows:
        arm = arm_of(str(row.get("started", "")), manifest)
        if arm is None:
            unmapped += 1
            continue
        acc = out.setdefault(arm, {"trials": 0, "passed": 0, "empty": 0})
        acc["trials"] += 1
        acc["passed"] += 1 if row.get("passed") else 0
        acc["empty"] += 1 if row.get("solution_empty") else 0
    return out, unmapped


def verdict(on_f: int, off_f: int, p: float) -> str:
    """The pre-registered sentences, chosen by the pre-registered rule."""
    if min(on_f, off_f) < MEANINGFUL_FAILURES:
        return (
            f"COULD NOT TELL -- {min(on_f, off_f)} failures in the smaller arm, "
            f"below the pre-registered bar of {MEANINGFUL_FAILURES}. No claim "
            "either way; the cell keeps the strip, marked "
            "implemented-and-unmeasured at the outcome level."
        )
    if p < 0.05 and off_f > on_f:
        return (
            "WORKS -- with the strip removed the conditional failure rate after "
            ">=1 prior error is higher than the strip-on arm on the same "
            "protocol. The scaffolding echo carries the loop; the strip stays."
        )
    if p < 0.05:
        return (
            "UNEXPECTED -- the arms differ, but in the direction opposite to the "
            "pre-registration. Do not narrate this as a result; re-measure it."
        )
    return (
        "DOES NOTHING -- the arms are indistinguishable at the pre-registered "
        "bar. The echo is not the carrier; the strip is scaffolding hygiene, "
        "not a fix, and item 2 closes as measured-no-effect."
    )


def arms_in(*sources: dict[str, object]) -> list[str]:
    """The arms present, with the #112 pair kept in its published order.

    Any other experiment's arms are sorted, so this reads out `targets_ab.sh`
    (#146) as well without pretending its arms are called on and off.
    """
    names = {k for source in sources for k in source}
    if names == {"on", "off"}:
        return ["on", "off"]
    return sorted(names)


def render(
    per_arm: dict[str, tuple[int, int, int, int]],
    outcome: dict[str, dict[str, int]],
) -> str:
    order = arms_in(per_arm, outcome)
    lines = [f"A/B read-out: {' vs '.join(order)}", ""]
    lines.append("conditional failure rate (the pre-registered primary)")
    lines.append(
        f"{'arm':>5}  {'clean context':>16}  {'after >=1 error':>17}  {'failures':>8}"
    )
    counts: dict[str, tuple[int, int]] = {}
    for arm in order:
        if arm not in per_arm:
            continue
        cf, cn, af, an = per_arm[arm]
        counts[arm] = (af, an)
        lines.append(
            f"{arm:>5}  {cf}/{cn} = {100 * cf / cn if cn else 0:5.1f}%  "
            f"{af}/{an} = {100 * af / an if an else 0:5.1f}%  {cf + af:>8}"
        )
    lines.append("")
    lines.append("trial outcomes (NOT the pre-registered primary -- see the docstring)")
    lines.append(f"{'arm':>5}  {'passed':>12}  {'solution_empty':>15}")
    for arm in order:
        if arm not in outcome:
            continue
        o = outcome[arm]
        lines.append(
            f"{arm:>5}  {o['passed']}/{o['trials']} = {100 * o['passed'] / o['trials']:4.1f}%  "
            f"{o['empty']:>15}"
        )
    lines.append("")
    if len(counts) == 2:
        first, second = order
        (a_f, a_n), (b_f, b_n) = counts[first], counts[second]
        p = fisher_exact(b_f, b_n - b_f, a_f, a_n - a_f)
        lines.append(
            f"after >=1 error, {second} vs {first}: Fisher exact two-sided p = {p:.4f}"
        )
        # The verdict sentences are #112's pre-registration. Another
        # experiment's arms have their own, written on their own issue, and
        # printing #112's over them would be worse than printing none.
        if order == ["on", "off"]:
            lines.append("")
            lines.append(
                verdict(
                    per_arm["on"][0] + per_arm["on"][2],
                    per_arm["off"][0] + per_arm["off"][2],
                    p,
                )
            )
    lines.append("")
    lines.append(
        "The conditional is a mechanism proxy. The link from its slope to "
        "actual trial deaths has never been measured."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    here = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--results", type=pathlib.Path, default=here / "results-112-strip-ab.jsonl"
    )
    p.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=here / "results-112-strip-ab-manifest.jsonl",
    )
    p.add_argument(
        "--bench-logs", type=pathlib.Path, default=pathlib.Path.home() / "bench-logs"
    )
    args = p.parse_args(argv)

    manifest = [
        json.loads(line) for line in args.manifest.read_text().splitlines() if line
    ]
    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line]

    per_arm: dict[str, tuple[int, int, int, int]] = {}
    for arm in ("on", "off"):
        dirs = [pathlib.Path(e["dir"]) for e in manifest if e["arm"] == arm]
        paths = [q for d in dirs for q in sorted(d.glob("*.jsonl"))]
        if not paths:
            continue
        logger.info("arm %s: %d transcripts from %d runs", arm, len(paths), len(dirs))
        per_arm[arm] = split(arm_calls(paths))

    outcome, unmapped = outcomes(rows, manifest)
    if unmapped:
        logger.warning(
            "%d row(s) in %s match no run window in the manifest",
            unmapped,
            args.results,
        )
    logger.info("\n%s", render(per_arm, outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
