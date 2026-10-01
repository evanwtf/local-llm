#!/usr/bin/env python3
"""A/B NVMe interrupt coalescing on a DGX Spark with fio (#884).

    uv run python scripts/nvme_coalescing_ab.py --file SHARD [--file SHARD ...]
    uv run python scripts/nvme_coalescing_ab.py --file SHARD --apply

NVIDIA's `nvidia-nvme-interrupt-coalescing.service` writes 0x107 to NVMe
feature 0x08 at boot: interrupt after 8 completions or 100 us. At queue depth
1 the threshold is never met, so every read waits for the timer. A post
reported ~56 us -> ~200 us per 4K read. DeepSeek V4.1 EXL3 (#685) reads its
file-backed Engram rows from the head's NVMe during prefill and decode, so it
is the stack this could move.

Six arms, on/off three times, so drift cannot pass for an effect. Each arm runs
three fio jobs against existing files, read-only and O_DIRECT:

- qd1:  random 4K reads, one in flight   (the Engram-miss pattern)
- qd32: random 4K reads, 32 in flight    (what coalescing is meant to help)
- seq:  1 MiB sequential reads, 8 deep   (a proxy for model load)

The toggle is `systemctl stop|start nvidia-nvme-interrupt-coalescing`: its
ExecStop writes 0 and its ExecStart writes 0x107. `set-feature` does not
persist, and the service restores 0x107 at boot, so no reboot is needed.
The script reads the value back after every toggle, and **always restores
the state it found** at the end, even on failure. The cluster's standing
state is off with the service masked (docs/dgx-spark-nvme-coalescing.md), so
it refuses while the service is masked: unmask, run, and mask again.

Without `--apply` it prints the plan and changes nothing. It refuses while
any GPU process runs: an idle machine is part of the measurement.

fio creates no data files when it reads existing files with `--readonly`.
The script still lists the files' directories and the output directory before
and after, and deletes anything new that is not its own JSON result.
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
import time
from collections.abc import Iterable, Sequence
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import logs

logger = logging.getLogger(__name__)

SERVICE = "nvidia-nvme-interrupt-coalescing"
ARMS = ("on", "off", "on", "off", "on", "off")  # three per state (no claim on fewer)
STATE_VALUE = {"on": 0x107, "off": 0}
JOBS = {
    "qd1-randread-4k": ["--rw=randread", "--bs=4k", "--iodepth=1"],
    "qd32-randread-4k": ["--rw=randread", "--bs=4k", "--iodepth=32"],
    "seq-read-1m": ["--rw=read", "--bs=1m", "--iodepth=8"],
}


def summarize(fio: dict[str, Any]) -> dict[str, float]:
    """IOPS, bandwidth and completion latency (us) of a one-job fio result."""
    jobs = fio.get("jobs") or []
    if not jobs:
        raise ValueError("fio result has no jobs")
    read = jobs[0]["read"]
    clat = read["clat_ns"]
    pct = clat["percentile"]
    return {
        "iops": read["iops"],
        "bw_kib_s": read["bw"],
        "clat_mean_us": round(clat["mean"] / 1000, 1),
        "clat_p50_us": round(pct["50.000000"] / 1000, 1),
        "clat_p99_us": round(pct["99.000000"] / 1000, 1),
    }


def nvme_interrupts(proc_interrupts: str, controller: str) -> int:
    """Total interrupts on the controller's I/O queues (q1..). q0 is admin."""
    pattern = re.compile(rf"\b{re.escape(controller)}q([1-9]\d*)$")
    lines = proc_interrupts.splitlines()
    # The header names one column per CPU. Only those columns are counts: the
    # chip's hwirq number follows them ("ITS-MSI 1 Edge") and must not be summed.
    ncpu = len(lines[0].split()) if lines else 0
    total = 0
    for line in lines[1:]:
        fields = line.split()
        if not fields or not pattern.search(fields[-1]):
            continue
        total += sum(int(f) for f in fields[1 : 1 + ncpu])
    return total


def parse_feature_value(line: str) -> int:
    """The value from `nvme get-feature DEV -f 8` (without -H)."""
    m = re.search(r"Current value:\s*(0x)?([0-9a-fA-F]+)\s*$", line.strip())
    if not m:
        raise ValueError(f"no feature value in: {line!r}")
    return int(m.group(2), 16)


def listing(dirs: Iterable[pathlib.Path]) -> set[pathlib.Path]:
    return {p for d in dirs for p in d.iterdir()}


def new_files(
    before: set[pathlib.Path], after: set[pathlib.Path]
) -> list[pathlib.Path]:
    return sorted(after - before)


