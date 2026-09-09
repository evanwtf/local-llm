#!/usr/bin/env python3
"""start / stop / status for the processes this repo runs. #234

Every long-lived process here -- a ds4 server, a tool shim, an mlx-serve --
was started with `nohup ... &` and found again later with `pgrep -f`, then
killed with `pkill -f`. Matching a process by the text of its command line is
guessing, and on 2026-09-08 it cost, in one day:

- **Seven orphaned waiter shells**, up to 6h30m old, each built as
  `until ! pgrep -f 'metal_knob_ab.sh'; do sleep 30; done`. The shell's own
  command line contains the pattern, so each one waited on itself forever.
- **A commit guard that refused every commit while the machine was idle**,
  because those same shells matched its twelve driver patterns.
- **A red CI run**: `pgrep -a` is GNU-only, `pgrep -l` on GNU prints the
  process name from /proc truncated to 15 characters, so `stack_agent_ab.sh`
  arrived as `stack_agent_ab.` and matched nothing.
- **A published comment** quoting `grep -o` word-occurrence counts as
  drafting metrics.

The fix is to stop searching. A process this repo starts gets a **name**, and
its pid is recorded at spawn:

    uv run python scripts/unitctl.py start shim-8102 \\
        --log ~/bench-logs/run/shim.log -- \\
        uv run python ds4_qwen_tool_shim.py --port 8102
    uv run python scripts/unitctl.py status shim-8102
    uv run python scripts/unitctl.py stop shim-8102

## Two things a naive pidfile gets wrong, and how this avoids them

**The recorded pid is usually not the process you care about.** `uv run python
server.py` records `uv`; the server is its child, and killing the parent
orphans it. So every unit is started with `start_new_session=True`, which puts
it in a **new process group whose id is its own pid**, and stop signals the
whole group. That is what systemd does with a control group, in the one form
POSIX offers.

**Pids are reused.** A stale record naming a pid the kernel has since handed
to something else would make `stop` kill an innocent process. So the unit
records the process's own start time at spawn and re-checks it: a pid whose
start time has changed is not our process, and the record is stale rather than
live. Never kill on the strength of a pid alone.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import errno
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
from collections.abc import Sequence

logger = logging.getLogger(__name__)

STATE_DIR = pathlib.Path(
    os.environ.get("LOCAL_LLM_UNIT_DIR", pathlib.Path.home() / ".local-llm-bench/units")
)

# Exit codes, borrowed from systemctl so a script can branch on them:
# 0 running, 3 stopped/absent. Anything else is this tool failing, not the
# unit being down.
EXIT_RUNNING = 0
EXIT_STOPPED = 3
EXIT_ERROR = 1

RUNNING = "running"
STOPPED = "stopped"
STALE = "stale"

# Units this process started, by name. A child of ours becomes a ZOMBIE when it
# dies and stays one until somebody reaps it -- and `os.kill(pid, 0)` succeeds
# on a zombie, so a liveness check alone reports a dead process as running
# forever. That made every stop wait out its full timeout and then SIGKILL,
# including for `sleep`, which honours SIGTERM immediately.
#
# It only bites when start and stop happen in one process, which is exactly
# what the `serving()` context manager does. Started from the CLI and stopped
# from another, the process is re-parented to init and reaped there.
_OWNED: dict[str, subprocess.Popen] = {}


@dataclasses.dataclass(frozen=True)
class Unit:
    """A named process, as recorded at spawn."""

    name: str
    pid: int
    command: list[str]
    cwd: str
    log: str | None
    started: str
    start_key: str | None
    hostname: str

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def _valid_name(name: str) -> bool:
    """Names become filenames, so they may not traverse or hide."""
    return (
        bool(name) and name not in (".", "..") and "/" not in name and "\0" not in name
    )


def record_path(name: str, state_dir: pathlib.Path | None = None) -> pathlib.Path:
    if not _valid_name(name):
        raise ValueError(f"invalid unit name: {name!r}")
    return (state_dir or STATE_DIR) / f"{name}.json"


def start_key(pid: int) -> str | None:
    """A stable identity for THIS incarnation of `pid`, or None.

    The pid alone is not an identity: the kernel reuses it, and a stale record
    would then aim `stop` at an unrelated process. `ps -o lstart=` gives the
    process's own start time, which a reused pid will not share.
    """
    proc = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def alive(pid: int) -> bool:
    """Whether `pid` is a live process. EPERM counts as alive: someone else's.

    A zombie is NOT alive. It has exited and is only waiting to be reaped, but
    it still answers signal 0, so a plain `os.kill` check calls it running for
    as long as its parent neglects it.
    """
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """Whether `pid` has exited and not yet been reaped. False if ps fails."""
    proc = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and proc.stdout.strip().startswith("Z")


def read(name: str, state_dir: pathlib.Path | None = None) -> Unit | None:
    """The recorded unit, or None when there is no record or it is unreadable."""
    path = record_path(name, state_dir)
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    try:
        return Unit(
            name=raw["name"],
            pid=int(raw["pid"]),
            command=list(raw["command"]),
            cwd=raw.get("cwd", ""),
            log=raw.get("log"),
            started=raw.get("started", ""),
            start_key=raw.get("start_key"),
            hostname=raw.get("hostname", ""),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("unit record for %s is malformed; treating it as absent", name)
        return None


def state(unit: Unit | None) -> str:
    """running / stale / stopped, judged without searching for anything.

    `stale` is not `running`: the record names a pid that is gone, or one the
    kernel has reused for a different process. Both mean there is nothing of
    ours to stop, and neither may be reported as up.

    Two edges in the reuse check, which differ and should:

    - **ps fails later.** `start_key()` returns None, the recorded key is not
      None, so they differ and the unit reads `stale`. That is fail-closed: an
      identity we cannot confirm is not an identity we will signal.
    - **ps failed at spawn.** The record's own key is None, the check is
      skipped, and the unit reads `running`. This is the weaker case and it is
      deliberate: refusing here would call a freshly-started unit dead. The
      window it leaves open needs the pid to be recycled onto another process
      within one unit's lifetime.

    A caller deciding whether to start something must test for `RUNNING`, not
    for `STOPPED`: a `stale` unit is neither, and `if state(u) == STOPPED:
    start()` silently declines to restart a unit that died under it.
    """
    if unit is None:
        return STOPPED
    if not alive(unit.pid):
        return STALE
    if unit.start_key is not None and start_key(unit.pid) != unit.start_key:
        return STALE
    return RUNNING


def start(
    name: str,
    command: list[str],
    log: pathlib.Path | None = None,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
    unset: Sequence[str] = (),
    state_dir: pathlib.Path | None = None,
) -> Unit:
    """Start `command` as unit `name` and record its pid. Refuses a live unit.

    `env` adds to this process's environment; `unset` removes from it. The
    removal is not symmetry for its own sake. A variable that defines an arm by
    its **absence** cannot be expressed by a dict: not mentioning it means
    "inherit", so an operator who exported it in their own shell would hand it
    to the arm that is defined by not having it. #149's R arm is exactly that,
    and the shell wrote `env -u DS4_METAL_ENABLE_TENSOR` for exactly this
    reason.
    """
    if not command:
        raise ValueError("a unit needs a command")
    existing = read(name, state_dir)
    if state(existing) == RUNNING:
        assert existing is not None
        raise RuntimeError(
            f"unit {name} is already running as pid {existing.pid}; "
            "stop it first rather than starting a second one"
        )

    directory = state_dir or STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "ab") if log is not None else subprocess.DEVNULL  # noqa: SIM115

    merged = dict(os.environ)
    merged.update(env or {})
    for key in unset:
        merged.pop(key, None)
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=merged,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # The whole point: a new session gives the child a process group of
            # its own, whose id is its pid, so stop can reap its children too.
            start_new_session=True,
        )
        _OWNED[name] = proc
    finally:
        if handle is not subprocess.DEVNULL:
            handle.close()

    # Spawn-then-record is two steps, and everything between them must be
    # all-or-nothing. If the record never lands -- an unwritable state dir, a
    # full disk -- the process is running and NOTHING can find it: `stop`
    # reads the record, sees none, and reports `stopped` while an 85 GiB
    # server holds the GPU. The shell this replaces would have caught it,
    # because `pkill -f 'ds4-server --metal'` does not need a record. Refusing
    # to search is what makes recording load-bearing.
    try:
        unit = Unit(
            name=name,
            pid=proc.pid,
            command=list(command),
            cwd=str(cwd or pathlib.Path.cwd()),
            log=str(log) if log else None,
            started=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            start_key=start_key(proc.pid),
            hostname=os.uname().nodename,
        )
        record_path(name, state_dir).write_text(json.dumps(unit.as_dict(), indent=2))
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt between the spawn
        # and the record leaks exactly the same way, and Ctrl-C is the
        # likeliest way to end a long batch.
        logger.warning(
            "%s was spawned as pid %d but could not be recorded; stopping it "
            "rather than leaving a process nothing can find",
            name,
            proc.pid,
        )
        _signal_group(proc.pid, signal.SIGKILL)
        _OWNED.pop(name, None)
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        record_path(name, state_dir).unlink(missing_ok=True)
        raise
    logger.info("started %s as pid %d: %s", name, unit.pid, " ".join(command))
    return unit


def stop(
    name: str,
    timeout: float = 10.0,
    state_dir: pathlib.Path | None = None,
) -> str:
    """Stop a unit and forget it. Returns the state it was found in.

    TERM to the process group, then KILL if it outlasts `timeout`. Signalling
    the group is what reaps `uv run python server.py`, where the recorded pid
    is `uv` and the server is its child.
    """
    unit = read(name, state_dir)
    found = state(unit)
    if found == RUNNING:
        assert unit is not None
        _signal_group(unit.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _reap(name)
            if not alive(unit.pid):
                break
            time.sleep(0.05)
        if alive(unit.pid):
            logger.warning("%s ignored SIGTERM for %.0fs; sending KILL", name, timeout)
            _signal_group(unit.pid, signal.SIGKILL)
            _reap(name, block=True)
        logger.info("stopped %s (pid %d)", name, unit.pid)
    elif found == STALE:
        assert unit is not None
        logger.info(
            "%s was already gone (recorded pid %d is not our process); "
            "clearing the record",
            name,
            unit.pid,
        )
    else:
        logger.info("%s is not running", name)
    _OWNED.pop(name, None)
    record_path(name, state_dir).unlink(missing_ok=True)
    return found


def _reap(name: str, block: bool = False) -> None:
    """Collect a child we started, so it stops being a zombie.

    Without this the process is dead, `os.kill(pid, 0)` still succeeds, and
    stop waits out its whole timeout before escalating to a SIGKILL that was
    never needed.
    """
    proc = _OWNED.get(name)
    if proc is None:
        return
    try:
        if block:
            proc.wait(timeout=5)
        elif proc.poll() is None:
            return
    except (subprocess.TimeoutExpired, OSError):
        return
    _OWNED.pop(name, None)


def _signal_group(pid: int, sig: int) -> None:
    """Signal the process group led by `pid`, falling back to the pid alone."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def units(state_dir: pathlib.Path | None = None) -> list[str]:
    directory = state_dir or STATE_DIR
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))


