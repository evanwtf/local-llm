"""Post the two-Spark cluster heartbeat from a timer, independent of any agent session.

Why this exists (#691, failure 3): the 30-minute heartbeat was a session cron
job, and a session scheduler silently skips ticks -- 17:37, 18:07, 20:07 and
20:37 EDT on 2026-09-22 never fired, while the job still showed as registered.
The pair then sat idle and loaded, with nobody told. The operator has seen it
more than once. A heartbeat whose job is to make a stuck window visible cannot
depend on the thing that gets stuck.

So the cadence and the header fields belong to a `systemd --user` timer
(`systemd/local-llm-cluster-heartbeat.*`) that runs this script, and every field
is read fresh here, on both nodes, at post time. The operating session only
writes a small state file saying what is running and what is next, plus any
notes. If the session stops updating it, the heartbeat keeps posting and names
how old the notes are. That staleness line is the signal the old scheme could
not give.

State file (JSON, default ~/.local-llm-bench/heartbeat.json; the session writes
it with `--set`):

    {"issue": 711, "task": "#711 (glm53fexl3dual2xrcctx), 12/30 trials",
     "next": "...", "notes": ["What changed: ...", "..."],
     "updated": "2026-09-24T09:40:00-0400"}

Format: §3a of hardware/Cortex-X925-128GB-GB10-x2/agent-opener-prompt-dgx-cluster.md.
The peer's address is taken from the command line and never printed: the repo
is public and the post goes to a public issue.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import logs

logger = logging.getLogger(__name__)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_PATH = pathlib.Path.home() / ".local-llm-bench" / "heartbeat.json"

#: Every fabric interface on a Spark: two QSFP cages, each reached over two
#: PCIe paths (docs/dgx-cluster-setup.md, gotcha 3).
FABRIC_IFACES = ("enp1s0f1np1", "enP2p1s0f1np1", "enp1s0f0np0", "enP2p1s0f0np0")

#: One shell probe, run identically on each node (locally on the head, over
#: ssh on the worker), so the two halves of the heartbeat cannot drift apart.
#: key=value lines; parse_probe() is the tested half.
PROBE = (
    "echo gpu=$(nvidia-smi --query-gpu=utilization.gpu,power.draw,temperature.gpu"
    " --format=csv,noheader,nounits | head -1 | tr -d ' ');"
    " echo mem_kib=$(awk '/MemAvailable/{print $2}' /proc/meminfo);"
    " echo earlyoom=$(systemctl is-active earlyoom 2>/dev/null || true);"
    " c=0; for i in " + " ".join(FABRIC_IFACES) + "; do"
    ' [ "$(cat /sys/class/net/$i/carrier 2>/dev/null)" = 1 ] && c=$((c+1)); done;'
    " echo links_up=$c; echo links_total=" + str(len(FABRIC_IFACES)) + ";"
    " echo roce_active=$(rdma link show 2>/dev/null | grep -c ACTIVE)"
)

#: Below this many watts a GB10 is at its idle floor (opener §3a: ~4-5 W idle).
IDLE_W = 10.0
#: Notes older than this are called out: the session that owns them may be stuck.
STALE_NOTES_MIN = 45


@dataclass
class Node:
    """One node's reading. None means the probe could not read that field."""

    util: float | None = None
    power_w: float | None = None
    temp_c: float | None = None
    mem_gib: float | None = None
    earlyoom: str | None = None
    links_up: int | None = None
    links_total: int | None = None
    roce_active: int | None = None
    reachable: bool = True


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def parse_probe(text: str) -> Node:
    """Parse PROBE's key=value output. Pure; unknown or missing keys stay None."""
    kv = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and line.strip()
    )
    n = Node()
    gpu = kv.get("gpu", "").split(",")
    if len(gpu) == 3:
        n.util, n.power_w, n.temp_c = (_num(g) for g in gpu)
    if (m := _num(kv.get("mem_kib", ""))) is not None:
        n.mem_gib = m / 1048576
    n.earlyoom = kv.get("earlyoom") or None
    for key in ("links_up", "links_total", "roce_active"):
        if (v := _num(kv.get(key, ""))) is not None:
            setattr(n, key, int(v))
    return n


