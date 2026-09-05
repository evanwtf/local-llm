"""Read out for the #138 stack A/B: two whole stacks, four sweeps, one screen.

Implements the pre-registered recipe from the 23:20 analysis (see the header
of scripts/stack_agent_ab.sh for the run and its pre-registration). The
judgement calls the spec left open are listed under JUDGEMENT CALLS below --
they are decisions this script makes so the 23:20 reader does not have to.

JUDGEMENT CALLS:

- VOID enforcement. Every void check runs before any statistic. A failing
  check prints its name and exits 2; the pre-registered sentences are printed
  only when nothing is void. A healthy old arm plus a broken new arm is a
  valid FAIL, never a void: the control check (old arm >= 25/30) is what
  distinguishes "the run measured nothing" from "the new stack is worse".
- The old-arm control floor is hard-coded at 25/30. Cohort 2, the baseline
  this screen leans on, scored 42/45 = 28/30 twice on 2026-09-03; 25 is the
  floor below which the session itself is suspect.
- Wall-eligible := not solution_empty. Turn-1 deaths and mid-session deaths
  are excluded from walls; wrong-code failures and timeouts are INCLUDED --
  their walls are long and real. A timeout wall is capped by the harness but
  it is the wall the trial actually took.
- A task pairs only if BOTH arms have >= 1 wall-eligible trial for it. A task
  that is dead in every trial of one arm contributes no wall pair and no
  imputed value; it is fully counted in the death tally.
- The primary wall statistic is the MEAN of per-task log ratios, not the
  median. With 15 pairs the median's interval comes from the sign test; it is
  printed as a robustness check only and must not replace the mean.
- n_pairs < 10 prints COULD NOT TELL for the wall endpoint regardless of the
  value of D. Hard-coded, not left to the reader.
- Sweep windows come from sweep-order.txt, whose lines carry a time of day
  but no date. The date is taken from run-record.txt's started line. A sweep
  that crosses midnight would be mis-windowed; no planned run does this.
- Scoping to tonight. Rows are cut at the started line of run-record.txt --
  the run's own record of when THIS launch began. run-record.txt is truncated
  per run, so a relaunch re-scopes automatically: the 20:57 launch died 18
  minutes in and its 8 rows were excluded as a run, the 21:27 relaunch
  overwrote the line, and no new flag was needed. --cut overrides; with
  neither the script refuses (exit 2) rather than guessing. Rows starting
  exactly at the cut instant are dropped; no trial can start in the seconds
  between the record line and the first server restart.
- Death split by num_turns: a row without num_turns counts as turn-1.
- The screen verdict assembles the three pre-registered indicators:
  completes >= 28/30 (not solution_empty), pass-count gap <= 4,
  exp(D) <= 1.25, deaths <= 3. Any fail-side indicator makes the screen FAIL;
  wall COULD NOT TELL leaves the verdict resting on pass and deaths, and says
  so. There is no separate null sentence here beyond that note: the screen's
  null was pre-registered as "no adoption claim", which is what a pass-side
  result also means for adoption.

    uv run python scripts/stack_agent_report.py \\
        --ledger hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/results.jsonl \\
        --run-dir ~/bench-logs/138-stack-ab
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import pathlib
import re
import statistics
import sys
from typing import Any

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import results as results_mod

logger = logging.getLogger(__name__)

NEW_BACKEND = "qwen38fnds4kimat"
OLD_BACKEND = "qwen38fnds4shim"
BACKENDS = {NEW_BACKEND: "new", OLD_BACKEND: "old"}
EXPECTED_PER_SWEEP = 15
#: Control floor for the old arm (cohort 2 scored 28/30 twice).
OLD_ARM_CONTROL = 25
#: Pre-registered screen bars (stack_agent_ab.sh header).
COMPLETES_FLOOR = 28  # of 30, new arm, not solution_empty
PASS_GAP_PASS_SIDE = 4  # absolute gap <= 4 is pass-side, >= 5 fail-side
WALL_RATIO_PASS_SIDE = 1.25  # exp(D) <= 1.25 pass-side
DEATH_SPIKE = 3  # new-arm deaths > 3 feed the fail side
TIE_BAND = math.log(1.05)  # |d_t| below this is a tie
MIN_PAIRS = 10  # below this the wall endpoint is COULD NOT TELL


def started(row: dict[str, Any]) -> dt.datetime | None:
    """Row start as naive local wall clock.

    Rows carry naive local stamps (results._now() writes none), and
    sweep-order.txt times are naive wall clock from `date +%H:%M:%S`. Naive
    local is the only frame both sides share; a --cut written with an offset
    is stripped of it, so it reads as local wall clock too.
    """
    raw = row.get("started")
    if not isinstance(raw, str):
        return None
    try:
        return dt.datetime.fromisoformat(raw).replace(tzinfo=None)
    except ValueError:
        return None


def load_raw(
    ledger: pathlib.Path, backends: dict[str, str], cut: dt.datetime
) -> list[dict[str, Any]]:
    """Tonight's raw rows for the two backends, before any filter."""
    out = []
    for line in ledger.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("backend") not in backends:
            continue
        when = started(row)
        if when is None or when < cut.replace(tzinfo=None):
            continue
        out.append(row)
    return out


