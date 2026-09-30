"""Render the 30-minute operator heartbeat, and flag a session that stopped working.

The operator reads one heartbeat every 30 minutes from each machine
(hardware/agent-opener-prompt.md, §3a). It carries these fields, in this order
(operator, 2026-09-30):

    1. the time, from this machine's clock
    2. the current task, if any
    3. sensors: power (W), temperature, fans, CPU busy
    4. free disk, in GB and as a percentage
    5. open PRs
    6. the next task
    7. questions for the operator

GPU utilization is not in it. It reads 96% while a model loads and 0% mid-trial
(the operator, 2026-09-30: "a nearly useless metric -- power (W) at the outlet
or GPU is much more useful, and temperature as well").

**Why the flags exist.** On 2026-09-30 the DGX cluster sat idle from 03:20 to
06:05 EDT. The server was ready in 34 s. The session had parked the client
launch on a one-shot wakeup that never fired, and the heartbeat kept posting
"server compiling" with both GPUs at 10-14 W. A heartbeat that only repeats
what the session last wrote cannot show that. So this script compares the
session's claim with the machine, and says so in the heartbeat when they
disagree:

* **idle power** -- the task is not idle, but GPU power read at the idle floor
  on this heartbeat and the one before it (two readings, 30 min apart, so a
  sample that falls in a tool gap does not fire it);
* **overdue** -- the session wrote `expect_by` for its current wait, and that
  time has passed;
* **quiet log** -- the session named the log of its current job, and the log
  has not been written for LOG_QUIET_MIN minutes;
* **stale state** -- the session has not updated the state file for
  STALE_MIN minutes;
* **idle, no reason** -- no task, no question for the operator, and no reason
  written: an idle tick must launch work, ask, or say why it may not (opener §3);
* **loop expiring** -- the session's recurring loop job is near its 7-day
  expiry.

State file (JSON, default ~/.local-llm-bench/heartbeat.json). The session
writes its half with `--set`; this script adds `heartbeat_at` and
`idle_power_since`:

    {"issue": 692, "task": "#692 qwen3.8:27b-mlx on Ollama 0.35.0, 12/30 trials",
     "next": "#737 oMLX release", "notes": ["what changed"],
     "questions": ["#451: approve the 115 GB download?"],
     "expect_by": "2026-09-30T08:10:00-04:00", "log": "~/.local-llm-bench/logs/x.log",
     "loop_armed_at": "2026-09-30T06:40:00-04:00"}

Modes:

    heartbeat.py                 render, print, record heartbeat_at and tick_at
    heartbeat.py --dry-run       render and print; record nothing
    heartbeat.py --set JSON      merge JSON into the state file
    heartbeat.py --armed         record loop_armed_at and tick_at (step 0)
    heartbeat.py --tick          record tick_at only (the cluster's tick, whose
                                 heartbeat the timer posts)
    heartbeat.py --watchdog      block until the last tick is STALE_HEARTBEAT_MIN
                                 old, then print what to do and exit. Run it in
                                 the background: its exit wakes the session.

`tick_at` is the session's own pulse, separate from `heartbeat_at`: on the
cluster the timer posts heartbeats whether or not the session is alive, so only
the session can say that the session ticked.

The cluster (scripts/cluster_heartbeat.py) uses the same state file, the same
flags and the same field order, with two nodes in place of one.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import logs

logger = logging.getLogger(__name__)

STATE_PATH = pathlib.Path.home() / ".local-llm-bench" / "heartbeat.json"
REPO = "evanwtf/local-llm"

#: State not updated for this long is flagged: the session may be stuck.
STALE_MIN = 45
#: A named log not written for this long, while the task is busy, is flagged.
LOG_QUIET_MIN = 20
#: The watchdog fires when the last tick is this old (one missed tick).
STALE_HEARTBEAT_MIN = 35
#: A Claude Code recurring job expires after 7 days; warn a day before.
LOOP_WARN_DAYS = 6

#: GPU power at or below this is the idle floor (W). The M5 Max SoC rail reads
#: 2.4 W idle; the RTX 3080 Ti reads ~20 W idle; a GB10 reads 9-14 W idle and
#: 35-83 W working (memory: gpu-idle-is-power-not-utilization).
IDLE_FLOOR_W = {"mac": 5.0, "nvidia": 30.0, "gb10": 20.0}

MACHINE_NAMES = {"mac": "M5 Max", "nvidia": "Ryzen / RTX 3080 Ti"}


# -- state --------------------------------------------------------------------


def read_state(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(
    path: pathlib.Path,
    updates: dict[str, Any],
    now: dt.datetime,
    *,
    stamp: bool = True,
) -> dict[str, Any]:
    """Merge `updates` into the state file; read back to verify.

    `stamp` sets `updated`, which is the session's own progress clock. The
    script's bookkeeping (heartbeat_at, idle_power_since) passes stamp=False,
    or every heartbeat would reset the staleness it is meant to measure.

    Two processes write this file: the session (--set, --tick) and, on the
    cluster, the timer. So the whole read-merge-write holds an exclusive lock
    on a sibling `.lock` file, and each writer renames its own temp file. With
    one shared `.tmp` path, one writer's rename removed the other's file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    with lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        state = read_state(path) | updates
        if stamp:
            state["updated"] = now.isoformat(timespec="seconds")
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(state, indent=2) + "\n")
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        if read_state(path) != state:  # a write that is not read back is a guess
            raise RuntimeError(f"{path} did not read back as written")
    return state


