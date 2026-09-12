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
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
import logs

logger = logging.getLogger(__name__)

DEFAULT_LOG = pathlib.Path.home() / ".local-llm-bench" / "gpu-utilization.jsonl"
IDLE_WATTS = 20.0
BUSY_OCCUPANCY = 50.0
TARGET = 80.0


def _dcgmi() -> dict:
    """Temperature and power from dcgmi, which reports both more directly.

    See docs/dcgmi.md. The profiling fields that would be genuinely better than
    occupancy -- sm_active (1002), sm_occupancy (1003), tensor_active (1004),
    dram_active (1005) -- are NOT available on this host: dcgmi answers
    "Error setting watches. Result: -33: This request is serviced by a module of
    DCGM that is not currently loaded". So this reads the fields that do work
    and the busy decision still rests on power.

    Needs sudo to reach the container's docker socket, so it is best-effort and
    nvidia-smi remains the fallback.
    """
    try:
        out = subprocess.run(
            [
                "sudo",
                "-n",
                "docker",
                "exec",
                "dcgm",
                "dcgmi",
                "dmon",
                "-e",
                "150,203,155",
                "-c",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "GPU":
            try:
                return {
                    "temp_c": float(parts[2]),
                    "occupancy": float(parts[3]),
                    "watts": float(parts[4]),
                    "source": "dcgmi",
                }
            except ValueError:
                return {}
    return {}


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
    """Real work. **Power is the primary indicator, not occupancy.**

    Measured on this GB10, and the numbers are unambiguous:

        idle      9.4 W, 37 C, occupancy 0%
        loading  36 W,   58 C, occupancy 96%   <- occupancy lies here
        serving  35-81 W, 53-63 C

    `utilization.gpu` is occupancy over time -- the fraction of the sample
    window with at least one kernel resident -- so it reads 96% while a model is
    merely being copied into memory, and it reads 0% mid-trial whenever an agent
    is running a tool or a test suite rather than decoding. It is the wrong
    primary signal in both directions.

    Power separates the states cleanly: a ~4x gap between 9.4 W at rest and
    35-81 W under any real work, with no overlap. Temperature corroborates
    (37 C idle against 53-63 C busy) but lags by tens of seconds, so it is
    recorded and reported, never used to decide.

    Occupancy is kept only as a fallback for a machine where power is
    unreadable.
    """
    watts = sample.get("watts")
    if watts is not None:
        return watts >= idle_watts
    occ = sample.get("occupancy")
    return occ is not None and occ >= BUSY_OCCUPANCY


def sample(path: pathlib.Path) -> dict:
    got = _dcgmi() or _query()
    if not got:
        return {}
    got.setdefault("source", "nvidia-smi")
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
    # The SPAN the samples actually cover, not the window that was requested.
    # Reporting "over 24h" when the log holds twenty minutes is a lie by label:
    # it invites a reader to treat a short, unrepresentative stretch as a day's
    # worth of evidence. The requested window is kept separately as `asked_for`.
    try:
        stamps = sorted(datetime.datetime.fromisoformat(r["at"]) for r in rows)
        span_h = (stamps[-1] - stamps[0]).total_seconds() / 3600
    except (KeyError, ValueError):
        span_h = 0.0
    return {
        "samples": len(rows),
        "span_hours": round(span_h, 2),
        "asked_for_hours": hours,
        "enough_data": span_h >= hours * 0.8,
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


# --- idleness, which is not the same question as "is a kernel resident" ------


def machine_state(log: pathlib.Path, window: int = 10) -> dict:
    """Is the machine failing to make progress? A verdict with its reason.

    No single instantaneous signal answers this, and every one of them has been
    wrong here on its own:

    - **occupancy** reads 96% while weights are merely being copied in, and 0%
      mid-trial whenever the agent is running a tool or a test suite instead of
      decoding.
    - **power** is the best single signal (9.4 W at rest against 35-81 W under
      work) but still dips to idle levels during those same tool phases, so one
      low reading proves nothing.
    - **a resident server** proves less than it looks: 83 GiB held by a vLLM
      process that finished its job an hour ago reads exactly like a server
      about to serve.
    - **the run lock** says a batch claimed the machine, not that it is moving;
      a wedged run holds the lock forever.

    So the verdict is a conjunction over a WINDOW, corroborated by progress:

    BUSY      recent samples show real power draw
    WORKING   power is low right now but a benchmark process is alive AND its
              log advanced recently -- the tool-execution phase of a live trial
    LOADING   a server process exists but does not answer yet. Weight loading is
              disk-bound, so power stays at idle levels throughout
    IDLE      sustained low power, no live benchmark process, no log movement.
              The only state that is a defect, and the only one worth acting on.
    STALLED   a benchmark process is alive and holding the lock, but power has
              been low and its log has not advanced -- worse than idle, because
              it looks busy
    """
    rows = load(log, hours=1)[-window:]
    watts = [r["watts"] for r in rows if r.get("watts") is not None]
    recent_power = max(watts) if watts else None
    procs = _serving_processes()
    bench = _benchmark_running()
    advanced = _log_advanced_recently()

    if recent_power is not None and recent_power >= IDLE_WATTS:
        state = "BUSY"
        why = f"peak {recent_power:.1f} W over the last {len(watts)} samples"
    elif bench and advanced:
        state = "WORKING"
        why = "power low, but a benchmark is alive and its log advanced -- a trial's tool phase"
    elif bench:
        state = "STALLED"
        why = "a benchmark process holds the machine but power is low and its log has not advanced"
    elif procs and not _server_answers():
        # Weight loading is DISK-bound, not GPU-bound: power sits at idle
        # levels for the whole shard read, so power cannot separate loading
        # from idle. The separating fact is that a server process exists and
        # is not yet answering. An earlier version required >15 W here and
        # therefore called a loading server IDLE -- the 36 W seen previously
        # while "loading" was the later CUDA-graph capture, not the read.
        state = "LOADING"
        why = "a server process exists but does not answer yet -- between phases"
    else:
        state = "IDLE"
        why = (
            f"no benchmark process, no log movement, peak "
            f"{recent_power if recent_power is None else round(recent_power, 1)} W"
        )
    return {
        "state": state,
        "why": why,
        "peak_watts": recent_power,
        "samples": len(watts),
        "benchmark_running": bench,
        "log_advanced": advanced,
        "resident_gib": round(_resident_gib(), 1),
        "actionable": state in {"IDLE", "STALLED"},
    }


def _own_tree() -> set[int]:
    """This process and every ancestor, to exclude from any argv match.

    The self-match trap, which has produced three separate bugs in this repo and
    fired inside this very function's first version: a shell that quotes a
    pattern matches it. Bracketing (`[b]enchmarks`) only ever protected against
    matching the pattern literally; it does not stop a parent shell whose
    command line happens to contain the words.
    """
    mine = set()
    pid = os.getpid()
    for _ in range(32):
        if pid <= 1:
            break
        mine.add(pid)
        try:
            stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return mine


def _cmdlines() -> list[str]:
    """Every process's argv, excluding this process and its ancestors."""
    mine = _own_tree()
    out = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in mine:
            continue
        try:
            out.append((entry / "cmdline").read_bytes().replace(b"\0", b" ").decode())
        except OSError:
            continue
    return out


def _serving_processes() -> bool:
    return any(
        k in c
        for c in _cmdlines()
        for k in ("vllm serve", "llama-server", "ds4-server")
    )


def _benchmark_running() -> bool:
    """A measurement process, matched on the harness's own module path."""
    return any(
        k in c
        for c in _cmdlines()
        for k in (
            "benchmarks/agent/run.py",
            "scripts/vllm_load.py",
            "scripts/model_probe.py",
        )
    )


def _server_answers(ports: tuple[int, ...] = (8020, 8030, 8000)) -> bool:
    """Does any local model server answer a real request?

    Probes /v1/models rather than /health: on 2026-08-31 a llama.cpp server
    answered health with 200 while every completion returned 503, because an
    84 GB model was still being read off disk.
    """
    for port in ports:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=3
            ) as response:
                if response.status == 200:
                    return True
        except Exception as exc:  # noqa: BLE001 - any failure means "not answering"
            logger.debug("port %d did not answer: %s", port, exc)
    return False


def _log_advanced_recently(seconds: float = 600.0) -> bool:
    """Has any run log been appended to lately? Progress, not intent.

    A wedged run holds its lock and its process and writes nothing, which is the
    state this distinguishes from a healthy tool phase.
    """
    newest = 0.0
    roots = [
        pathlib.Path.home() / ".local-llm-bench",
        pathlib.Path.home() / "bench-logs",
        pathlib.Path.home() / "git/local-llm/hardware",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    newest = max(newest, path.stat().st_mtime)
                except OSError:
                    continue
    return bool(newest) and (time.time() - newest) < seconds


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("mode", choices=["sample", "watch", "report", "state"])
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
    if args.mode == "state":
        got = machine_state(args.log)
        level = logger.warning if got["actionable"] else logger.info
        level("%s -- %s", got["state"], got["why"])
        logger.info("%s", json.dumps(got))
        return 2 if got["actionable"] else 0
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
        "%s: %.1f%% busy over the %.2fh these %d samples actually cover "
        "(target %.0f%%, asked for %.0fh)%s",
        verdict,
        got["utilization"],
        got["span_hours"],
        got["samples"],
        got["target"],
        got["asked_for_hours"],
        ""
        if got["enough_data"]
        else " -- NOT ENOUGH DATA, do not quote this as a daily figure",
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