def run(cmd: Sequence[str]) -> str:
    logger.debug("run %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def feature_value(device: str) -> int:
    return parse_feature_value(
        run(["sudo", "-n", "nvme", "get-feature", device, "-f", "8"])
    )


def set_state(state: str, device: str) -> None:
    action = "start" if state == "on" else "stop"
    run(["sudo", "-n", "systemctl", action, SERVICE])
    got = feature_value(device)
    if got != STATE_VALUE[state]:
        raise RuntimeError(
            f"{device}: wanted {STATE_VALUE[state]:#x} for {state}, read {got:#x}"
        )
    logger.info("coalescing %s: %s feature 0x08 = %#x (verified)", state, device, got)


def gpu_busy() -> list[str]:
    out = run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"]
    )
    return [line for line in out.splitlines() if line.strip()]


def fio_job(
    name: str, files: Sequence[pathlib.Path], runtime: int, out: pathlib.Path
) -> dict[str, float]:
    cmd = [
        "fio",
        f"--name={name}",
        "--filename=" + ":".join(str(f) for f in files),
        "--readonly",
        "--direct=1",
        "--ioengine=libaio",
        "--numjobs=1",
        f"--runtime={runtime}",
        "--time_based",
        "--randrepeat=0",
        "--output-format=json",
        f"--output={out}",
        *JOBS[name],
    ]
    run(cmd)
    return summarize(json.loads(out.read_text()))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--file",
        type=pathlib.Path,
        action="append",
        required=True,
        help="an existing file to read (larger than RAM); repeat for more",
    )
    ap.add_argument(
        "--device", default="/dev/nvme0", help="the controller holding the files"
    )
    ap.add_argument("--runtime", type=int, default=60, help="seconds per fio job")
    ap.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path.home()
        / "bench-logs"
        / f"nvme-coalescing-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}",
    )
    ap.add_argument(
        "--apply", action="store_true", help="toggle the setting and run fio"
    )
    args = ap.parse_args(argv)
    logs.configure()

    files = [f.resolve() for f in args.file]
    for f in files:
        if not f.is_file():
            logger.error("not a file: %s", f)
            return 2
    controller = pathlib.Path(args.device).name
    logger.info(
        "plan: arms %s x jobs %s, %d s each, files %s, results in %s",
        "/".join(ARMS),
        ", ".join(JOBS),
        args.runtime,
        ", ".join(map(str, files)),
        args.out,
    )
    if not args.apply:
        logger.info("dry run: nothing changed (pass --apply)")
        return 0
    busy = gpu_busy()
    if busy:
        logger.error("refusing: GPU processes running: %s", "; ".join(busy))
        return 1
    enabled = subprocess.run(
        ["systemctl", "is-enabled", SERVICE],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if enabled == "masked":
        logger.error(
            "refusing: %s is masked, so it cannot be toggled. Unmask it, run, then"
            " mask it again (docs/dgx-spark-nvme-coalescing.md)",
            SERVICE,
        )
        return 1
    found = "on" if feature_value(args.device) else "off"
    logger.info("found coalescing %s; the run ends there", found)

    args.out.mkdir(parents=True)
    dirs = {f.parent for f in files} | {args.out, pathlib.Path.cwd()}
    before = listing(dirs)
    results: list[dict[str, Any]] = []
    try:
        for i, state in enumerate(ARMS, 1):
            set_state(state, args.device)
            for name in JOBS:
                irq0 = nvme_interrupts(
                    pathlib.Path("/proc/interrupts").read_text(), controller
                )
                t0 = time.monotonic()
                out = args.out / f"arm{i}-{state}-{name}.json"
                summary = fio_job(name, files, args.runtime, out)
                elapsed = time.monotonic() - t0
                irq1 = nvme_interrupts(
                    pathlib.Path("/proc/interrupts").read_text(), controller
                )
                row = {
                    "arm": i,
                    "state": state,
                    "job": name,
                    **summary,
                    "irq_per_s": round((irq1 - irq0) / elapsed),
                }
                results.append(row)
                logger.info(
                    "arm %d %-3s %-17s iops %9.0f  p50 %7.1f us  p99 %7.1f us  mean %7.1f us  irq/s %7d",
                    i,
                    state,
                    name,
                    row["iops"],
                    row["clat_p50_us"],
                    row["clat_p99_us"],
                    row["clat_mean_us"],
                    row["irq_per_s"],
                )
    finally:
        set_state(found, args.device)
        ours = {
            args.out / f"arm{i}-{s}-{n}.json"
            for i, s in enumerate(ARMS, 1)
            for n in JOBS
        }
        for p in new_files(before, listing(dirs)):
            if p in ours:
                continue
            logger.warning("deleting a file fio left behind: %s", p)
            p.unlink()
    (args.out / "summary.json").write_text(json.dumps(results, indent=1) + "\n")
    logger.info("wrote %s", args.out / "summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
