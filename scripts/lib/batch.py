"""The batch loop `targets_ab.sh` and `strip_toggle_ab.sh` both run. #235

Two experiments, one protocol: 15 tasks x 1 trial per run, the model server
restarted before each, arms alternating A B B A, one harness commit throughout,
rows to their own results file, and a manifest that says which arm held which
time window.

`targets_ab.sh` says the extraction is "worth doing once a third experiment of
this shape exists, and not before -- generalizing from two examples is how the
harness got its last set of wrong abstractions." That is still the right rule,
and this is not a third example: it is the *port* of the same two, where the
duplication would otherwise be recreated in a second language. Nothing here
generalizes past what both drivers already do.

## The cutoff voids, it does not truncate

A run that would start past `--until` **voids the whole batch** -- exit 1 and a
VOID row -- rather than stopping early. A partial batch is no result: the arms
would no longer have led equally often, and a cutoff that silently turns four
runs into two is a check that fails quietly.

#175 is why the comparison is on integers. `[ "23:35" \\< "2359" ]` is true,
because ':' (58) sorts above '5' (53), so a string compare fired the cutoff
whenever the hour was 23.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
import logging
import os
import pathlib
import shutil
import sys
import time
from collections.abc import Iterator, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import child

logger = logging.getLogger(__name__)

FAKE_NOW = "LOCAL_LLM_FAKE_NOW"


class Void(RuntimeError):
    """The batch cannot complete as pre-registered, so it produces nothing."""


def now() -> float:
    """Seconds since the epoch, or the injected clock.

    `LOCAL_LLM_FAKE_NOW` is namespaced on purpose: a generic `NOW` in somebody's
    shell would silently shift the cutoff to a wrong instant.
    """
    injected = os.environ.get(FAKE_NOW)
    if injected:
        return float(injected)
    return time.time()


def resolve_until(hhmm: str, *, at: float | None = None) -> float:
    """`HH:MM` today, or tomorrow when today's is already past. Raises ValueError.

    Seconds are pinned to zero. Both `date` dialects filled unspecified fields
    from the current time, so a bare "23:59" carried the current second and the
    resolved cutoff drifted up to 59s -- the at-cutoff test then passed only
    when the second happened to be 0.
    """
    parsed = dt.datetime.strptime(hhmm, "%H:%M")
    moment = dt.datetime.fromtimestamp(at if at is not None else now())
    today = moment.replace(
        hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
    )
    # Strictly past, not at-or-past: at exactly HH:MM the cutoff is today, and
    # the loop's >= fires the VOID at that moment.
    if moment.timestamp() > today.timestamp():
        today += dt.timedelta(days=1)
    return today.timestamp()


def past(cutoff: float | None, *, at: float | None = None) -> bool:
    """Whether the cutoff has arrived. None means there is no cutoff."""
    if cutoff is None:
        return False
    return (at if at is not None else now()) >= cutoff


def stamp(at: float | None = None, *, utc: bool = False) -> str:
    """A timestamp `strip_ab_report.epoch` can read. Two forms, both supported.

    The two shell drivers disagreed: `targets_ab.sh` wrote local-with-offset
    (`2026-09-05T08:56:17-0400`), `strip_toggle_ab.sh` wrote UTC-with-Z
    (`2026-09-06T07:26:35Z`). Each keeps its own form here, so a re-run appends
    to its existing manifest in the shape already in the file.

    Either is fine downstream because `epoch()` normalises both to an instant.
    What is not fine is comparing them as **strings**, which once put 118 of
    120 rows outside every window and mapped them to no arm at all.
    """
    moment = dt.datetime.fromtimestamp(at if at is not None else now(), dt.UTC)
    if utc:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    return moment.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def append(manifest: pathlib.Path, entry: dict[str, object]) -> None:
    """One JSON line. The manifest is how a row learns which arm produced it."""
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a") as handle:
        handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


def void(manifest: pathlib.Path, run: int, until: str) -> None:
    append(
        manifest, {"run": run, "arm": "VOID", "reason": "past-until", "until": until}
    )


@dataclasses.dataclass(frozen=True)
class Batch:
    """Everything a run of either experiment needs to place its output."""

    repo: pathlib.Path
    results: pathlib.Path
    manifest: pathlib.Path
    logdir: pathlib.Path
    bench_logs: pathlib.Path
    batch: str
    harness_head: str
    backend: str
    prefix: str

    def run_dir(self, run: int, arm: str) -> pathlib.Path:
        """`<bench-logs>/<prefix>-<arm>-<batch>-run<n>`.

        `strip_ab_report.batch_of` reads the batch id back out of this name, so
        the shape is an interface and not a filename.
        """
        return self.bench_logs / f"{self.prefix}-{arm}-{self.batch}-run{run}"


def argv(
    b: Batch, arm_flags: Sequence[str], trials: int = 1, client: str = "opencode"
) -> list[str]:
    """The `run.py` command line shared by both experiments.

    `--no-lock` because the batch holds the machine lock for its whole length;
    without it every run would refuse against the batch's own claim.
    """
    return [
        "uv",
        "run",
        "python",
        "benchmarks/agent/run.py",
        "--backend",
        b.backend,
        "--trials",
        str(trials),
        "--client",
        client,
        "--no-lock",
        "--allow-implausible",
        "--results",
        str(b.results),
        "--require-harness-head",
        b.harness_head,
        "--batch",
        b.batch,
        *arm_flags,
    ]


def collect(b: Batch, destination: pathlib.Path, since: float, trials: int = 1) -> int:
    """Claim this run's transcripts, and only the ones this run wrote.

    `since` is the instant the run started. Anything older in the shared log
    directory is a leftover from a killed run, and the plain
    `mv <src>/*<backend>-opencode-1*` both shell drivers used sweeps it in:
    `old-sweep1` once held **22 transcripts for a 15-task sweep**, seven of them
    left by a run killed at 08:17. `lib/transcript_move.sh` closed that for a
    third driver with a marker file and `find -newer`, because BSD and GNU
    `touch -t` disagree on the token form. Comparing mtimes needs neither.
    """
    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in sorted(b.bench_logs.glob(f"*{b.backend}-opencode-{trials}*")):
        if path.is_dir() or path.stat().st_mtime <= since:
            continue
        shutil.move(str(path), str(destination / path.name))
        moved += 1
    return moved


def run_one(
    b: Batch,
    run: int,
    arm: str,
    arm_flags: Sequence[str] = (),
    trials: int = 1,
    utc: bool = False,
) -> int:
    """One run: measure, claim the transcripts, record the window. Returns rc.

    A non-zero exit keeps whatever the run wrote. The rows that exist are still
    rows; what must not happen is the batch reporting itself clean.
    """
    destination = b.run_dir(run, arm)
    destination.mkdir(parents=True, exist_ok=True)
    started = stamp(utc=utc)
    since = time.time()
    logger.info("run %d, arm=%s, starting", run, arm)
    log = b.logdir / f"run{run}-{arm}.log"
    # child.run, not subprocess.run: run.py re-spawns `opencode`, and a signal
    # to this driver reaches neither. #268 -- a stopped driver left run.py
    # writing rows against a server it had already stopped.
    rc = child.run(argv(b, arm_flags, trials), cwd=b.repo, log=log)
    if rc != 0:
        logger.warning("run %d exited %d; keeping what it wrote (%s)", run, rc, log)
    moved = collect(b, destination, since, trials)
    append(
        b.manifest,
        {
            "run": run,
            "arm": arm,
            "started": started,
            "ended": stamp(utc=utc),
            "dir": str(destination),
            "batch": b.batch,
        },
    )
    logger.info("run %d done, %d transcripts", run, moved)
    return rc


def order(arms: tuple[str, str], runs: int) -> list[str]:
    """A B B A, repeating. The shell wrote it as `ORDER[(n-1) % 4]`.

    Identical to `ab_driver.order` read one arm at a time, which is why this
    stays a list rather than growing a second alternation policy: whichever arm
    runs first is faster in 9 of 12 reps (#130, #201), and only equal leading
    cancels it.
    """
    a, c = arms
    cycle = (a, c, c, a)
    return [cycle[(n - 1) % 4] for n in range(1, runs + 1)]


def leads_equally(runs: int) -> bool:
    """Whether `runs` lets each arm lead equally often. A B B A is a 4-cycle."""
    return runs % 4 == 0


@contextlib.contextmanager
def machine(what: str, owner_pid: int, preflight_module) -> Iterator[None]:
    """Hold the machine lock for the batch, and release it on every exit."""
    taken, why = preflight_module.acquire_lock(what, pid=owner_pid)
    if not taken:
        raise Void(f"could not claim the machine: {why}")
    logger.info("machine lock held: %s", why)
    try:
        yield
    finally:
        released, why = preflight_module.release_lock(pid=owner_pid)
        logger.info("machine lock released=%s: %s", released, why)