def minutes_since(stamp: str | None, now: dt.datetime) -> float | None:
    if not stamp:
        return None
    try:
        then = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        return None
    return (now - then).total_seconds() / 60


def is_busy(state: dict[str, Any]) -> bool:
    """The session says work is in progress."""
    task = str(state.get("task") or "").strip().lower()
    return bool(task) and not task.startswith("idle")


# -- flags --------------------------------------------------------------------


def idle_power_updates(
    state: dict[str, Any], gpu_idle: bool | None, now: dt.datetime
) -> dict[str, Any]:
    """The new `idle_power_since`: set on the first idle reading of a busy task,
    kept while it stays idle, cleared by any working reading or an idle task."""
    if gpu_idle is None:
        return {}
    if gpu_idle and is_busy(state):
        since = state.get("idle_power_since") or now.isoformat(timespec="seconds")
        return {"idle_power_since": since}
    return {"idle_power_since": None}


def flags(
    state: dict[str, Any],
    now: dt.datetime,
    *,
    gpu_idle: bool | None,
    log_age_min: float | None,
    stale_min: float = STALE_MIN,
) -> list[str]:
    """What disagrees between the session's claim and the machine. Pure."""
    out: list[str] = []
    busy = is_busy(state)
    idle_age = minutes_since(state.get("idle_power_since"), now)
    if busy and gpu_idle and idle_age is not None and idle_age >= 20:
        out.append(
            f"**Idle power, busy task:** the GPU has read the idle floor for"
            f" {idle_age:.0f} min while the task says work is running."
            " Check the job now: is it waiting on nothing?"
        )
    over = minutes_since(state.get("expect_by"), now)
    if busy and over is not None and over > 0:
        out.append(
            f"**Overdue:** the current wait was due {over:.0f} min ago"
            f" ({state['expect_by']}). Diagnose it; do not keep waiting."
        )
    if busy and log_age_min is not None and log_age_min > LOG_QUIET_MIN:
        out.append(
            f"**Quiet log:** the job's log was last written {log_age_min:.0f} min ago."
        )
    age = minutes_since(state.get("updated"), now)
    if age is None:
        out.append("**No state:** the operating session has recorded nothing.")
    elif age > stale_min:
        out.append(
            f"**Stale state:** the session last updated its task {age:.0f} min ago."
            " It may be stuck."
        )
    reason = str(state.get("task") or "").strip().lower().removeprefix("idle")
    if not busy and not state.get("questions") and not reason.strip(" :-—"):
        out.append(
            "**Idle, no reason:** nothing is running, nothing is asked, and the"
            " task gives no reason. Launch the next task, ask the operator, or"
            ' write why (task: "idle: <reason, until when>").'
        )
    loop_age = minutes_since(state.get("loop_armed_at"), now)
    if loop_age is not None and loop_age > LOOP_WARN_DAYS * 1440:
        out.append(
            f"**Loop expiring:** the recurring loop job is {loop_age / 1440:.1f} days"
            " old and expires at 7. Re-arm it (opener, step 0)."
        )
    return out


