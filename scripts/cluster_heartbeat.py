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

Format: §3a of hardware/agent-opener-prompt.md -- the same field order and the
same stall flags as the single-node `scripts/heartbeat.py`, with both nodes in
the sensors and disk fields. The peer's address is taken from the command line
and never printed: the repo is public and the post goes to a public issue.
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
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import heartbeat as hb

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
    " echo disk_free_b=$(df -B1 --output=avail / | tail -1 | tr -d ' ');"
    " echo disk_total_b=$(df -B1 --output=size / | tail -1 | tr -d ' ');"
    # every fan the nvfanread hwmon exposes (the head: two); none on node B
    # until its MOK-signed driver is installed (dgx-spark-fan-override#14)
    " echo fans=$(cat /sys/class/hwmon/hwmon*/fan*_input 2>/dev/null"
    " | paste -sd, -);"
    " echo earlyoom=$(systemctl is-active earlyoom 2>/dev/null || true);"
    # NVMe feature 0x08 (#884): must read 0. NVIDIA's coalescing service, masked
    # on both nodes, writes 0x107 and makes a QD1 read take 369% as long.
    " echo nvme_coalescing=$(sudo -n nvme get-feature /dev/nvme0 -f 8 2>/dev/null"
    " | sed -n 's/.*Current value://p');"
    " c=0; for i in " + " ".join(FABRIC_IFACES) + "; do"
    ' [ "$(cat /sys/class/net/$i/carrier 2>/dev/null)" = 1 ] && c=$((c+1)); done;'
    " echo links_up=$c; echo links_total=" + str(len(FABRIC_IFACES)) + ";"
    " echo roce_active=$(rdma link show 2>/dev/null | grep -c ACTIVE)"
)

#: At or below this many watts a GB10 is at its idle floor. Idle reads 9-14 W,
#: work 35-83 W. The old line, 10 W, read the 10-14 W of the 2026-09-30 idle
#: window as "working power".
IDLE_W = hb.IDLE_FLOOR_W["gb10"]
#: Free space on / below this is critical (operator, 2026-09-24): the weights for
#: the next arm, a worker copy, or a log burst must never be what fills a node.
#: Decimal GB, as `df -B1` counts it.
DISK_CRITICAL_GB = 600
#: Notes older than this are called out: the session that owns them may be stuck.
STALE_NOTES_MIN = 45


@dataclass
class Node:
    """One node's reading. None means the probe could not read that field."""

    util: float | None = None
    power_w: float | None = None
    temp_c: float | None = None
    mem_gib: float | None = None
    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    fans: list[float] = field(default_factory=list)
    earlyoom: str | None = None
    nvme_coalescing: int | None = None
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
    if (d := _num(kv.get("disk_free_b", ""))) is not None:
        n.disk_free_gb = d / 1e9
    if (d := _num(kv.get("disk_total_b", ""))) is not None:
        n.disk_total_gb = d / 1e9
    n.fans = [f for v in kv.get("fans", "").split(",") if (f := _num(v)) is not None]
    n.earlyoom = kv.get("earlyoom") or None
    try:
        n.nvme_coalescing = int(kv.get("nvme_coalescing", ""), 16)
    except ValueError:
        n.nvme_coalescing = None  # no passwordless sudo or nvme-cli: unknown
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
    """Power, temperature, fan and memory for one node. No utilization: it
    reads 96% while a model loads and 0% mid-trial (operator, 2026-09-30)."""
    if not n.reachable:
        return f"**{name}** UNREACHABLE"
    return (
        f"**{name}** {_f(n.power_w, '{:.0f}')} W, {_f(n.temp_c, '{:.0f}')} °C,"
        f" fans {hb.format_rpm(n.fans)},"
        f" {_f(n.mem_gib, '{:.1f}')} GiB avail"
    )


def node_disk(name: str, n: Node) -> str:
    if not n.reachable:
        return f"{name} n/a"
    pct = (
        ""
        if not n.disk_free_gb or not n.disk_total_gb
        else f" ({100 * n.disk_free_gb / n.disk_total_gb:.0f}% free)"
    )
    return f"{name} {_f(n.disk_free_gb, '{:,.0f}')} GB{pct}" + (
        " **(CRITICAL)**" if disk_critical(n) else ""
    )


