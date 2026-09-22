"""Run a subprocess under a memory ceiling, portably.

#82: a `scan` implementation that buffered instead of streaming made the oracle
allocate 49 GB and drove the machine into swap -- 19.6 of 20.5 GB used, 97k
pageouts, every process at 0% CPU. The harness had a timeout on that step and
it did not help: a timeout shortens an outage, it does not prevent one.

`RLIMIT_AS` is not an option here. On macOS `setrlimit` refuses even generous
values ("current limit exceeds maximum limit"), and a `preexec_fn` that sets one
low enough to matter kills the fork before exec. Measured, not assumed.

So: poll the child's resident set, including descendants, and kill the process
group when it crosses the ceiling. Polling is crude and it is enough -- the
failure this guards against grows to tens of gigabytes over seconds, not
kilobytes over hours.
"""

from __future__ import annotations

import logging
import os
import pathlib
import signal
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0

#: Where a process sees its own cgroup's files. Inside a container this is the
#: container's cgroup, so `memory.max` is the `docker run --memory` limit.
CGROUP_ROOT = pathlib.Path("/sys/fs/cgroup")

#: The cap when no cgroup limit applies (a bare host run). #379.
UNCONTAINED_CAP_GIB = 24.0

#: cgroup v1 reports "no limit" as a page-rounded near-2**63 value.
_V1_UNLIMITED = 1 << 60


def cgroup_limit_gib(root: pathlib.Path = CGROUP_ROOT) -> float | None:
    """This process's cgroup memory limit in GiB, or None when there is none.

    cgroup v2 `memory.max` first ("max" means unlimited), then v1
    `memory/memory.limit_in_bytes`. An unreadable file is None, never a guess.
    """
    for path in (root / "memory.max", root / "memory" / "memory.limit_in_bytes"):
        try:
            raw = path.read_text().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            limit = int(raw)
        except ValueError:
            return None
        if limit <= 0 or limit >= _V1_UNLIMITED:
            return None
        return limit / 1024**3
    return None


def cgroup_oom_kills(root: pathlib.Path = CGROUP_ROOT) -> int | None:
    """The cgroup's cumulative kernel OOM-kill count, or None if unreadable.

    Read before and after a client phase: a rise means the kernel killed a
    process inside this container, which the harness must record as a memory
    kill (#680), never as the model's failure.
    """
    for path in (root / "memory.events", root / "memory" / "memory.oom_control"):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            key, _, value = line.partition(" ")
            if key == "oom_kill":
                try:
                    return int(value)
                except ValueError:
                    return None
    return None


def default_client_cap_gib(env=None, root: pathlib.Path = CGROUP_ROOT) -> float:
    """The client watcher's cap in GiB; 0 means the watcher is off.

    An explicit `LOCAL_LLM_CLIENT_MEM_CAP_GIB` wins. Inside a cgroup with a
    memory limit (the client container, #680) the watcher is **off**: the
    kernel enforces the limit and kills inside the container. The watcher was
    only ever there to stop a global OOM taking sshd and the model server with
    it (#82, #379), and a container limit already does that. The trial is made
    the kernel's victim (`oom_first`) and the kill is read back from
    `memory.events` (`cgroup_oom_kills`). With no cgroup limit (a bare host,
    or macOS, which has no cgroups) the watcher keeps its 24 GiB default.
    """
    env = os.environ if env is None else env
    raw = env.get("LOCAL_LLM_CLIENT_MEM_CAP_GIB")
    if raw is not None:
        return float(raw)
    if cgroup_limit_gib(root) is not None:
        return 0.0
    return UNCONTAINED_CAP_GIB


def oom_first(argv: list[str]) -> list[str]:
    """`argv`, launched with oom_score_adj 1000 so the kernel kills it first.

    Children inherit the score, so a runaway the agent executes is the victim,
    never the harness that has to write the row. Raising the score needs no
    privilege.
    """
    script = 'echo 1000 > /proc/self/oom_score_adj && exec "$@"'
    return ["sh", "-c", script, "sh", *argv]