def run_started(run_dir: pathlib.Path) -> dt.datetime | None:
    """The instant the run started, from run-record.txt's first line.

    run-record.txt is truncated per run (`} > "$OUT/run-record.txt"` in
    stack_agent_ab.sh), so the first line is the CURRENT run's start -- the
    right cut for scoping rows to this run. A relaunch overwrites the line,
    so a stale --cut default can never survive it.
    """
    record = run_dir / "run-record.txt"
    try:
        first = record.read_text(errors="replace").splitlines()[:1]
    except OSError:
        return None
    # The producer writes an ISO T (date '+%Y-%m-%dT%H:%M:%S %Z'); the space
    # form is accepted too because the first fixture used it. The producer
    # is the contract; the reader adapts to it.
    match = (
        re.search(r"(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}:\d{2}", first[0])
        if first
        else None
    )
    return dt.datetime.fromisoformat(match.group(0)) if match else None


def run_date(run_dir: pathlib.Path) -> dt.date | None:
    """The date the run started, from run-record.txt's first line."""
    started = run_started(run_dir)
    return started.date() if started else None


class Sweep:
    def __init__(self, tag: str, start: dt.datetime) -> None:
        self.tag = tag
        self.start = start
        self.arm = tag.rsplit("-sweep", 1)[0]
        self.backend = {arm: backend for backend, arm in BACKENDS.items()}[self.arm]
        self.rows: list[dict[str, Any]] = []