def disk_critical(n: Node) -> bool:
    return n.disk_free_gb is not None and n.disk_free_gb < DISK_CRITICAL_GB


def power_reason(head: Node, worker: Node, occupant: str) -> str:
    """Why the GPU power reads what it does. A reading without its reason is
    unreadable a day later (opener §3a), and an asymmetric pair is a finding."""
    idle = occupant.lower().startswith("idle")
    lost = [name for name, n in (("head", head), ("worker", worker)) if not n.reachable]
    if lost:
        # Half a cluster is not evidence about the whole. Say what was seen,
        # and never "Both GPUs ..." on one reading (review of b7a366b).
        seen = [
            f"{name} reads {_f(n.power_w, '{:.0f}')} W"
            for name, n in (("head", head), ("worker", worker))
            if n.reachable
        ]
        return (
            f"The {' and the '.join(lost)} {'is' if len(lost) == 1 else 'are'}"
            " unreachable"
            + (f"; the {', '.join(seen)}" if seen else "")
            + ". The pair's GPU state is unknown."
        )
    powers = [head.power_w, worker.power_w]
    if any(p is None for p in powers):
        return "GPU power could not be read on every node."
    low = [p <= IDLE_W for p in powers]  # type: ignore[operator]
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


def gpus_idle(head: Node, worker: Node) -> bool | None:
    """Both GPUs at the idle floor; None unless both nodes gave a power read.

    An unreachable node is unknown, not idle: dropping it judged the pair on
    the head alone and started the idle clock on half an observation.
    """
    if not (head.reachable and worker.reachable):
        return None
    powers = [head.power_w, worker.power_w]
    if any(p is None for p in powers):
        return None
    return all(p <= IDLE_W for p in powers)  # type: ignore[operator]


def inlet_line(ambient_f: float | None, head: Node, worker: Node) -> str:
    """The inlet air's temperature and each GPU's rise over it. A GPU at 80 °C
    means something different with 20 °C air than with 30 °C air (operator,
    2026-10-03: the PWS indoor sensor sits at the front of the DGX, "presumably
    the inlet")."""
    if ambient_f is None:
        return "**inlet** n/a (the PWS indoor sensor gave no reading in 2 h)"
    c = (ambient_f - 32) * 5 / 9
    rise = [
        "n/a" if n.temp_c is None else f"{n.temp_c - c:+.0f}" for n in (head, worker)
    ]
    return (
        f"**inlet** {ambient_f:.1f} °F ({c:.1f} °C); GPUs {' / '.join(rise)} °C over it"
    )