def _rss_kib_by_pid() -> dict[int, tuple[int, int]]:
    """{pid: (ppid, rss_kib)} for every process, in one `ps` call."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            try:
                table[int(parts[0])] = (int(parts[1]), int(parts[2]))
            except ValueError:
                continue
    return table


def tree_rss_gib(root: int, table: dict[int, tuple[int, int]]) -> float:
    """Resident set of `root` and every descendant, in GiB.

    The oracle is `uv run pytest`, so the memory is in a grandchild -- reading
    only the direct child would have reported near zero for the 49 GB run that
    motivated this.
    """
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    total, stack = 0, [root]
    seen = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        entry = table.get(pid)
        if entry is None:
            continue
        total += entry[1]
        stack.extend(children.get(pid, ()))
    return round(total / 1024**2, 2)


def tree_pids(root: int, table: dict[int, tuple[int, int]]) -> list[int]:
    """`root` and every descendant, by parent link, in discovery order.

    This is the same walk `tree_rss_gib` measures, and that is the point: the
    cap must kill exactly what it counted. A descendant that leaves the process
    group -- a detached tool call, anything that calls `setsid` -- is still
    reached here by its parent link, but a `killpg` never reaches it.
    """
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    out, stack, seen = [], [root], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if pid in table:
            out.append(pid)
        stack.extend(children.get(pid, ()))
    return out


def kill_tree(root: int, sig: int = signal.SIGKILL) -> int:
    """Signal `root`'s process group AND every descendant. Returns pids signalled.

    2026-09-17 (#485): the client cap measured a 24.8 GiB tree, killed the
    process group, and the model's `mbox-scan` code kept running outside it --
    it had left the group -- until earlyoom SIGTERMed it at 108,524 MiB. The
    cap had counted the runaway and could not kill it.

    **The snapshot is taken before anything is signalled.** Once the parent
    dies its orphans are reparented, the parent links break, and the runaway
    is no longer findable from `root`. Unknown (an unreadable table) falls back
    to the group kill alone rather than doing nothing.
    """
    snapshot = tree_pids(root, _rss_kib_by_pid())
    try:
        os.killpg(os.getpgid(root), sig)
    except (ProcessLookupError, PermissionError):
        pass
    signalled = 0
    for pid in snapshot:
        try:
            os.kill(pid, sig)
            signalled += 1
        except (ProcessLookupError, PermissionError):
            continue
    return signalled


def sample_tree_rss_gib(pid: int) -> float:
    """Resident set of `pid` and every descendant right now, in GiB.

    One `ps` sweep, reused by the client watchdog (#379) so the agent phase gets
    the same tree-RSS ceiling the oracle already has -- the agent's own executed
    code (a runaway model-written solution) grows in a descendant, not in the
    client process itself. Returns 0.0 when the table cannot be read, which the
    caller treats as "unknown", never as a breach.
    """
    return tree_rss_gib(pid, _rss_kib_by_pid())


def run_capped(cmd, cwd, timeout, cap_gib, env=None):
    """subprocess.run, but killed if the process tree exceeds `cap_gib`.

    Returns (CompletedProcess-like, peak_gib, killed_for_memory).
    """
    # Spool to temp files, NOT to pipes. A pipe holds ~64 KiB; once the child
    # fills it the child blocks in write() at 0% CPU and `proc.poll()` never
    # returns, so this loop polls until the deadline and then SIGKILLs a process
    # that had already finished its work. The kill sets returncode -9, which
    # `tests_pass` reads as "tests failed" -- a verbose but honest test failure
    # was silently converted into a timeout and then recorded as a model
    # failure, with killed=False so nothing flagged it. Measured: a command
    # writing 200 KB and exiting immediately burned a full 15s timeout and
    # returned exactly 65536 bytes of truncated output.
    with (
        tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out_f,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_f,
    ):
        return _run_capped_spooled(cmd, cwd, timeout, cap_gib, env, out_f, err_f)


def _run_capped_spooled(cmd, cwd, timeout, cap_gib, env, out_f, err_f):
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=out_f,
        stderr=err_f,
        text=True,
        start_new_session=True,  # own process group, so we can kill the tree
    )
    deadline = time.monotonic() + timeout
    peak, killed = 0.0, False
    while proc.poll() is None:
        if cap_gib:
            peak = max(peak, tree_rss_gib(proc.pid, _rss_kib_by_pid()))
            if peak > cap_gib:
                logger.error(
                    "killing oracle: process tree reached %.1f GiB, cap is %.1f (#82)",
                    peak,
                    cap_gib,
                )
                killed = True
                break
        if time.monotonic() > deadline:
            break
        time.sleep(POLL_SECONDS)

    if killed or proc.poll() is None:
        try:
            # The whole counted tree, not just the group (#485).
            kill_tree(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    proc.wait()
    out_f.flush()
    err_f.flush()
    out_f.seek(0)
    err_f.seek(0)
    stdout, stderr = out_f.read(), err_f.read()
    # `proc.returncode or 1` would turn a successful 0 into a 1 -- every passing
    # oracle run would have been recorded as a failure.
    rc = proc.returncode if proc.returncode is not None else 1
    return subprocess.CompletedProcess(cmd, rc, stdout, stderr), peak, killed