def sweep_windows(run_dir: pathlib.Path) -> list[Sweep] | None:
    """Sweeps in start order, windows from sweep-order.txt + the run date.

    A sweep owns [start, next sweep's start). The last window is open-ended.
    """
    date = run_date(run_dir)
    if date is None:
        logger.error("run-record.txt has no parseable started line: %s", run_dir)
        return None
    order = run_dir / "sweep-order.txt"
    try:
        lines = [ln for ln in record_lines(order) if ln and not ln.startswith("#")]
    except OSError:
        logger.error("no sweep-order.txt in %s", run_dir)
        return None
    if not lines:
        logger.error("sweep-order.txt is empty: %s", order)
        return None

    def at(tod: str) -> dt.datetime | None:
        try:
            hh, mm, ss = (int(x) for x in tod.split(":"))
            return dt.datetime.combine(date, dt.time(hh, mm, ss))
        except ValueError:
            return None

    sweeps: list[Sweep] = []
    # Two shapes. "tag start finish" is what stack_agent_ab.sh writes now.
    # "tag finish" is every run directory written before 2026-09-05, when the
    # file carried one time -- appended AFTER the sweep -- which this function
    # read as the sweep's START. Each window then held the next sweep's rows.
    # On the re-run that put 45 of 60 rows in no window at all and reported the
    # old-arm control as 14/30 when it was 27/30.
    legacy: list[tuple[str, dt.datetime]] = []
    for line in lines:
        parts = line.split()
        if len(parts) == 3:
            tag, start_s, _finish = parts
            start = at(start_s)
            if start is None:
                logger.error("unparsable time in sweep-order line: %r", line)
                return None
            sweeps.append(Sweep(tag, start))
        elif len(parts) == 2:
            tag, finish_s = parts
            finish = at(finish_s)
            if finish is None:
                logger.error("unparsable time in sweep-order line: %r", line)
                return None
            legacy.append((tag, finish))
        else:
            logger.error("unparsable sweep-order line: %r", line)
            return None
    if legacy and sweeps:
        logger.error("sweep-order.txt mixes one-time and two-time lines: %s", order)
        return None
    if legacy:
        # Each sweep ENDS at its recorded time, so it starts when the previous
        # one ended -- and the first starts when the run did.
        legacy.sort(key=lambda pair: pair[1])
        began = run_started(run_dir) or legacy[0][1]
        previous = began
        for tag, finish in legacy:
            sweeps.append(Sweep(tag, previous))
            previous = finish
    sweeps.sort(key=lambda s: s.start)
    return sweeps


def record_lines(path: pathlib.Path) -> list[str]:
    return path.read_text(errors="replace").splitlines()


def assign(rows: list[dict[str, Any]], sweeps: list[Sweep]) -> list[dict[str, Any]]:
    """Put each row into its sweep window; return rows that fit nowhere."""
    bounds = [s.start for s in sweeps] + [dt.datetime.max.replace(tzinfo=None)]
    leftover = []
    for row in rows:
        when = started(row)
        if when is None:
            leftover.append(row)
            continue
        for i, sweep in enumerate(sweeps):
            if sweep.start <= when < bounds[i + 1]:
                if sweep.backend == row.get("backend"):
                    sweep.rows.append(row)
                else:
                    leftover.append(row)
                break
        else:
            leftover.append(row)
    return leftover


def passes(row: dict[str, Any]) -> bool:
    """One place decides: results.verdict(), never row["passed"]."""
    return results_mod.verdict(row)


def guard_flips(rows: list[dict[str, Any]]) -> int:
    """Rows whose `passed` was true but a guard made verdict() false."""
    return sum(1 for r in rows if r.get("passed") and not passes(r))


def tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    dead = [r for r in rows if r.get("solution_empty")]
    turn1 = [r for r in dead if (r.get("num_turns") or 0) <= 1]
    return {
        "n": len(rows),
        "passes": sum(1 for r in rows if passes(r)),
        "deaths": len(dead),
        "deaths_turn1": len(turn1),
        "deaths_multi": len(dead) - len(turn1),
        "guard_flips": guard_flips(rows),
    }


def task_wall(rows: list[dict[str, Any]]) -> float | None:
    """Geometric-mean wall for one (task, arm) over wall-eligible trials."""
    walls = [
        r.get("wall_seconds")
        for r in rows
        if not r.get("solution_empty")
        and isinstance(r.get("wall_seconds"), (int, float))
        and r["wall_seconds"] > 0
    ]
    if not walls:
        return None
    return math.exp(statistics.fmean(math.log(w) for w in walls))


def pairs_by_task(sweeps: list[Sweep]) -> list[tuple[str, float, float]]:
    """(task, wall_new, wall_old) for every task with walls on both arms."""
    new: dict[str, dict[str, Any]] = {}
    old: dict[str, dict[str, Any]] = {}
    for sweep in sweeps:
        bucket = new if sweep.arm == "new" else old
        for row in sweep.rows:
            bucket.setdefault(row.get("task") or "?", []).append(row)
    out = []
    for task in sorted(new):
        if task in old:
            wn, wo = task_wall(new[task]), task_wall(old[task])
            if wn is not None and wo is not None:
                out.append((task, wn, wo))
    return out


