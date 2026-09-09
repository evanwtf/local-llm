"""The mlx-serve lifecycle: start, stop, and prove it stopped. #235 stage 2b.

Replaces `scripts/lib/mlx_serve.sh`. The sibling of `lib/ds4_server.py`, and
the same shape for the same reasons -- the server is a `unitctl` unit (#234),
so its pid is recorded at spawn and stop signals its process group.

Everything #145 established about ds4 applies here and harder: **this engine
holds ~100 GiB resident**, and until 2026-09-07 `preflight.py` could not see an
mlx-serve process at all -- it was absent from `INFERENCE`, so a leftover was
invisible to the empty-machine gate rather than merely uncounted. A leaked
server read as an empty machine with 100 GiB of headroom that did not exist.

## Three things the shell did that this does not

**`pgrep -f 'mlx-serve --model'`.** The shell's own comment explains why the
pattern carries `--model`: `pgrep -f mlx-serve` alone matches the shell running
the pgrep. That is the self-match trap, and bracketing only ever protected
against *self*-match -- a third process quoting the same string matches too. A
recorded pid has neither problem.

**`pkill -f`, then `pkill -9 -f`.** Killing by pattern kills whatever matches,
including a server this project did not start. `stop()` signals the process
group of a pid it recorded, and `unitctl` refuses to signal a pid it cannot
confirm is still the process it started -- see `start_key`, which keys on the
process start time so a reused pid is not mistaken for ours.

**Chaining EXIT traps through `trap -p` and `sed`.** `mlx_serve_arm_stop_trap`
parsed the installed handler out of `trap -p EXIT` with a regex and re-installed
it alongside its own, because a second bare `trap ... EXIT` silently discards
the first -- and the one it would have discarded is the ds4 teardown. Two
nested `with` statements cannot get that order wrong, and there is nothing to
parse.

## What it keeps

**Stop, then prove it stopped.** The shell's structure, and it is right: a stop
that is not verified is a stop that reports success while 100 GiB stays
resident.

**A foreign server is refused, not killed.** `stop()` stops *our unit*: a
resident mlx-serve this project did not start is somebody else's process, and
`pkill`-ing it by name is exactly the behavior #235 exists to remove.

But stopping our own unit is only half the invariant. If a foreign server is
resident and we start beside it, that is ~200 GiB on a 128 GiB machine, and
the symptom is not a crash -- it is a run that swaps, and rows that are slow
for a reason nobody records. The shell's `pkill -f` prevented that by killing
whatever matched. **Refusing is the safe half of what it did**, and it lives
in `start()` rather than in each driver, because "an arm starts from a clean
slate" is the invariant this module exists to hold and a driver that has to
remember it is a driver that will forget.
"""

from __future__ import annotations

import contextlib
import logging
import pathlib
import sys
from collections.abc import Iterator, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[2] / "benchmarks" / "agent")
)

import preflight
import unitctl

logger = logging.getLogger(__name__)

UNIT = "mlx-serve"
PROCESS = "mlx-serve"
"""The name preflight's census uses. It joined `INFERENCE` for #191."""


class ForeignServer(RuntimeError):
    """A resident mlx-serve that this project did not start.

    Its own exception because the operator action is specific and manual: find
    out whose it is, and stop it deliberately. The one thing that must not
    happen is starting a second server beside it.
    """


class WouldNotStop(RuntimeError):
    """The server was signalled and is still resident.

    Its own exception because the operator action differs from every other
    failure here: ~100 GiB is held by a process that ignored a stop, and the
    next run must not start.
    """


def running(state_dir: pathlib.Path | None = None) -> bool:
    """Whether *our* unit is running. Says nothing about a foreign server."""
    return unitctl.state(unitctl.read(UNIT, state_dir)) == unitctl.RUNNING


