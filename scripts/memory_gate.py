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
import sys
import time

GIB = 1024 * 1024  # /proc/meminfo is in kB


def _meminfo() -> dict[str, float]:
    fields: dict[str, float] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "MemFree"):
                fields[key] = int(rest.split()[0]) / GIB
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
    args = p.parse_args(argv)
    ready = gate(args.min_avail_gib, args.timeout, args.interval, args.settle_readings)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