def wall_report(paired: list[tuple[str, float, float]]) -> dict[str, Any]:
    d = [math.log(n / o) for _, n, o in paired]
    n = len(d)
    if n < MIN_PAIRS:
        return {"n_pairs": n, "verdict": "COULD NOT TELL", "d": d}
    mean = statistics.fmean(d)
    se = statistics.stdev(d) / math.sqrt(n) if n > 1 else float("nan")
    median = statistics.median(d)
    wins = sum(1 for x in d if x < -TIE_BAND)
    losses = sum(1 for x in d if x > TIE_BAND)
    ties = n - wins - losses
    return {
        "n_pairs": n,
        "d": d,
        "ratio": math.exp(mean),
        "ci_lo": math.exp(mean - 1.96 * se),
        "ci_hi": math.exp(mean + 1.96 * se),
        "median_ratio": math.exp(median),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "verdict": "OK",
    }


def void_checks(
    raw: list[dict[str, Any]], sweeps: list[Sweep], leftover: list[dict[str, Any]]
) -> list[str]:
    """Return the list of failing void checks; empty means not void."""
    failures = []
    per = {}
    for sweep in sweeps:
        per[sweep.tag] = len(sweep.rows)
    expected = len(sweeps) * EXPECTED_PER_SWEEP
    if len(raw) != expected:
        failures.append(f"raw count {len(raw)} != {expected}")
    for tag, n in sorted(per.items()):
        if n != EXPECTED_PER_SWEEP:
            failures.append(f"sweep {tag} has {n} rows, expected {EXPECTED_PER_SWEEP}")
    if leftover:
        failures.append(f"{len(leftover)} rows fit no sweep window or backend")
    by_arm: dict[str, set] = {}
    for s in sweeps:
        by_arm.setdefault(s.arm, set()).update(r.get("task") for r in s.rows)
    if len(by_arm) == 2 and by_arm["new"] != by_arm["old"]:
        failures.append("task sets differ between arms")
    envs = [r["env"] for r in raw if isinstance(r.get("env"), dict)]
    heads = {e.get("harness_head") for e in envs if e.get("harness_head")}
    if len(heads) > 1:
        failures.append(f"harness_head varies across rows: {sorted(heads)}")
    if any(e.get("harness_dirty") for e in envs):
        failures.append("harness_dirty row present")
    versions = {r.get("client_version") for r in raw}
    if len(versions) > 1 or versions - {"1.18.27"}:
        failures.append(f"client_version not uniformly 1.18.27: {sorted(versions)}")
    old_rows = [r for s in sweeps if s.arm == "old" for r in s.rows]
    old_passes = sum(1 for r in old_rows if passes(r))
    if old_rows and old_passes < OLD_ARM_CONTROL:
        failures.append(
            f"old-arm control {old_passes}/30 below floor {OLD_ARM_CONTROL}"
        )
    return failures


