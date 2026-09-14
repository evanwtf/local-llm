"""Stop a trial that is burning the clock without touching the GPU (#366).

Two policies, kept in one module so the reasoning lives in one place.

**Why this exists.** On the DGX Spark an agent trial sometimes "runs" for many
minutes while the GPU sits at idle *power* (~9-13 W): the client (opencode) is
stuck not calling the model, so the trial rides its full per-step wall-clock
timeout doing nothing. GPU power is the honest signal here -- ``utilization.gpu``
reads 96% while weights merely load and 0% mid-trial during a tool phase, so it
lies in both directions. ``scripts/gpu_utilization.py`` already encodes the
power rule (``IDLE_WATTS``, ``is_busy``), and this module reuses it.

(A) ``IdleStallWatchdog`` + ``run_client_with_watchdog`` kill a single trial
whose GPU has been idle for a continuous window, well before its wall-clock
timeout, while the wall-clock stays as a backstop.

(B) ``cell_should_abort`` is a pure circuit-breaker: once enough trials of a
cell have run and more than half of them timed out, stop pouring machine-hours
into a cell that is not producing rows.

Both are built to be tested offline: the watchdog takes an injectable clock and
a plain watts sampler, and the circuit-breaker is a pure function. Nothing here
depends on ``nvidia-smi`` being present -- an unreadable GPU reads as ``None``,
which is held, never counted as idle.
"""

from __future__ import annotations

import logging
import math
import os
import pathlib
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# gpu_utilization lives in scripts/, which is not on the default import path.
# Insert it the same way provenance.py reaches scripts/lib: at import, so the
# idle-floor default and the watts sampler can both come from the one module
# that already owns the power rule.
_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import gpu_utilization
import memcap

logger = logging.getLogger(__name__)

#: A GPU power reading in watts, or None when the GPU cannot be read. None is
#: "unknown" -- never treated as idle -- so an unreadable sensor cannot abort a
#: trial that is actually working.
WattsSampler = Callable[[], float | None]

#: The idle floor: at or above this the GPU is doing real work. Sourced from
#: gpu_utilization so there is one definition of "idle" on this hardware.
IDLE_FLOOR_WATTS: float = gpu_utilization.IDLE_WATTS

#: A continuous idle stretch this long (seconds) means the trial has stalled.
#: An agent trial alternates decode with tool and test phases, so a short
#: window would flag a healthy tool phase; ten minutes is long enough that a
#: live run cannot hide inside its gaps.
DEFAULT_IDLE_STALL_SECS: float = 600.0

#: How often the watchdog samples power while a client runs.
DEFAULT_POLL_SECS: float = 15.0

#: How long a killed process group is given to die on SIGTERM before SIGKILL.
DEFAULT_GRACE_SECS: float = 5.0


