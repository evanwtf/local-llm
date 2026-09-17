#!/usr/bin/env python3
"""Notice an OOM kill and put the box's reachability back. #459

The DGX's unified pool can be exhausted faster than a person can react, and
twice it has taken the whole machine with it (2026-09-13, 2026-09-17). Two
layers already stand in front of that: earlyoom, box-wide and memory-only
since #458, and the per-server `MemAvailable` watcher from #456. This is the
layer behind them — it does not prevent anything. It answers "the pool went to
zero, something was killed; is the box still usable?"

**What it actually does, and what it cannot.**

- It records every OOM kill it can see, from the kernel (`oom-kill:` /
  `Out of memory: Killed process`) and from earlyoom (`sending SIGTERM` /
  `sending SIGKILL`), so an incident write-up has the lines rather than a
  rotated-out journal (#390 lost the 2026-09-13 kernel lines that way).
- It restarts the units that keep the box reachable and protected — `ssh` and
  `earlyoom` — when they are not active. `ssh.service` already has
  `Restart=on-failure` and its socket is owned by PID 1, so this is the
  belt-and-suspenders case: a unit left inactive rather than failed.
- **It cannot help a wedged box.** If the pool is at zero and nothing can be
  scheduled, this script does not run either — that is what the hardware
  watchdog in the same issue is for (`/dev/watchdog0`, an SBSA Generic
  Watchdog, unused: `RuntimeWatchdogUSec=0`). A userspace watchdog covers the
  narrower case where the box is responsive but a daemon died.

**Why a systemd timer and not cron.** PID 1 schedules timers and is never
OOM-killed; crond is an ordinary killable daemon, and the event this reacts to
is precisely the one that kills ordinary daemons. The unit files are in
`systemd/` beside this script, and the timer runs `--once`.

    uv run python scripts/oom_watchdog.py --once            # one pass, restart what is down
    uv run python scripts/oom_watchdog.py --once --dry-run  # say what it would do
    uv run python scripts/oom_watchdog.py --since=-24h      # a wider window (note the =)
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: Where the last pass's high-water mark lives, so a run only reports what is
#: new. Outside the repo: this is machine state, not a result.
STATE_PATH = pathlib.Path.home() / ".local-llm-bench" / "oom-watchdog.json"

#: The units whose death costs the operator the box. `ssh` first: without it
#: there is no way in. `earlyoom` second: without it the next exhaustion has
#: no net at all.
GUARDED_UNITS = ("ssh.service", "earlyoom.service")

#: One line per killer, because they log nothing alike.
KERNEL_OOM = re.compile(r"oom-kill:|Out of memory: Killed process")
EARLYOOM_KILL = re.compile(r"sending SIG(TERM|KILL) to process (\d+)")
#: earlyoom's periodic report, which is NOT a kill. It looks alarming at 0%
#: and means only that it was watching.
EARLYOOM_REPORT = re.compile(r"mem avail:")


def classify(line: str) -> str | None:
    """`kernel-oom`, `earlyoom-kill`, or None for anything else.

    earlyoom's `mem avail:` report is explicitly not an event: on 2026-09-17
    it printed `mem avail: 0 of 124610 MiB (0.00%)` and killed nothing,
    because its swap threshold was never met (#458). Reading that line as a
    kill would have reported a save that never happened.
    """
    if EARLYOOM_REPORT.search(line):
        return None
    if KERNEL_OOM.search(line):
        return "kernel-oom"
    if EARLYOOM_KILL.search(line):
        return "earlyoom-kill"
    return None


def events(journal_text: str) -> list[dict[str, str]]:
    """Every OOM event in a journal excerpt, in order, as {kind, line}."""
    out = []
    for line in journal_text.splitlines():
        kind = classify(line)
        if kind:
            out.append({"kind": kind, "line": line.strip()})
    return out


def read_journal(since: str) -> str:
    """`journalctl --since <since>`, or "" when it cannot be read.

    Kernel and earlyoom lines both land in the system journal, so one call
    covers both. An unreadable journal is an absence, not a crash: the restart
    half of this script still has work to do.
    """
    try:
        proc = subprocess.run(
            ["journalctl", "--no-pager", "--since", since, "-o", "short-iso"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout


def unit_active(unit: str) -> bool | None:
    """True/False, or None when systemctl cannot be asked.

    None matters: "unknown" must not be treated as "down", or the watchdog
    restarts healthy units every time systemd is busy.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    state = proc.stdout.strip()
    if state == "active":
        return True
    if state in {"inactive", "failed"}:
        return False
    # `activating` and `deactivating` are transitions, not verdicts: a restart
    # issued into one of them races systemd for no reason. Unknown is unknown.
    return None


def restart(unit: str, dry_run: bool) -> bool:
    """Restart `unit`. Returns whether the command was actually issued."""
    if dry_run:
        logger.warning("would restart %s", unit)
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "restart", unit],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("could not restart %s: %s", unit, exc)
        return False
    if proc.returncode:
        logger.error("restart %s failed: %s", unit, proc.stderr.strip()[:200])
        return False
    logger.warning("restarted %s", unit)
    return True


def load_state(path: pathlib.Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: pathlib.Path = STATE_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n")
    except OSError as exc:
        logger.warning("could not record state: %s", exc)


def sweep(
    since: str,
    dry_run: bool = False,
    restart_units: bool = True,
    state_path: pathlib.Path = STATE_PATH,
) -> dict:
    """One pass: report OOM events in the window, restart what is down."""
    found = events(read_journal(since))
    statuses = {unit: unit_active(unit) for unit in GUARDED_UNITS}
    restarted = []
    for unit, active in statuses.items():
        if active is False and restart_units and restart(unit, dry_run):
            restarted.append(unit)
    for event in found:
        logger.warning("%s: %s", event["kind"], event["line"][:300])
    result = {
        "checked": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "since": since,
        "events": found,
        "units": {unit: state for unit, state in statuses.items()},
        "restarted": restarted,
    }
    save_state(result, state_path)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--since",
        default="-10min",
        help=(
            "journal window to scan (default -10min, the timer's period). "
            "A relative window starts with a dash, so it needs --since=-24h: "
            "argparse reads a bare -24h as another flag."
        ),
    )
    p.add_argument("--once", action="store_true", help="one pass (the timer's mode)")
    p.add_argument("--dry-run", action="store_true", help="report, and restart nothing")
    p.add_argument("--json", action="store_true", help="print the pass as JSON")
    args = p.parse_args(argv)
    # `logs.configure()` puts records on stdout, which is right for a CLI and
    # wrong for `--json`: the caller gets log lines before the object and
    # cannot parse it. Send them to stderr in that mode only.
    logs.configure(stream=sys.stderr if args.json else None)
    result = sweep(args.since, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2))
    elif not result["events"] and not result["restarted"]:
        logger.info(
            "no OOM events since %s; %s",
            args.since,
            ", ".join(f"{u}={s}" for u, s in result["units"].items()),
        )
    # 0 nothing to report, 1 something was killed or restarted. The timer does
    # not care, but a person running it by hand does.
    return 1 if (result["events"] or result["restarted"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