def screen_verdict(
    new: dict[str, int], old: dict[str, int], wall: dict[str, Any]
) -> list[str]:
    """The pre-registered sentences, assembled from the three indicators."""
    completes = new["n"] - new["deaths"]
    gap = abs(old["passes"] - new["passes"])
    indicators = {
        "completes": completes >= COMPLETES_FLOOR,
        "pass gap <= 4": gap <= PASS_GAP_PASS_SIDE,
        "deaths <= 3": new["deaths"] <= DEATH_SPIKE,
    }
    wall_ok: bool | None = None
    if wall["verdict"] == "OK":
        wall_ok = wall["ratio"] <= WALL_RATIO_PASS_SIDE
        indicators[f"wall ratio <= {WALL_RATIO_PASS_SIDE}"] = wall_ok
    lines = [
        (
            f"completes {completes}/{new['n']}, gap {gap}, "
            f"deaths {new['deaths']} ({new['deaths_turn1']} turn-1)"
        )
    ]
    lines += [
        f"  {'PASS-SIDE' if ok else 'FAIL-SIDE'}: {name}"
        for name, ok in indicators.items()
    ]
    if wall["verdict"] != "OK":
        lines.append(
            f"  wall endpoint: {wall['verdict']} (n_pairs {wall['n_pairs']} < {MIN_PAIRS});"
            " verdict rests on pass and death bars"
        )
    if all(indicators.values()):
        lines.append(
            "SCREEN PASS: the new stack integrates and regresses nothing"
            " catastrophically; book the paired 3+3 for a superiority claim."
        )
    else:
        lines.append(
            "SCREEN FAIL: hold the Q4_0 stack as the published reference;"
            " #138's agent question closes as a regression at screen resolution."
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
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
        default=pathlib.Path.home() / "bench-logs" / "138-stack-ab",
    )
    p.add_argument(
        "--cut",
        default=None,
        help="rows before this instant are not tonight's; default: the"
        " started line of run-record.txt in --run-dir",
    )
    args = p.parse_args(argv)

    if args.cut is not None:
        cut = dt.datetime.fromisoformat(args.cut)
        logger.info("cut: %s (--cut)", args.cut)
    else:
        started_at = run_started(args.run_dir)
        if started_at is None:
            logger.info(
                "VOID: %s has no parseable started line and no --cut given;"
                " refusing to guess which rows are tonight's",
                args.run_dir / "run-record.txt",
            )
            return 2
        cut = started_at
        logger.info("cut: %s (run-record.txt)", started_at.isoformat(sep=" "))

    raw = load_raw(args.ledger, BACKENDS, cut)
    per_backend = {b: sum(1 for r in raw if r["backend"] == b) for b in BACKENDS}
    excluded = sum(1 for r in raw if r.get("excluded"))
    dry = sum(1 for r in raw if r.get("dry_run"))
    logger.info(
        "raw rows: %d (new %d, old %d); excluded %d; dry %d",
        len(raw),
        per_backend[NEW_BACKEND],
        per_backend[OLD_BACKEND],
        excluded,
        dry,
    )
    if excluded or dry:
        logger.info(
            "dropped rows are visible above, not inferred; they are"
            " holes in n, not passes or fails"
        )

    sweeps = sweep_windows(args.run_dir)
    if sweeps is None:
        logger.info("VOID: sweep windows unavailable")
        return 2
    # Excluded and dry rows are holes in n, not passes or fails: they are
    # counted in the raw line above and dropped before assignment, so a hole
    # shows up as a short sweep cell, which void_checks refuses on.
    usable = [r for r in raw if not r.get("excluded") and not r.get("dry_run")]
    leftover = assign(usable, sweeps)
    failures = void_checks(raw, sweeps, leftover)
    for s in sweeps:
        logger.info(
            "sweep %-12s %s  rows %2d  %s",
            s.tag,
            s.start.strftime("%H:%M:%S"),
            len(s.rows),
            tally(s.rows),
        )
    if failures:
        for f in failures:
            logger.info("VOID: %s", f)
        return 2
    logger.info("void checks: all pass")

    new = tally([r for s in sweeps if s.arm == "new" for r in s.rows])
    old = tally([r for s in sweeps if s.arm == "old" for r in s.rows])
    logger.info("new arm: %s", new)
    logger.info("old arm: %s", old)

    paired = pairs_by_task(sweeps)
    wall = wall_report(paired)
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
        logger.info("  per-task log ratios (new/old geometric means):")
        for task, wn, wo in paired:
            logger.info(
                "    %-28s %7.1f %7.1f  d %+0.3f", task, wn, wo, math.log(wn / wo)
            )
    for line in screen_verdict(new, old, wall):
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
