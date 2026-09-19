"""Wait for memory to be safe before starting the next trial.

Two OOM kills on 2026-09-13, both on this GB10's unified pool, both because
work started while memory was in flux:

  - a new vLLM server sized its KV cache against a pool the departing server
    had not yet released, and the kernel killed it once both were resident;
  - the pytest suite ran while a resident vLLM held ~115 of 122 GiB, and the
    two together were over-committed.

On unified memory there is no separate VRAM the driver would refuse to
over-allocate: an oversized request succeeds against host RAM and the OOM
killer arbitrates, taking whatever it likes -- including the benchmark. A
PID vanishing is not memory freed, and `nvidia-smi` cannot see the pool at
all (`memory.used` reads N/A on GB10), so the honest signal is host
MemAvailable from /proc/meminfo.

The M5 Max has the same unified-pool hazard and no /proc/meminfo, so on macOS
the gate reads `vm_stat` and `sysctl hw.memsize` instead (#363); see
`parse_vm_stat`. Both paths report the same JSON fields.

This gate loops once a second, prints one JSON object per reading, and exits
0 as soon as memory is BOTH above a floor and no longer falling -- "settled",
not merely momentarily high. It exits 1 if that never happens before the
deadline, so a caller can refuse to start rather than launch into an OOM.

    uv run python scripts/memory_gate.py --min-avail-gib 12
    uv run python scripts/memory_gate.py --min-avail-gib 12 --timeout 180

Each line is a JSON object:

    {"t": 3, "total_gib": 121.7, "used_gib": 109.4, "avail_gib": 12.3,
     "free_gib": 2.9, "min_avail_gib": 12.0, "settled": true, "ok": true}

and the last line carries "result": "ready" | "timeout".
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

GIB = 1024 * 1024  # /proc/meminfo is in kB
BYTES_PER_GIB = 1024**3

_PAGE_SIZE = re.compile(r"page size of (\d+) bytes")
_VM_STAT_LINE = re.compile(r'^"?([^":]+)"?:\s+(\d+)\.?$')
_VM_STAT_AVAILABLE = (
    "Pages free",
    "Pages inactive",
    "Pages speculative",
    "Pages purgeable",
)


def parse_vm_stat(text: str, memsize_bytes: int) -> dict[str, float]:
    """macOS memory from `vm_stat` and `sysctl hw.memsize` (#363).

    Available is free + inactive + speculative + purgeable pages: pages the
    kernel can give a new allocation without swapping, the same meaning as
    MemAvailable on Linux. The page size comes from vm_stat's own header and
    never from a default: Apple silicon pages are 16 KiB, so an assumed 4 KiB
    page would report a quarter of the real headroom. Free and inactive are
    required; speculative and purgeable count as zero when a macOS release
    omits them.
    """
    size = _PAGE_SIZE.search(text)
    if not size:
        raise ValueError("vm_stat output has no page size header")
    page = int(size.group(1))
    pages: dict[str, int] = {}
    for line in text.splitlines():
        m = _VM_STAT_LINE.match(line.strip())
        if m:
            pages[m.group(1).strip()] = int(m.group(2))
    for key in ("Pages free", "Pages inactive"):
        if key not in pages:
            raise ValueError(f"vm_stat output has no {key!r} counter")
    total = memsize_bytes / BYTES_PER_GIB
    avail = sum(pages.get(k, 0) for k in _VM_STAT_AVAILABLE) * page / BYTES_PER_GIB
    return {
        "total_gib": round(total, 1),
        "avail_gib": round(avail, 1),
        "free_gib": round(pages["Pages free"] * page / BYTES_PER_GIB, 1),
        "used_gib": round(total - avail, 1),
    }


def _darwin_sources() -> tuple[str, int]:
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True)
    size = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True
    )
    return vm.stdout, int(size.stdout.strip())


#: #562: when set, read the pool from this node_exporter instead of locally --
#: the harness runs on a client and the pool to protect is the server's.
NODE_EXPORTER: str | None = None


def _node_exporter_meminfo(url: str) -> dict[str, float]:
    import urllib.request

    with urllib.request.urlopen(url, timeout=5) as resp:
        text = resp.read().decode("utf-8", "replace")
    fields: dict[str, float] = {}
    for key in ("MemTotal", "MemAvailable", "MemFree"):
        m = re.search(
            rf"^node_memory_{key}_bytes(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)\s*$",
            text,
            re.MULTILINE,
        )
        if m:
            fields[key] = float(m.group(1)) / BYTES_PER_GIB
    return _summarize(fields)


def _summarize(fields: dict[str, float]) -> dict[str, float]:
    total = fields.get("MemTotal", 0.0)
    avail = fields.get("MemAvailable", 0.0)
    return {
        "total_gib": round(total, 1),
        "avail_gib": round(avail, 1),
        "free_gib": round(fields.get("MemFree", 0.0), 1),
        # used = total - available, NOT total - free: streaming an 84 GiB GGUF
        # leaves ~18 GiB of reclaimable page cache, so MemFree understates what
        # is usable by that much. MemAvailable is the figure that predicts an
        # allocation succeeding.
        "used_gib": round(total - avail, 1),
    }


def _meminfo() -> dict[str, float]:
    if NODE_EXPORTER:
        return _node_exporter_meminfo(NODE_EXPORTER)
    if sys.platform == "darwin":
        return parse_vm_stat(*_darwin_sources())
    return _linux_meminfo()


def _linux_meminfo() -> dict[str, float]:
    fields: dict[str, float] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "MemFree"):
                fields[key] = int(rest.split()[0]) / GIB
    return _summarize(fields)


def gate(
    min_avail_gib: float,
    timeout: float,
    interval: float,
    settle_readings: int,
    out=sys.stdout,
) -> bool:
    """True once memory is above the floor and has stopped falling.

    "Settled" means `settle_readings` consecutive samples at or above the
    floor whose available memory is not still dropping -- a server mid-load
    climbs THROUGH the floor and a trial mid-cleanup rises TO it, and neither
    is safe to build on until it holds. Returns False on timeout.
    """
    deadline = time.monotonic() + timeout
    good = 0
    prev_avail: float | None = None
    t = 0
    while True:
        m = _meminfo()
        avail = m["avail_gib"]
        above = avail >= min_avail_gib
        not_falling = prev_avail is None or avail >= prev_avail - 0.5
        good = good + 1 if (above and not_falling) else 0
        settled = good >= settle_readings
        record = {
            "t": t,
            **m,
            "min_avail_gib": min_avail_gib,
            "settled": settled,
            "ok": above,
        }
        if settled or time.monotonic() >= deadline:
            record["result"] = "ready" if settled else "timeout"
            print(json.dumps(record), file=out, flush=True)
            return settled
        print(json.dumps(record), file=out, flush=True)
        prev_avail = avail
        t += 1
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--min-avail-gib",
        type=float,
        required=True,
        help="proceed only when at least this much host memory is available. "
        "This is HEADROOM for one trial's transient allocations on top of a "
        "resident server, not an empty pool -- a served vLLM holds most of the "
        "128 GiB, so a floor near that total would never clear.",
    )
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument(
        "--settle-readings",
        type=int,
        default=3,
        help="consecutive good samples required before declaring ready, so a "
        "momentary spike does not wave a launch through",
    )
    p.add_argument(
        "--node-exporter",
        default=None,
        help="read memory from this node_exporter /metrics URL instead of this "
        "machine (#562: the harness is on a client, the pool is the server's)",
    )
    args = p.parse_args(argv)
    global NODE_EXPORTER
    NODE_EXPORTER = args.node_exporter
    ready = gate(args.min_avail_gib, args.timeout, args.interval, args.settle_readings)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