def render(
    now: dt.datetime,
    head: Node,
    worker: Node,
    rtt_ms: float | None,
    outlet: dict[str, float | None],
    occupant: str,
    state: dict,
    serving: str | None,
    prs: str = "n/a",
    log_age_min: float | None = None,
    ambient_f: float | None = None,
) -> str:
    """The heartbeat body, in the operator's field order. Pure."""
    hw, ww = outlet.get("wall_w"), outlet.get("worker_wall_w")
    pair = None if hw is None or ww is None else hw + ww
    ups = [n.links_up for n in (head, worker)]
    roce = [n.roce_active for n in (head, worker)]
    total = head.links_total or worker.links_total or len(FABRIC_IFACES)
    sensors = [
        f"{node_header('head', head)} · {node_header('worker', worker)}",
        (
            f"**outlet** {_f(hw, '{:.0f}')} W + {_f(ww, '{:.0f}')} W ="
            f" {_f(pair, '{:.0f}')} W pair (30-min peaks"
            f" {_f(outlet.get('wall_peak_w'), '{:.0f}')} /"
            f" {_f(outlet.get('worker_wall_peak_w'), '{:.0f}')} W)"
        ),
        (
            f"**link** {'/'.join(_f(u, '{}') for u in ups)} of {total} up"
            f" (head/worker), RoCE {'/'.join(_f(r, '{}') for r in roce)} ACTIVE,"
            f" RTT {_f(rtt_ms, '{:.2f}')} ms"
        ),
        inlet_line(ambient_f, head, worker),
        power_reason(head, worker, occupant),
    ]
    extra = []
    if serving:
        extra.append(f"**Serving:** {serving}.")
    eo = [
        f"{name} {n.earlyoom or 'unknown'}" if n.reachable else f"{name} unreachable"
        for name, n in (("head", head), ("worker", worker))
    ]
    down = any(n.reachable and n.earlyoom != "active" for n in (head, worker))
    extra.append(
        f"**Safety:** earlyoom: {', '.join(eo)}"
        + (" — **DOWN, fix before the next launch**." if down else ".")
    )
    on = [
        f"{name} ({n.nvme_coalescing:#x})"
        for name, n in (("head", head), ("worker", worker))
        if n.reachable and n.nvme_coalescing
    ]
    if on:
        extra.append(
            f"**NVMe coalescing ON:** {' and '.join(on)}. It should read 0: re-mask"
            " the service (docs/dgx-spark-nvme-coalescing.md, #884)."
        )
    low = [name for name, n in (("head", head), ("worker", worker)) if disk_critical(n)]
    if low:
        extra.append(
            f"**Disk critical:** {' and '.join(low)} below {DISK_CRITICAL_GB} GB free."
            " No download or copy until space is freed (#697)."
        )
    body = hb.render(
        now,
        "DGX cluster",
        state,
        sensors=sensors,
        disk=" · ".join(
            node_disk(n, x) for n, x in (("head", head), ("worker", worker))
        ),
        prs=prs,
        alerts=hb.flags(
            state,
            now,
            gpu_idle=gpus_idle(head, worker),
            log_age_min=log_age_min,
            stale_min=STALE_NOTES_MIN,
        ),
        extra=extra,
        on_gpu=occupant,
    )
    return body


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


read_state = hb.read_state
write_state = hb.write_state
minutes_since = hb.minutes_since


def gather(peer: str, rtt_target: str, state: dict) -> tuple[str, dict]:
    """Read both nodes; return the body and the bookkeeping to record."""
    import dgx_metrics  # gcx transport; imported here so tests need no token

    now = dt.datetime.now().astimezone()
    head, worker = read_node(None), read_node(peer)
    rtt = parse_rtt(_run(["ping", "-c5", "-q", "-W2", rtt_target]))
    try:
        outlet = dgx_metrics.wall_power("30m")
    except Exception as e:  # noqa: BLE001 -- a missing plug must not stop the post
        logger.warning("outlet read failed: %s", e)
        outlet = {}
    try:
        ambient_f = dgx_metrics.inlet_air_f()
    except Exception as e:  # noqa: BLE001 -- a silent sensor must not stop the post
        logger.warning("inlet sensor read failed: %s", e)
        ambient_f = None
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
    merged = state | hb.idle_power_updates(state, gpus_idle(head, worker), now)
    body = render(
        now,
        head,
        worker,
        rtt,
        outlet,
        occupant,
        merged,
        serving,
        prs=hb.format_prs(hb.read_prs()),
        log_age_min=hb.log_age(state, now),  # a log on the head; not the client's
        ambient_f=ambient_f,
    )
    return body, {k: merged.get(k) for k in ("idle_power_since",)}


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
    body, book = gather(args.peer, args.rtt_target, state)
    if not args.dry_run:
        now = dt.datetime.now().astimezone()
        write_state(
            args.state,
            book | {"heartbeat_at": now.isoformat(timespec="seconds")},
            now,
            stamp=False,
        )
    if args.dry_run or not state.get("issue"):
        body = hb.sign(body, "scripts/cluster_heartbeat.py")
        print(body)
        if not state.get("issue"):
            logger.warning("no issue in %s; printed instead of posting", args.state)
        return 0
    out = subprocess.run(
        ["gh", "issue", "comment", str(state["issue"]), "--repo", args.repo, "-F", "-"],
        input=hb.sign(body, "scripts/cluster_heartbeat.py", "(systemd timer)"),
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
