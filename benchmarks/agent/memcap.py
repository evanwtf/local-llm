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
import signal
import subprocess
import time

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0


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


def run_capped(cmd, cwd, timeout, cap_gib, env=None):
    """subprocess.run, but killed if the process tree exceeds `cap_gib`.

    Returns (CompletedProcess-like, peak_gib, killed_for_memory).
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    stdout, stderr = proc.communicate()
    # `proc.returncode or 1` would turn a successful 0 into a 1 -- every passing
    # oracle run would have been recorded as a failure.
    rc = proc.returncode if proc.returncode is not None else 1
    return subprocess.CompletedProcess(cmd, rc, stdout, stderr), peak, killed