# -- readings -----------------------------------------------------------------


@dataclass
class Sensors:
    """One machine's reading. None means the field could not be read."""

    gpu_w: float | None = None
    gpu_label: str = "GPU"
    wall_w: float | None = None
    wall_label: str = "input"
    gpu_c: float | None = None
    cpu_c: float | None = None
    fans: list[str] = field(default_factory=list)
    cpu_busy_pct: float | None = None


def _f(v: float | None, fmt: str) -> str:
    return "n/a" if v is None else fmt.format(v)


def format_rpm(values: list[float]) -> str:
    """Fan speeds to the nearest 100 rpm (operator, 2026-09-30): 3,642 -> 3,600.
    Half rounds up; Python's round() would send 3,650 to 3,600."""
    if not values:
        return "n/a"
    return " / ".join(f"{int(v / 100 + 0.5) * 100:,}" for v in values) + " rpm"


def format_sensors(s: Sensors) -> str:
    parts = [f"{s.gpu_label} {_f(s.gpu_w, '{:.0f}')} W"]
    if s.wall_w is not None:
        parts.append(f"{s.wall_label} {s.wall_w:.0f} W")
    parts.append(f"GPU {_f(s.gpu_c, '{:.0f}')} °C")
    if s.cpu_c is not None:
        parts.append(f"CPU {s.cpu_c:.0f} °C")
    parts.append("fans " + (" / ".join(s.fans) if s.fans else "n/a"))
    parts.append(f"CPU {_f(s.cpu_busy_pct, '{:.0f}')}% busy")
    return " · ".join(parts)


#: No downloads below this much free space (opener §2): decimal GB.
DOWNLOAD_FLOOR_GB = 1500


def format_disk(
    free_b: float | None,
    total_b: float | None,
    where: str,
    floor_gb: float | None = DOWNLOAD_FLOOR_GB,
) -> str:
    """Free space in decimal GB, as df -B1 counts it, and as a percentage."""
    if free_b is None:
        return f"n/a ({where})"
    pct = "" if not total_b else f" ({100 * free_b / total_b:.0f}% free)"
    below = (
        f", under the {floor_gb:,.0f} GB download floor"
        if floor_gb is not None and free_b / 1e9 < floor_gb
        else ""
    )
    return f"{free_b / 1e9:,.0f} GB{pct} on {where}{below}"


def format_prs(prs: list[dict[str, Any]] | None) -> str:
    if prs is None:
        return "n/a (gh failed)"
    if not prs:
        return "none open"
    out = []
    for pr in sorted(prs, key=lambda p: int(p["number"])):
        state = str(pr.get("mergeStateStatus") or "").upper()
        tags = ["draft"] if pr.get("isDraft") else []
        if state:
            tags.append(state)
        if pr.get("autoMergeRequest"):
            tags.append("auto-merge")
        out.append(f"#{pr['number']} {pr.get('title', '')} [{', '.join(tags)}]")
    return "; ".join(out)


def parse_top_cpu(text: str) -> float | None:
    """Busy % (user + sys) from the last `CPU usage:` line of macOS `top -l N`."""
    hits = re.findall(r"CPU usage: ([\d.]+)% user, ([\d.]+)% sys", text)
    if not hits:
        return None
    user, sys_ = hits[-1]
    return float(user) + float(sys_)


def parse_proc_stat(before: str, after: str) -> float | None:
    """Busy % between two reads of the `cpu ` line of Linux /proc/stat."""

    def totals(text: str) -> tuple[int, int] | None:
        for line in text.splitlines():
            if line.startswith("cpu "):
                vals = [int(v) for v in line.split()[1:]]
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                return idle, sum(vals)
        return None

    a, b = totals(before), totals(after)
    if a is None or b is None or b[1] == a[1]:
        return None
    return 100 * (1 - (b[0] - a[0]) / (b[1] - a[1]))