def foreign(state_dir: pathlib.Path | None = None) -> list[preflight.Proc]:
    """Resident mlx-serve processes that are NOT our unit, via preflight.

    The leftover the unit record cannot see: a server started by an earlier
    session, or by hand. It is returned rather than killed -- see the module
    docstring. `preflight` owns the census; this does not re-derive it.

    Our own unit is excluded by pid. An earlier version matched every
    mlx-serve process including ours, which made "foreign" untrue whenever we
    had a server up -- harmless at the intended call site, wrong everywhere
    else. Raised by @deepseek reviewing #252.
    """
    procs = preflight.parse_ps(
        preflight._capture(["ps", "-eo", "pid,rss,etime,command"])
    )
    ours = unitctl.read(UNIT, state_dir)
    our_pid = ours.pid if ours is not None else None
    return [p for p in procs if PROCESS in p.command and p.pid != our_pid]


def stop(why: str = "", state_dir: pathlib.Path | None = None) -> str:
    """Stop our unit and forget it. Returns the state it was found in.

    Idempotent: a stale or absent record is a no-op. `start()` depends on that.
    """
    if why:
        logger.info("stopping %s (%s)", UNIT, why)
    return unitctl.stop(UNIT, state_dir=state_dir)


def stop_and_prove(why: str = "", state_dir: pathlib.Path | None = None) -> str:
    """Stop, then confirm. Raises WouldNotStop if the unit is still running.

    The shell stopped, slept 3, escalated to SIGKILL, slept 2, and checked
    again. `unitctl.stop` already escalates within the process group and
    reports the state it ended in, so the sleeps are gone -- but the *check*
    is not, because a stop that is not verified is a stop that reports success
    while 100 GiB stays resident.
    """
    state = stop(why, state_dir=state_dir)
    if running(state_dir):
        raise WouldNotStop(f"{UNIT} would not stop; it is still running")
    return state


def start(
    command: Sequence[str],
    log: pathlib.Path,
    *,
    cwd: pathlib.Path,
    env: dict[str, str] | None = None,
    allow_foreign: bool = False,
    state_dir: pathlib.Path | None = None,
) -> unitctl.Unit:
    """Start mlx-serve as the `mlx-serve` unit, after stopping any leftover.

    The leftover stop lives here, not in the driver, for the reason
    `lib/ds4_server.py` gives: "an arm starts from a clean slate" is the
    invariant this module exists to hold, and a driver that forgets it
    reproduces #145.
    """
    stop("leftover from an earlier run", state_dir=state_dir)
    resident = foreign(state_dir)
    if resident and not allow_foreign:
        detail = ", ".join(f"pid {p.pid} ({p.rss_gib:.1f} GiB)" for p in resident)
        raise ForeignServer(
            f"an mlx-serve this run did not start is resident: {detail}. "
            "Starting beside it would put both on the machine at once. Stop it "
            "deliberately, or pass allow_foreign=True if that is the intent."
        )
    return unitctl.start(
        UNIT, list(command), log=log, cwd=cwd, env=env, state_dir=state_dir
    )


@contextlib.contextmanager
def serving(
    command: Sequence[str],
    log: pathlib.Path,
    *,
    cwd: pathlib.Path,
    env: dict[str, str] | None = None,
    allow_foreign: bool = False,
    state_dir: pathlib.Path | None = None,
) -> Iterator[unitctl.Unit]:
    """Run mlx-serve for the duration of the block, and always stop it.

    The stop is in a `finally`, so it happens on the exception path, the
    early-return path and the Ctrl-C path without anyone remembering to write
    it. That is the whole of what `mlx_serve_arm_stop_trap` was for, minus the
    `trap -p` parsing.
    """
    unit = start(
        command,
        log,
        cwd=cwd,
        env=env,
        allow_foreign=allow_foreign,
        state_dir=state_dir,
    )
    try:
        yield unit
    finally:
        try:
            stop_and_prove("teardown", state_dir=state_dir)
        except WouldNotStop:
            # Warn, never raise, on the way out: a teardown failure must not
            # replace the exception that is already propagating. The shell had
            # the same rule -- `|| echo "WARNING: mlx-serve survived teardown"`.
            logger.warning("mlx-serve survived teardown; ~100 GiB may still be held")