def parse_rtt(ping_output: str) -> float | None:
    """Average RTT in ms from `ping -q` output, or None if no reply."""
    m = re.search(r"= [\d.]+/([\d.]+)/", ping_output)
    return float(m.group(1)) if m else None


def parse_occupant(machine_state_output: str) -> str | None:
    """The phrase after "Currently on GPU:" in machine_state.py's log."""
    m = re.search(r"Currently on GPU: (.+)", machine_state_output)
    return m.group(1).strip() if m else None


def _f(v: float | None, fmt: str) -> str:
    return "n/a" if v is None else fmt.format(v)


def node_header(name: str, n: Node) -> str:
    if not n.reachable:
        return f"**{name}** UNREACHABLE"
    return (
        f"**{name}** util {_f(n.util, '{:.0f}')}%, {_f(n.power_w, '{:.0f}')}W,"
        f" {_f(n.temp_c, '{:.0f}')}ºC, {_f(n.mem_gib, '{:.1f}')} GiB avail"
    )


def power_reason(head: Node, worker: Node, occupant: str) -> str:
    """Why the GPU power reads what it does. A reading without its reason is
    unreadable a day later (opener §3a), and an asymmetric pair is a finding."""
    idle = occupant.lower().startswith("idle")
    powers = [n.power_w for n in (head, worker) if n.reachable]
    if any(p is None for p in powers) or not powers:
        return "GPU power could not be read on every node."
    low = [p < IDLE_W for p in powers]  # type: ignore[operator]
    if all(low):
        if idle:
            return "Both GPUs read the idle floor: no model is loaded."
        return (
            "Both GPUs read near idle with a model resident: loading (disk-bound)"
            " or between requests."
        )
    if any(low):
        return (
            "**Asymmetric:** one GPU is working and the other is at the idle floor."
            " The split is not doing what it should, unless this sample fell between kernels."
        )
    return "Both GPUs are drawing working power."


def minutes_since(stamp: str | None, now: dt.datetime) -> float | None:
    if not stamp:
        return None
    try:
        then = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return (now - then).total_seconds() / 60


def render(
    now: dt.datetime,
    head: Node,
    worker: Node,
    rtt_ms: float | None,
    outlet: dict[str, float | None],
    occupant: str,
    state: dict,
    serving: str | None,
) -> str:
    """The heartbeat body. Pure: everything it prints is passed in."""
    issue = state.get("issue")
    on_gpu = f"**Currently on GPU:** {occupant}" + (f" (#{issue})" if issue else "")
    hw, ww = outlet.get("wall_w"), outlet.get("worker_wall_w")
    pair = None if hw is None or ww is None else hw + ww
    ups = [n.links_up for n in (head, worker)]
    roce = [n.roce_active for n in (head, worker)]
    total = head.links_total or worker.links_total or len(FABRIC_IFACES)
    link = (
        f"**link** {'/'.join(_f(u, '{}') for u in ups)} of {total} up (head/worker),"
        f" RoCE {'/'.join(_f(r, '{}') for r in roce)} ACTIVE,"
        f" RTT {_f(rtt_ms, '{:.2f}')} ms"
    )
    header = " · ".join(
        [
            f"**{now.strftime('%H:%M %Z')}** — {node_header('head', head)}",
            node_header("worker", worker),
            f"**outlet** {_f(hw, '{:.0f}')}W + {_f(ww, '{:.0f}')}W = {_f(pair, '{:.0f}')}W pair",
            link,
            f"**task** {state.get('task') or 'idle'}",
            f"**next** {state.get('next') or 'not recorded'}",
        ]
    )
    bullets = [f"- {note}" for note in state.get("notes") or []]
    bullets.append(
        f"- **Metrics:** outlet 30-minute peaks {_f(outlet.get('wall_peak_w'), '{:.0f}')} W head,"
        f" {_f(outlet.get('worker_wall_peak_w'), '{:.0f}')} W worker."
        + (f" Serving: {serving}." if serving else "")
    )
    eo = [
        f"{name} {n.earlyoom or 'unknown'}" if n.reachable else f"{name} unreachable"
        for name, n in (("head", head), ("worker", worker))
    ]
    down = any(n.reachable and n.earlyoom != "active" for n in (head, worker))
    bullets.append(
        f"- **Safety:** MemAvailable {_f(head.mem_gib, '{:.1f}')} GiB head,"
        f" {_f(worker.mem_gib, '{:.1f}')} GiB worker. earlyoom: {', '.join(eo)}"
        + (" — **DOWN, fix before the next launch**." if down else ".")
    )
    age = minutes_since(state.get("updated"), now)
    if age is None:
        bullets.append("- **Notes:** the operating session has not recorded any.")
    elif age > STALE_NOTES_MIN:
        bullets.append(
            f"- **Stale notes:** the task/next fields were last updated {age:.0f} min ago."
            " The operating session may be stuck; the fields above are fresh."
        )
    return "\n\n".join(
        [
            on_gpu,
            header,
            power_reason(head, worker, occupant),
            "\n".join(bullets),
            "-- automatic heartbeat (scripts/cluster_heartbeat.py, systemd timer)",
        ]
    )