def parse_nvidia_smi(text: str) -> tuple[float | None, float | None, float | None]:
    """power.draw, temperature.gpu, fan.speed from one csv,noheader,nounits line."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    vals = [v.strip() for v in line.split(",")]

    def num(i: int) -> float | None:
        try:
            return float(vals[i])
        except (IndexError, ValueError):
            return None

    return num(0), num(1), num(2)


def _run(cmd: Sequence[str], timeout: int = 60) -> str | None:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("%s failed: %s", cmd[0], e)
        return None
    if p.returncode != 0:
        logger.warning("%s exited %d: %s", cmd[0], p.returncode, p.stderr.strip())
        return None
    return p.stdout


def read_mac() -> Sensors:
    import mac_dash  # the exporter on this machine; imported here so tests need none

    snap = mac_dash.snapshot()
    temp: dict[str, float] = snap["temp"]  # type: ignore[assignment]
    fan: dict[str, float] = snap["fan"]  # type: ignore[assignment]
    power: dict[str, float] = snap["power"]  # type: ignore[assignment]
    return Sensors(
        gpu_w=power.get("soc"),
        gpu_label="SoC",
        wall_w=power.get("input"),
        wall_label="input",
        gpu_c=temp.get("gpu"),
        cpu_c=temp.get("cpu"),
        fans=[format_rpm([fan[k] for k in sorted(fan)])] if fan else [],
        cpu_busy_pct=parse_top_cpu(
            _run(["top", "-l", "2", "-n", "0", "-s", "1"]) or ""
        ),
    )


def _hwmon_temp(name: str) -> float | None:
    for d in pathlib.Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (d / "name").read_text().strip() == name:
                return int((d / "temp1_input").read_text()) / 1000
        except (OSError, ValueError):
            continue
    return None


def read_nvidia() -> Sensors:
    w, c, fan = parse_nvidia_smi(
        _run(
            [
                "nvidia-smi",
                "--query-gpu=power.draw,temperature.gpu,fan.speed",
                "--format=csv,noheader,nounits",
            ]
        )
        or ""
    )
    stat = pathlib.Path("/proc/stat")
    before = stat.read_text()
    time.sleep(1)
    return Sensors(
        gpu_w=w,
        gpu_c=c,
        cpu_c=_hwmon_temp("k10temp"),
        fans=[] if fan is None else [f"GPU {fan:.0f}%"],
        cpu_busy_pct=parse_proc_stat(before, stat.read_text()),
    )


def read_prs(repo: str = REPO) -> list[dict[str, Any]] | None:
    out = _run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--json",
            "number,title,isDraft,mergeStateStatus,autoMergeRequest",
        ]
    )
    if out is None:
        return None
    data = json.loads(out)
    return data if isinstance(data, list) else None


def log_age(state: dict[str, Any], now: dt.datetime) -> float | None:
    path = state.get("log")
    if not path:
        return None
    try:
        mtime = pathlib.Path(str(path)).expanduser().stat().st_mtime
    except OSError:
        return None
    return (now.timestamp() - mtime) / 60


# -- render -------------------------------------------------------------------


def render(
    now: dt.datetime,
    machine: str,
    state: dict[str, Any],
    *,
    sensors: list[str],
    disk: str,
    prs: str,
    alerts: list[str],
    extra: list[str] | None = None,
    on_gpu: str | None = None,
) -> str:
    """The heartbeat body, in the operator's field order. Pure."""
    issue = state.get("issue")
    task = str(state.get("task") or "idle")
    if issue and f"#{issue}" not in task:
        task += f" (#{issue})"
    if on_gpu:
        task += f" · on GPU: {on_gpu}"
    lines = [f"**{now.strftime('%Y-%m-%d %H:%M')}** — {machine}", ""]
    lines.append(f"- **Task:** {task}")
    lines += [f"- ⚠ {a}" for a in alerts]
    lines += [f"- **Changed:** {n}" for n in state.get("notes") or []]
    for i, s in enumerate(sensors):
        lines.append(f"- **Sensors:** {s}" if i == 0 else f"  {s}")
    lines += [f"- {e}" for e in extra or []]
    lines.append(f"- **Disk:** {disk}")
    lines.append(f"- **PRs:** {prs}")
    lines.append(f"- **Next:** {state.get('next') or 'not recorded'}")
    questions = state.get("questions") or []
    if questions:
        lines.append("- **Questions for you:**")
        lines += [f"  {i}. {q}" for i, q in enumerate(questions, 1)]
    else:
        lines.append("- **Questions for you:** none")
    return "\n".join(lines)