class IdleStallWatchdog:
    """Track the current continuous GPU-idle stretch. Pure and threadless.

    Call :meth:`sample` on a cadence; ask :meth:`stalled` whether the idle
    stretch has reached the window. A reading at or above ``idle_floor_watts``
    resets the stretch; a reading below it starts or extends it; a ``None``
    reading is *held* -- the stretch neither resets nor grows, because an
    unreadable GPU is not evidence of idleness and must never trigger an abort.

    The clock is injectable so the whole thing is testable without sleeping.
    :meth:`stalled` reads no clock of its own: it measures against the instant
    of the last real sample, so one poll costs exactly one clock read.
    """

    def __init__(
        self,
        watts: WattsSampler,
        *,
        idle_floor_watts: float = IDLE_FLOOR_WATTS,
        idle_stall_secs: float = DEFAULT_IDLE_STALL_SECS,
        poll_secs: float = DEFAULT_POLL_SECS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._watts = watts
        self.idle_floor_watts = idle_floor_watts
        self.idle_stall_secs = idle_stall_secs
        self.poll_secs = poll_secs
        self._monotonic = monotonic
        #: Start of the current continuous idle stretch, or None when the last
        #: known state was busy (or nothing has been sampled yet).
        self._idle_since: float | None = None
        #: The instant of the last readable sample, so stalled() needs no clock.
        self._last: float = monotonic()

    def sample(self) -> float | None:
        """Take one reading and fold it into the idle-stretch state."""
        watts = self._watts()
        if watts is None:
            # Unknown: hold. Do not reset the stretch, do not extend it, and do
            # not advance the reference instant -- so a run of Nones cannot make
            # an idle stretch appear to grow, nor erase one that had begun.
            return None
        now = self._monotonic()
        self._last = now
        if watts >= self.idle_floor_watts:
            self._idle_since = None
        elif self._idle_since is None:
            self._idle_since = now
        return watts

    def idle_seconds(self) -> float:
        """Length of the current continuous idle stretch, as of the last read."""
        if self._idle_since is None:
            return 0.0
        return self._last - self._idle_since

    def stalled(self) -> bool:
        """True once the GPU has been continuously idle for the full window."""
        return self._idle_since is not None and (
            self.idle_seconds() >= self.idle_stall_secs
        )


@dataclass
class ClientResult:
    """What a watched client run produced.

    ``returncode`` is None when the process was killed and never reported one.
    ``idle_stalled``, ``timed_out`` and ``memory_killed`` are the three ways the
    watchdog ended it; all False on a normal exit. ``peak_rss_gib`` is the
    largest tree-RSS sample taken during the run (0.0 when no cap was set).
    """

    stdout: str
    stderr: str
    returncode: int | None
    idle_stalled: bool
    timed_out: bool
    memory_killed: bool = False
    peak_rss_gib: float = 0.0


def _terminate_group(
    proc: subprocess.Popen[str],
    grace_secs: float,
    log: logging.Logger,
) -> None:
    """SIGTERM the child's process group, then SIGKILL it after a grace.

    A pid is a process; a trial is a tree. The client spawns a server-facing
    child, a shell, a test run -- killing the pid alone orphans the rest. The
    child was started with ``start_new_session=True``, so it leads its own
    group and one signal reaches the whole tree.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_secs)
        return
    except subprocess.TimeoutExpired:
        log.warning("client group %d did not exit on SIGTERM; sending SIGKILL", pgid)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_client_with_watchdog(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    env: dict[str, str] | None,
    timeout: float | None,
    watchdog: IdleStallWatchdog,
    poll_secs: float = DEFAULT_POLL_SECS,
    grace_secs: float = DEFAULT_GRACE_SECS,
    memory_cap_gib: float | None = None,
    rss_sampler: Callable[[int], float] = memcap.sample_tree_rss_gib,
    log: logging.Logger = logger,
) -> ClientResult:
    """Run a client subprocess under the idle-stall watchdog.

    Like run.py's ``run()`` -- ``stdin`` closed, stdout/stderr captured, text
    mode -- but via ``Popen`` with ``start_new_session=True`` so the child is
    its own process group and the whole tree can be signalled at once.

    The loop drains output with ``communicate(timeout=...)`` (which reads the
    pipes as it waits, so a chatty client cannot deadlock on a full buffer)
    and samples the watchdog each interval. A sustained idle stall ends the run
    early; the hard ``timeout`` stays as a wall-clock backstop. Whatever the
    child emitted is always collected before returning.
    """
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    start = time.monotonic()
    idle_stalled = False
    timed_out = False
    memory_killed = False
    peak_rss_gib = 0.0
    stdout = ""
    stderr = ""
    while True:
        try:
            # communicate() drains the pipes while it waits, so the child never
            # blocks on a full buffer; retrying after a timeout keeps the output
            # already read. On a clean exit this returns and we are done.
            stdout, stderr = proc.communicate(timeout=poll_secs)
            break
        except subprocess.TimeoutExpired:
            pass
        watchdog.sample()
        if memory_cap_gib:
            # The agent phase gets the ceiling the oracle already has (#82/#379).
            # The runaway grows in a descendant of the client (model-written code
            # the agent executed), so sample the whole tree, not just the client.
            peak_rss_gib = max(peak_rss_gib, rss_sampler(proc.pid))
            if peak_rss_gib > memory_cap_gib:
                memory_killed = True
                log.error(
                    "client memory cap: process tree reached %.1f GiB, cap is "
                    "%.1f GiB -- killing the group before it OOMs the box (#379)",
                    peak_rss_gib,
                    memory_cap_gib,
                )
                break
        if watchdog.stalled():
            idle_stalled = True
            log.error(
                "client GPU-idle-stall: below %.0f W for >= %.0fs -- killing the "
                "process group (was riding a %s timeout doing nothing)",
                watchdog.idle_floor_watts,
                watchdog.idle_stall_secs,
                "None" if timeout is None else f"{timeout:.0f}s",
            )
            break
        if timeout is not None and (time.monotonic() - start) >= timeout:
            timed_out = True
            log.error(
                "client wall-clock timeout after %.0fs -- killing the group", timeout
            )
            break

    if idle_stalled or timed_out or memory_killed:
        _terminate_group(proc, grace_secs, log)
        # Collect whatever was produced before and during the kill.
        try:
            rest_out, rest_err = proc.communicate(timeout=grace_secs)
        except subprocess.TimeoutExpired:
            rest_out, rest_err = "", ""
        stdout = (stdout or "") + (rest_out or "")
        stderr = (stderr or "") + (rest_err or "")

    return ClientResult(
        stdout=stdout or "",
        stderr=stderr or "",
        returncode=proc.returncode,
        idle_stalled=idle_stalled,
        timed_out=timed_out,
        memory_killed=memory_killed,
        peak_rss_gib=peak_rss_gib,
    )


def cell_should_abort(
    trials_done: int,
    total: int,
    timeouts: int,
    *,
    min_fraction: float = 0.25,
    timeout_fraction: float = 0.50,
) -> tuple[bool, str]:
    """Should a cell stop scheduling more trials? A verdict with its reason.

    Abort only when all three hold:

      * ``trials_done >= ceil(min_fraction * total)`` -- enough of the planned
        cell has run to judge it, not a first unlucky trial;
      * ``trials_done > 0``;
      * ``timeouts / trials_done > timeout_fraction`` -- strictly more than the
        fraction, so a cell sitting exactly at the line is given the benefit of
        the doubt.

    ``total <= 0`` (planned size unknown) and ``trials_done == 0`` never abort.
    """
    if total <= 0:
        return False, "not enough data: planned cell size is unknown (total <= 0)"
    if trials_done <= 0:
        return False, "not enough data: no trials done yet"
    threshold = math.ceil(min_fraction * total)
    if trials_done < threshold:
        return (
            False,
            (
                f"not enough data: {trials_done}/{total} done, need >= "
                f"{threshold} ({min_fraction:.0%} of {total}) before judging"
            ),
        )
    rate = timeouts / trials_done
    if rate > timeout_fraction:
        return (
            True,
            (
                f"abort: {timeouts}/{trials_done} trials timed out "
                f"({rate:.0%} > {timeout_fraction:.0%} of a judged cell)"
            ),
        )
    return (
        False,
        (
            f"continue: {timeouts}/{trials_done} timed out "
            f"({rate:.0%} <= {timeout_fraction:.0%})"
        ),
    )


def gpu_watts() -> float | None:
    """One best-effort GPU power reading in watts, or None if unreadable.

    Reuses gpu_utilization's samplers (dcgmi first, then nvidia-smi). This is
    the default watts sampler for the watchdog and the source for the
    ``sample-watts`` CLI. It never raises: an unreadable GPU is None, which the
    watchdog holds rather than counting as idle.
    """
    got = gpu_utilization._dcgmi() or gpu_utilization._query()
    watts = got.get("watts") if got else None
    return watts if isinstance(watts, (int, float)) else None


# --- standalone CLI ---------------------------------------------------------


def _evaluate(backend: str, results_path: pathlib.Path, total: int | None) -> int:
    """Print the circuit-breaker verdict for a backend's rows on disk."""
    import results

    rows = [
        r
        for r in results.load(results_path)
        if r.get("backend") == backend and not results.is_excluded(r)
    ]
    trials_done = len(rows)
    timeouts = sum(1 for r in rows if r.get("error") == "timeout")
    # By default the whole cell IS what has run: judge the timeouts against the
    # rows on disk. --total compares them against a planned cell size instead,
    # so a half-finished cell can be judged before it fills.
    planned = total if total is not None else trials_done
    abort, reason = cell_should_abort(trials_done, planned, timeouts)
    logger.info(
        "%s: %d trials, %d timeouts, planned total %d -- %s",
        backend,
        trials_done,
        timeouts,
        planned,
        reason,
    )
    return 1 if abort else 0


def _sample_watts() -> int:
    watts = gpu_watts()
    if watts is None:
        logger.info("GPU power unavailable")
    else:
        logger.info("GPU power: %.1f W (idle floor %.0f W)", watts, IDLE_FLOOR_WATTS)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    import provenance

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser(
        "evaluate",
        help="print the per-cell timeout circuit-breaker verdict for a backend",
    )
    ev.add_argument("--backend", required=True)
    ev.add_argument("--results", required=True, type=pathlib.Path)
    ev.add_argument(
        "--total",
        type=int,
        default=None,
        help="planned cell size to judge against; default: the rows on disk",
    )

    sub.add_parser("sample-watts", help="print one GPU watts reading, best effort")

    args = parser.parse_args(argv)
    provenance.configure()

    if args.cmd == "evaluate":
        return _evaluate(args.backend, args.results, args.total)
    return _sample_watts()


if __name__ == "__main__":
    raise SystemExit(main())