def _run(cmd: Sequence[str], timeout: int = 60) -> str:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("%s failed: %s", cmd[0], e)
        return ""
    return p.stdout + p.stderr


def read_node(peer: str | None) -> Node:
    if peer is None:
        return parse_probe(_run(["bash", "-c", PROBE]))
    out = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", peer, PROBE])
    node = parse_probe(out)
    node.reachable = node.power_w is not None or node.mem_gib is not None
    return node


def read_state(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_state(path: pathlib.Path, updates: dict, now: dt.datetime) -> dict:
    """Merge `updates` into the state file and stamp it; read back to verify."""
    state = read_state(path) | updates | {"updated": now.isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)
    if read_state(path) != state:  # a write that is not read back is a guess
        raise RuntimeError(f"{path} did not read back as written")
    return state


def gather(peer: str, rtt_target: str, state: dict) -> str:
    import dgx_metrics  # gcx transport; imported here so tests need no token

    now = dt.datetime.now().astimezone()
    head, worker = read_node(None), read_node(peer)
    rtt = parse_rtt(_run(["ping", "-c5", "-q", "-W2", rtt_target]))
    try:
        outlet = dgx_metrics.wall_power("30m")
    except Exception as e:  # noqa: BLE001 -- a missing plug must not stop the post
        logger.warning("outlet read failed: %s", e)
        outlet = {}
    serving = None
    if state.get("serving_model"):
        try:
            snap = dgx_metrics.snapshot(state["serving_model"])
            serving = dgx_metrics.format_line(snap).removeprefix("vLLM: ")
        except Exception as e:  # noqa: BLE001
            logger.warning("serving metrics failed: %s", e)
    ms = _run(
        [sys.executable, str(REPO_ROOT / "scripts" / "machine_state.py")], timeout=120
    )
    occupant = parse_occupant(ms) or "unknown (machine_state.py gave no answer)"
    return render(now, head, worker, rtt, outlet, occupant, state, serving)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--peer", default="spark-b-cx7", help="worker ssh name (not printed)"
    )
    ap.add_argument(
        "--rtt-target", default="10.0.0.2", help="worker fabric address (not printed)"
    )
    ap.add_argument("--state", type=pathlib.Path, default=STATE_PATH)
    ap.add_argument("--repo", default="evanwtf/local-llm")
    ap.add_argument("--dry-run", action="store_true", help="print, do not post")
    ap.add_argument(
        "--set",
        metavar="JSON",
        help="merge this JSON object into the state file and exit (the session's half)",
    )
    args = ap.parse_args(argv)
    logs.configure()

    if args.set is not None:
        updates = json.loads(args.set)
        if not isinstance(updates, dict):
            ap.error("--set takes a JSON object")
        state = write_state(args.state, updates, dt.datetime.now().astimezone())
        print(json.dumps(state, indent=2))
        return 0

    state = read_state(args.state)
    body = gather(args.peer, args.rtt_target, state)
    if args.dry_run or not state.get("issue"):
        print(body)
        if not state.get("issue"):
            logger.warning("no issue in %s; printed instead of posting", args.state)
        return 0
    out = subprocess.run(
        ["gh", "issue", "comment", str(state["issue"]), "--repo", args.repo, "-F", "-"],
        input=body,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if out.returncode != 0:
        logger.error("gh issue comment failed: %s", out.stderr.strip())
        return 1
    logger.info("posted %s", out.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