def disk_path(kind: str) -> pathlib.Path:
    models = pathlib.Path("/Volumes/Models")
    if kind == "mac" and models.is_dir():
        return models
    return pathlib.Path.home()


def gather(
    kind: str, state: dict[str, Any], now: dt.datetime
) -> tuple[str, dict[str, Any]]:
    """Read the machine; return the body and the bookkeeping to record."""
    s = read_mac() if kind == "mac" else read_nvidia()
    gpu_idle = None if s.gpu_w is None else s.gpu_w <= IDLE_FLOOR_W[kind]
    book = idle_power_updates(state, gpu_idle, now)
    merged = state | book
    where = disk_path(kind)
    usage = shutil.disk_usage(where)
    body = render(
        now,
        MACHINE_NAMES[kind],
        merged,
        sensors=[format_sensors(s)],
        disk=format_disk(usage.free, usage.total, str(where)),
        prs=format_prs(read_prs()),
        alerts=flags(merged, now, gpu_idle=gpu_idle, log_age_min=log_age(state, now)),
    )
    return body, book


# -- watchdog -----------------------------------------------------------------


def watchdog(path: pathlib.Path, stale_min: float, poll_s: float) -> str:
    """Block until the last tick is `stale_min` old; return the wake message."""
    while True:
        now = dt.datetime.now().astimezone()
        state = read_state(path)
        age = minutes_since(state.get("tick_at"), now)
        if age is None or age >= stale_min:
            last = state.get("tick_at") or "never"
            return (
                f"WATCHDOG: no tick since {last}"
                + ("" if age is None else f" ({age:.0f} min)")
                + ". The loop missed a tick. Run a tick now (hardware/"
                "agent-opener-prompt.md §3), check CronList, re-arm the loop if it"
                " is gone, then re-arm this watchdog."
            )
        time.sleep(poll_s)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--state", type=pathlib.Path, default=STATE_PATH)
    ap.add_argument(
        "--platform",
        choices=sorted(MACHINE_NAMES),
        default="mac" if platform.system() == "Darwin" else "nvidia",
    )
    ap.add_argument("--dry-run", action="store_true", help="print; record nothing")
    ap.add_argument("--set", metavar="JSON", help="merge a JSON object into the state")
    ap.add_argument("--tick", action="store_true", help="record tick_at and exit")
    ap.add_argument(
        "--armed", action="store_true", help="record loop_armed_at and exit"
    )
    ap.add_argument("--watchdog", action="store_true", help="wait for a missed tick")
    ap.add_argument("--stale-min", type=float, default=STALE_HEARTBEAT_MIN)
    ap.add_argument("--poll-s", type=float, default=60.0)
    args = ap.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    now = dt.datetime.now().astimezone()

    stamp = now.isoformat(timespec="seconds")
    # --armed is a tick too: step 0 starts the watchdog next, and a state
    # with no tick_at makes it fire at once (the DGX, 2026-09-30).
    keys = ["tick_at"] + (["loop_armed_at"] if args.armed else [])
    pulse = dict.fromkeys(keys, stamp) if args.tick or args.armed else {}
    if args.set is not None:
        updates = json.loads(args.set)
        if not isinstance(updates, dict):
            ap.error("--set takes a JSON object")
        # `--tick --set` must record the tick too. It once returned here first,
        # so the M5 Max's 07:41 tick was dropped and the watchdog fired at
        # 08:00 on a loop that had run (2026-09-30, #860).
        state = write_state(args.state, updates | pulse, now)
        logger.info("%s", json.dumps(state, indent=2))
        return 0
    if pulse:
        write_state(args.state, pulse, now, stamp=False)
        logger.info("%s %s", " ".join(keys), stamp)
        return 0
    if args.watchdog:
        logger.info("%s", watchdog(args.state, args.stale_min, args.poll_s))
        return 0

    state = read_state(args.state)
    body, book = gather(args.platform, state, now)
    logger.info("%s", body)
    if not args.dry_run:
        write_state(
            args.state,
            book | {"heartbeat_at": stamp, "tick_at": stamp},
            now,
            stamp=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
