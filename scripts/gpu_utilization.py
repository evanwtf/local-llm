"""Record what the GPU is actually doing, and say whether the box is earning it.

    uv run python scripts/gpu_utilization.py sample          # one sample
    uv run python scripts/gpu_utilization.py watch --interval 30
    uv run python scripts/gpu_utilization.py report --hours 24

The DGX Spark is a tier-1 measurement machine, and the standing expectation is
**at least 80% busy over any 24-hour window**. Idle time here is not neutral: it
is a machine-hour that produced no row.

Why this exists: on 2026-09-12 the GPU sat at 0% and 9 W for minutes at a time
while a vLLM process still held 83 GiB, because a short job had finished and the
server stayed resident. Nothing surfaced that -- the agent driving the machine
was writing prose, and "a server is loaded" reads like "the machine is working".

## The metric, and why `utilization.gpu` alone will not do

`nvidia-smi --query-gpu=utilization.gpu` is **occupancy over time**: the fraction
of the sample window in which at least one kernel was resident. It is not a
measure of how much compute is in use, and on this machine it reads **96% while
a model is merely loading and drawing 36 W**. Taken alone it would call a
loading server "busy" and score the box far above what it earned.

So a sample is BUSY when occupancy is high **and** power is above an idle floor.
Measured on this GB10: ~9 W at rest, ~36 W loading weights, 41-70 W under
inference. The default floor of 20 W separates rest from work and leaves
loading on the busy side, which is honest -- loading is work the machine must do
to produce a row.

`memory.used` is `[N/A]` here because the 128 GB pool is unified, so resident
memory is read from the process table instead, and it is reported but never
counted as busy. A server holding 83 GiB and computing nothing is exactly the
failure this script exists to make visible.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
import logs

logger = logging.getLogger(__name__)

DEFAULT_LOG = pathlib.Path.home() / ".local-llm-bench" / "gpu-utilization.jsonl"
IDLE_WATTS = 20.0
BUSY_OCCUPANCY = 50.0
TARGET = 80.0


def _query() -> dict:
    """One reading, or an empty dict when nvidia-smi cannot answer."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,power.draw,clocks.current.sm,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {}
    if not out:
        return {}
    parts = [p.strip() for p in out.splitlines()[0].split(",")]

    def num(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    return {
        "occupancy": num(parts[0]),
        "watts": num(parts[1]) if len(parts) > 1 else None,
        "sm_mhz": num(parts[2]) if len(parts) > 2 else None,
        "temp_c": num(parts[3]) if len(parts) > 3 else None,
    }


def _resident_gib() -> float:
    """GiB held by model servers, from the process table.

    nvidia-smi reports [N/A] for memory on unified memory, so this is RSS of the
    serving processes. Reported, never counted as busy.
    """
    try:
        out = subprocess.run(
            ["ps", "-eo", "rss,command"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    total = 0
    for line in out.splitlines()[1:]:
        rss, _, cmd = line.strip().partition(" ")
        if any(k in cmd for k in ("vllm", "llama-server", "ds4-server", "ollama")):
            try:
                total += int(rss)
            except ValueError:
                continue
    return total / 1024 / 1024


def is_busy(sample: dict, idle_watts: float = IDLE_WATTS) -> bool:
    """Real work, not a resident server and not a loading one misread as 96%."""
    occ = sample.get("occupancy")
    watts = sample.get("watts")
    if occ is None or watts is None:
        return False
    return occ >= BUSY_OCCUPANCY and watts >= idle_watts


def sample(path: pathlib.Path) -> dict:
    got = _query()
    if not got:
        return {}
    got["at"] = datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    got["resident_gib"] = round(_resident_gib(), 1)
    got["busy"] = is_busy(got)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(got) + "\n")
    return got


def load(path: pathlib.Path, hours: float) -> list[dict]:
    if not path.exists():
        return []
    cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(hours=hours)
    out = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            if datetime.datetime.fromisoformat(row["at"]) >= cutoff:
                out.append(row)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return out


def report(rows: list[dict], hours: float, target: float = TARGET) -> dict:
    if not rows:
        return {"samples": 0, "utilization": None, "target": target, "meets": False}
    busy = sum(1 for r in rows if r.get("busy"))
    pct = busy / len(rows) * 100
    idle_resident = [
        r for r in rows if not r.get("busy") and (r.get("resident_gib") or 0) > 10
    ]
    return {
        "samples": len(rows),
        "hours": hours,
        "utilization": round(pct, 1),
        "target": target,
        "meets": pct >= target,
        "busy_samples": busy,
        "idle_with_a_server_resident": len(idle_resident),
        "median_watts_busy": round(
            sorted(r["watts"] for r in rows if r.get("busy"))[busy // 2], 1
        )
        if busy
        else None,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mode", choices=["sample", "watch", "report"])
    p.add_argument("--log", type=pathlib.Path, default=DEFAULT_LOG)
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--target", type=float, default=TARGET)
    args = p.parse_args(argv)

    logs.configure()
    if args.mode == "sample":
        got = sample(args.log)
        logger.info("%s", json.dumps(got))
        return 0
    if args.mode == "watch":
        logger.info("sampling every %.0fs into %s", args.interval, args.log)
        while True:
            sample(args.log)
            time.sleep(args.interval)
    got = report(load(args.log, args.hours), args.hours, args.target)
    if not got["samples"]:
        logger.warning(
            "no samples in the last %.0fh -- is the watcher running?", args.hours
        )
        return 1
    verdict = "MEETS" if got["meets"] else "BELOW"
    logger.info(
        "%s: %.1f%% busy over %.0fh (target %.0f%%), %d samples",
        verdict,
        got["utilization"],
        got["hours"],
        got["target"],
        got["samples"],
    )
    if got["idle_with_a_server_resident"]:
        logger.warning(
            "%d idle samples had a model server resident -- a loaded server is "
            "not a working one, and that memory is a machine-hour producing "
            "no row",
            got["idle_with_a_server_resident"],
        )
    logger.info("%s", json.dumps(got))
    return 0 if got["meets"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
