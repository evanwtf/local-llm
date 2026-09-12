"""Is this machine in a state to start work, and did the work actually start?

    uv run python scripts/machine_health.py check            # before launching
    uv run python scripts/machine_health.py confirm --pid N --log FILE

Two failures on 2026-09-12, both of which wasted machine time while every
individual component behaved correctly:

**A second server was launched onto a bound port.** vLLM died instantly with
`OSError: [Errno 98] Address already in use`, the chain's wait loop then polled
for a readiness string that would never appear, and the GPU sat at 9 W for
twenty-five minutes -- while a perfectly healthy server was already listening on
that port. Nothing was broken; nothing checked the whole.

**A run was launched on a dirty tree.** Every row came back stamped
`harness_dirty`, which may not be published, and a pytest suite started minutes
earlier was still running when the lock was taken. Both were discovered after
the fact.

So: `check` refuses to let a launch proceed into a known-bad state, and
`confirm` verifies that something started is *actually running* rather than
assuming a process that was spawned is a process that works.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
import logs

logger = logging.getLogger(__name__)

PORTS = (8000, 8020, 8030)
LOCK = pathlib.Path.home() / ".local-llm-bench" / "run-lock.json"
REPO = pathlib.Path(__file__).resolve().parents[1]


def _own_tree() -> set[int]:
    """This process and its ancestors, excluded from every argv match.

    A shell that quotes a pattern matches it. This has produced three separate
    bugs here and fired inside an idleness check written the same day.
    """
    mine, pid = set(), os.getpid()
    for _ in range(32):
        if pid <= 1:
            break
        mine.add(pid)
        try:
            pid = int(
                pathlib.Path(f"/proc/{pid}/stat")
                .read_text()
                .rsplit(")", 1)[1]
                .split()[1]
            )
        except (OSError, IndexError, ValueError):
            break
    return mine


def _cmdlines() -> list[str]:
    mine, out = _own_tree(), []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in mine:
            continue
        try:
            out.append((entry / "cmdline").read_bytes().replace(b"\0", b" ").decode())
        except OSError:
            continue
    return out


def served_model(port: int) -> str | None:
    """The model a listening server actually serves, or None if nothing answers.

    Probes /v1/models rather than /health: a llama.cpp server answered health
    with 200 while every completion returned 503, because an 84 GB model was
    still being read off disk.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/v1/models", timeout=5
        ) as r:
            data = json.loads(r.read())
        ids = [m["id"] for m in data.get("data", []) if "id" in m]
        return ids[0] if ids else None
    except Exception:  # noqa: BLE001 - anything means "not answering"
        return None


def tree_dirty() -> str:
    try:
        return subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def lock_state() -> dict | None:
    if not LOCK.exists():
        return None
    try:
        held = json.loads(LOCK.read_text())
    except (OSError, json.JSONDecodeError):
        return {"stale": True, "why": "unreadable lock file"}
    pid = held.get("pid")
    alive = bool(pid) and pathlib.Path(f"/proc/{pid}").exists()
    held["stale"] = not alive
    return held


def check(intent: str = "server") -> list[str]:
    """Reasons not to launch. Empty means go.

    `intent` matters: an occupied port blocks starting a SERVER and is exactly
    what you want before starting a RUN against it. The first version of this
    function refused both, which would have talked an operator out of the right
    action -- reusing a healthy server -- on the strength of a check written to
    prevent the opposite mistake. A guard that fires on the correct action
    teaches people to ignore it.
    """
    problems = []
    if tree_dirty():
        problems.append(
            "the tree is dirty -- every row would be stamped harness_dirty and "
            "may not be published. Commit first."
        )
    lock = lock_state()
    if lock and not lock.get("stale"):
        problems.append(
            f"the run lock is held: {lock.get('what')} (pid {lock.get('pid')})"
        )
    if lock and lock.get("stale"):
        problems.append(
            f"a STALE run lock is present (pid {lock.get('pid')} is gone) -- clear it"
        )
    for port in PORTS:
        model = served_model(port)
        if model and intent == "server":
            problems.append(
                f"port {port} is already serving {model!r} -- reuse it rather than "
                f"launching a second server, which dies with EADDRINUSE while a "
                f"wait loop polls for a readiness line that never comes"
            )
    return problems


def confirm(pid: int, log: pathlib.Path | None, timeout: float, quiet: float) -> dict:
    """Did it actually start? Alive AND making progress, or say why not.

    A spawned process is not a working one. This waits for evidence of both
    rather than assuming, and gives up with a reason instead of hanging.
    """
    deadline = time.time() + timeout
    start_size = log.stat().st_size if log and log.exists() else 0
    while time.time() < deadline:
        if not pathlib.Path(f"/proc/{pid}").exists():
            tail = ""
            if log and log.exists():
                tail = log.read_text(errors="replace")[-400:]
            return {"ok": False, "why": f"pid {pid} exited", "tail": tail}
        if log and log.exists() and log.stat().st_size > start_size:
            return {"ok": True, "why": f"pid {pid} alive and {log.name} advanced"}
        time.sleep(2)
    if log and log.exists() and time.time() - log.stat().st_mtime > quiet:
        return {"ok": False, "why": f"pid {pid} alive but {log.name} has not advanced"}
    return {"ok": True, "why": f"pid {pid} alive (no log movement required yet)"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mode", choices=["check", "confirm"])
    p.add_argument(
        "--for",
        dest="intent",
        choices=["server", "run"],
        default="server",
        help="what is about to be launched. A busy port blocks a server and is "
        "fine for a run against it.",
    )
    p.add_argument("--pid", type=int)
    p.add_argument("--log", type=pathlib.Path)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--quiet", type=float, default=300.0)
    args = p.parse_args(argv)

    logs.configure()
    if args.mode == "check":
        problems = check(args.intent)
        for problem in problems:
            logger.warning("not ready: %s", problem)
        if not problems:
            logger.info(
                "ready to launch a %s: tree clean, no lock%s",
                args.intent,
                ", no port occupied" if args.intent == "server" else "",
            )
        return 1 if problems else 0

    if not args.pid:
        logger.error("confirm needs --pid")
        return 2
    got = confirm(args.pid, args.log, args.timeout, args.quiet)
    (logger.info if got["ok"] else logger.error)("%s", got["why"])
    if not got["ok"] and got.get("tail"):
        logger.error("last output:\n%s", got["tail"])
    return 0 if got["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
