"""The ds4-server lifecycle: start, wait, assert, stop. #236

Replaces `scripts/lib/ds4_server.sh`, which 8 drivers source. That file found
the server with `pgrep -f 'ds4-server --metal'`, killed it with `pkill`, and
shelled out to `uv run python -c '...'` to record the Metal route -- shell
calling Python calling shell, re-deriving a pid it never recorded.

Here the server is a `unitctl` unit (#234): its pid is recorded at spawn and
stop signals its process group, so nothing is ever searched for.

## What the shell version got right and this must not lose

**Teardown on every exit path.** #145: `stack_agent_ab.sh` restarted the server
between sweeps and stopped none of them, so *every clean finish* left the last
arm's server resident -- 97.9 GiB, four runs in a row. It blocked the next run,
and preflight called the machine healthy because a leftover from a finished run
is indistinguishable from a server the current one needs. `serving()` is a
context manager, so the stop happens on the exception path, the early-return
path and the Ctrl-C path without anyone remembering to write it.

**The exit status survives.** The shell chained EXIT traps by parsing `trap -p`
with sed, because a second bare `trap ... EXIT` would silently discard the one
`restart_between_trials.sh` uses to release the run lock -- and the lock would
then outlive the run that took it. A `finally` block cannot do that, and
nesting two `with` statements cannot get the order wrong.

**Route recording is never fatal.** It is provenance. A run that dies because
it could not write a provenance file is worse than a run whose rows say
`unrecorded`.

**An absent graph line is its own failure.** On 2026-09-08 `greedy_mtp_ab.sh`
printed `REFUSING: control arm loaded an MTP head` when in truth no server had
started and there was no log to read -- the `no:*` case matched an empty
string. A check that keeps firing but names the wrong cause is worse than no
check, so `assert_graph` raises `ServerNeverStarted` and `GraphMismatch`
separately.
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

import ds4_route
import preflight
import unitctl
import wait_ready

logger = logging.getLogger(__name__)

UNIT = "ds4-server"
PROCESS = "ds4-server"
"""The name preflight's census uses for this engine."""
DEFAULT_PORT = 8000
DEFAULT_CTX = 100000
DEFAULT_KV_DISK_MB = 8192
GRAPH_MARKER = "Qwen graph allocated"
MTP_OFF = "MTP=off"


class ServerNeverStarted(RuntimeError):
    """The log names no graph line, so the server did not get far enough.

    Distinct from GraphMismatch on purpose: 'we cannot see what it loaded' and
    'it loaded the wrong thing' call for different actions, and conflating them
    is what made a dead control arm report an MTP head it never had.
    """


class ForeignServer(RuntimeError):
    """A resident ds4-server that this project did not start.

    Its own exception because the operator action is specific and manual: find
    out whose it is, and stop it deliberately. The one thing that must not
    happen is starting a second server beside it.
    """


class GraphMismatch(RuntimeError):
    """The server started and loaded something other than the arm requires."""


class NotReady(RuntimeError):
    """The server started but never answered a real completion."""


def argv(
    model: pathlib.Path,
    ple: pathlib.Path,
    kv_dir: pathlib.Path,
    *,
    binary: pathlib.Path,
    port: int = DEFAULT_PORT,
    ctx: int = DEFAULT_CTX,
    kv_disk_mb: int = DEFAULT_KV_DISK_MB,
    mtp_model: pathlib.Path | None = None,
    mtp_draft: int | None = None,
    mtp_timing: bool = False,
    extra: Sequence[str] = (),
) -> list[str]:
    """The server command line.

    Built as a list, which is the point. In shell this was an array spliced
    into a command, and `"${mtp_args[@]}"` is an *unbound variable* under
    `set -u` on bash 3.2 when the array is empty -- so on 2026-09-08 the arm
    with no MTP flags (the control) failed to launch at all, while the arm with
    flags ran fine. A list that is sometimes empty is not a special case here.
    """
    args = [
        str(binary),
        "--metal",
        "-m",
        str(model),
        "--ple",
        str(ple),
        "--ctx",
        str(ctx),
        "--warm-weights",
        "--kv-disk-dir",
        str(kv_dir),
        "--kv-disk-space-mb",
        str(kv_disk_mb),
    ]
    if mtp_model is None and (mtp_draft is not None or mtp_timing):
        # Not refused, yet. `--mtp-draft 7` with no head is an arm that cannot
        # speculate while looking like one that can -- the exact shape of #151,
        # where 119 rows were taken on an arm that never drafted. Refusing is
        # the better end state, but it is a behaviour change, and during a port
        # a behaviour change is indistinguishable from a porting bug. Warn now,
        # refuse in a follow-up once the drivers are across.
        logger.warning(
            "mtp_draft=%r mtp_timing=%r were given with no mtp_model, so no "
            "MTP flags will be passed. This arm cannot speculate (#151)",
            mtp_draft,
            mtp_timing,
        )
    if mtp_model is not None:
        args += ["--mtp-model", str(mtp_model)]
        if mtp_draft is not None:
            args += ["--mtp-draft", str(mtp_draft)]
        if mtp_timing:
            args.append("--mtp-timing")
    args += ["--host", "127.0.0.1", "--port", str(port)]
    args += list(extra)
    return args


def running(state_dir: pathlib.Path | None = None) -> bool:
    """Whether our server unit is up. A recorded pid, not a search."""
    return unitctl.state(unitctl.read(UNIT, state_dir)) == unitctl.RUNNING