def describe(name: str, state_dir: pathlib.Path | None = None) -> str:
    unit = read(name, state_dir)
    found = state(unit)
    if unit is None:
        return f"{name:24} {found}"
    return (
        f"{name:24} {found:8} pid={unit.pid:<8} since {unit.started}  "
        f"{' '.join(unit.command)[:70]}"
    )


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    """Everything before the first standalone `--`, and everything after."""
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    return argv, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="start/stop/status for repo processes")
    parser.add_argument("--state-dir", type=pathlib.Path, default=None)
    sub = parser.add_subparsers(dest="action", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("name")
    p_start.add_argument("--log", type=pathlib.Path, default=None)
    p_start.add_argument("--cwd", type=pathlib.Path, default=None)
    p_start.add_argument("--env", action="append", default=[], metavar="K=V")

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("name")
    p_stop.add_argument("--timeout", type=float, default=10.0)

    p_status = sub.add_parser("status")
    p_status.add_argument("name", nargs="?")

    sub.add_parser("list")

    # Split on the first standalone `--` before argparse sees anything.
    # `argparse.REMAINDER` on a trailing positional swallows the options too:
    # `start probe --log x -- cmd` parsed as command=["--log","x","--","cmd"]
    # and tried to execute `--log`. Doing the split here makes the boundary
    # mean exactly what it looks like.
    head, command = _split_command(list(sys.argv[1:] if argv is None else argv))
    args = parser.parse_args(head)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.action == "start":
        if not command:
            logger.error("no command given; use: start NAME [opts] -- CMD ...")
            return EXIT_ERROR
        env = {}
        for item in args.env:
            key, _, value = item.partition("=")
            env[key] = value
        try:
            start(
                args.name,
                command,
                log=args.log,
                cwd=args.cwd,
                env=env,
                state_dir=args.state_dir,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            logger.error("%s", exc)
            return EXIT_ERROR
        return EXIT_RUNNING

    if args.action == "stop":
        stop(args.name, timeout=args.timeout, state_dir=args.state_dir)
        return EXIT_RUNNING

    if args.action == "list":
        names = units(args.state_dir)
        if not names:
            logger.info("no units")
        for name in names:
            logger.info("%s", describe(name, args.state_dir))
        return EXIT_RUNNING

    # status
    if args.name:
        logger.info("%s", describe(args.name, args.state_dir))
        found = state(read(args.name, args.state_dir))
        return EXIT_RUNNING if found == RUNNING else EXIT_STOPPED
    names = units(args.state_dir)
    if not names:
        logger.info("no units")
        return EXIT_STOPPED
    any_running = False
    for name in names:
        logger.info("%s", describe(name, args.state_dir))
        any_running = any_running or state(read(name, args.state_dir)) == RUNNING
    return EXIT_RUNNING if any_running else EXIT_STOPPED


if __name__ == "__main__":
    raise SystemExit(main())