def foreign(state_dir: pathlib.Path | None = None) -> list[preflight.Proc]:
    """Resident ds4-server processes that are NOT our unit, via preflight.

    The leftover the unit record cannot see: a server started by an earlier
    session, started by hand, or left by a run whose unit record was removed.
    It is returned rather than killed -- `pkill`-ing a process this project
    did not start is the behavior #235 exists to remove. Our own unit is
    excluded by pid, so "foreign" means "not ours".
    """
    procs = preflight.parse_ps(
        preflight._capture(["ps", "-eo", "pid,rss,etime,command"])
    )
    ours = unitctl.read(UNIT, state_dir)
    our_pid = ours.pid if ours is not None else None
    return [p for p in procs if PROCESS in p.command and p.pid != our_pid]


def stop(why: str = "", state_dir: pathlib.Path | None = None) -> str:
    """Stop the server and forget it. Returns the state it was found in."""
    if why:
        logger.info("stopping %s (%s)", UNIT, why)
    return unitctl.stop(UNIT, state_dir=state_dir)


def start(
    command: Sequence[str],
    log: pathlib.Path,
    *,
    cwd: pathlib.Path,
    allow_foreign: bool = False,
    state_dir: pathlib.Path | None = None,
) -> unitctl.Unit:
    """Start the server as the `ds4-server` unit, after stopping any leftover.

    The leftover stop lives here rather than in the driver: "an arm starts from
    a clean slate" is the invariant this module exists to hold, and a driver
    that forgets it reproduces #145. It means `serving()` calls `unitctl.stop`
    twice on the normal path -- once here, once in its `finally`.

    That is harmless only because `unitctl.stop` is idempotent: a stale or
    absent record is a no-op, and it never signals a pid it cannot confirm is
    ours. **That idempotency is load-bearing here**, not incidental.

    **A foreign server is refused here for the same reason** (#253). Stopping
    our own unit is only half of "a clean slate": if a ds4-server this project
    did not start is resident, starting beside it puts two engines on the
    machine at once, and the symptom is not a crash -- it is a run that swaps,
    and rows that are slow for a reason nobody records. The shell this module
    replaced prevented that with `pkill -f 'ds4-server --metal'`, which killed
    whatever matched; refusing is the safe half of what it did.

    `allow_foreign` is the way through, because a refusal with no way through
    gets deleted rather than satisfied -- but it is a deliberate act.
    """
    stop("leftover from an earlier run", state_dir=state_dir)
    resident = foreign(state_dir)
    if resident and not allow_foreign:
        detail = ", ".join(f"pid {p.pid} ({p.rss_gib:.1f} GiB)" for p in resident)
        raise ForeignServer(
            f"a ds4-server this run did not start is resident: {detail}. "
            "Starting beside it would put both on the machine at once. Stop it "
            "deliberately, or pass allow_foreign=True if that is the intent."
        )
    return unitctl.start(UNIT, list(command), log=log, cwd=cwd, state_dir=state_dir)


def graph_line(log: pathlib.Path) -> str | None:
    """The `Qwen graph allocated` line, or None when the log does not have one.

    None covers three cases that look identical from here and all mean the same
    thing: no log file, an empty log, and a server that died before allocating.
    """
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if GRAPH_MARKER in line:
            return line.strip()
    return None


def assert_graph(log: pathlib.Path, *, want_mtp: bool) -> str:
    """Check the arm loaded what it claims. Returns the graph line.

    Raises ServerNeverStarted when there is no line to read, and GraphMismatch
    when there is one and it disagrees. Never reports the second in place of
    the first.
    """
    line = graph_line(log)
    if line is None:
        raise ServerNeverStarted(
            f"no {GRAPH_MARKER!r} line in {log}; the server did not start, so "
            "nothing can be said about what it loaded"
        )
    has_mtp = MTP_OFF not in line
    if want_mtp and not has_mtp:
        raise GraphMismatch(f"the MTP arm reports {MTP_OFF}: {line}")
    if not want_mtp and has_mtp:
        raise GraphMismatch(f"the control arm loaded an MTP head: {line}")
    return line


def record_route(log: pathlib.Path, port: int, pid: int) -> bool:
    """Record the Metal route this server took (#149). Never raises.

    Provenance, not control flow: a run that dies because it could not write a
    provenance file is worse than one whose rows say `unrecorded`.
    """
    try:
        if ds4_route.record_from_log(log, port=port, pid=pid):
            logger.info("route recorded for :%d from %s", port, log)
            return True
        logger.warning("route NOT recorded: %s names no single route yet", log)
    except Exception:
        logger.warning("route NOT recorded: reading %s failed", log, exc_info=True)
    return False


@contextlib.contextmanager
def serving(
    command: Sequence[str],
    log: pathlib.Path,
    *,
    cwd: pathlib.Path,
    model_id: str,
    want_mtp: bool,
    port: int = DEFAULT_PORT,
    timeout: int = wait_ready.DEFAULT_TIMEOUT,
    allow_foreign: bool = False,
    state_dir: pathlib.Path | None = None,
) -> Iterator[unitctl.Unit]:
    """Run a server for the duration of the block, and always stop it.

    Start, wait for a real completion, assert the graph line, record the route,
    yield. The stop is in a `finally`, so it happens on every exit path --
    which is the #145 fix, expressed once instead of in eight drivers.
    """
    unit = start(
        command, log, cwd=cwd, allow_foreign=allow_foreign, state_dir=state_dir
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        if not wait_ready.ready(base_url, model_id, timeout=timeout):
            raise NotReady(f"{base_url} did not serve {model_id} within {timeout}s")
        logger.info("graph(%s): %s", UNIT, assert_graph(log, want_mtp=want_mtp))
        record_route(log, port, unit.pid)
        yield unit
    finally:
        stop("teardown", state_dir=state_dir)
