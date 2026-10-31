"""Fixed-work load test at several GPU clock caps on both Sparks (#1319).

The question: what does a GPU graphics-clock cap (`nvidia-smi -lgc 0,MHZ`,
the sparkLimiter setting) cost in throughput, and what does it save in power
and heat? An agent run cannot answer it: its length depends on what the model
decides to do, and that noise is larger than the effect. This test does the
same work in every run:

  prefill  long prompts, max_tokens=1, a fresh nonce first so the prefix
           cache cannot serve them
  c1       one stream at a time, a fixed number of output tokens (ignore_eos)
  c2       two streams at a time, the same requests

Every prompt starts with a nonce that names the run, so no run reuses another
run's cache. Requests go to /v1/completions with a raw prompt and
temperature 0, so the chat template and the reasoning effort do not enter.

Each arm sets the cap on both nodes, waits for the GPUs to cool to the
starting temperature, then runs the work. The arm order rotates each round, so
a drift in room temperature does not line up with one arm. At the end,
whatever happens, the clocks are reset on both nodes, and a node whose
standing cap unit (gpu-clock-cap.service) is enabled gets that cap back.

    uv run python scripts/clock_cap_ab.py --base-url http://127.0.0.1:8888 \\
        --model DeepSeek-V4.1-Flash-1017-EXL3 --worker spark-b-cx7 \\
        --out ~/bench-logs/1319-clockcap/runs.jsonl --rounds 3

One JSON line per run: arm, round, UTC start and end, the temperatures at the
start, and per-phase wall time and token counts. Power, temperature and the
measured clock during the run come from Grafana afterwards, by time window.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import logging
import pathlib
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import decode_ab

import logs

logger = logging.getLogger(__name__)

UNCAPPED = "uncapped"
DEFAULT_ARMS = (UNCAPPED, "2500", "2200", "1900")
NVSMI = "/usr/bin/nvidia-smi"

# A fixed paragraph, repeated to make a long prompt. Its content does not
# matter; its length and its sameness across runs do.
FILLER = (
    "The archive stores each message once and indexes it by thread, sender, "
    "date and label. A search walks the index, not the messages, so it stays "
    "fast as the archive grows. Each import is idempotent: a message that is "
    "already present is skipped by its content hash. "
)
DECODE_PROMPT = (
    "Write a Python function that parses an RFC 5322 email address list, "
    "handling quoted display names, comments in parentheses, and group "
    "syntax. Explain each step as you go.\n\n"
)


def schedule(arms: Sequence[str], rounds: int) -> list[tuple[int, str]]:
    """(round, arm) pairs. Round r starts at arm r, so each arm takes each
    position in turn and room-temperature drift spreads over all arms."""
    out: list[tuple[int, str]] = []
    n = len(arms)
    for r in range(rounds):
        for i in range(n):
            out.append((r + 1, arms[(r + i) % n]))
    return out


def clock_command(arm: str) -> list[str]:
    """The nvidia-smi command that puts one node in this arm."""
    if arm == UNCAPPED:
        return ["sudo", "-n", NVSMI, "-rgc"]
    mhz = int(arm)
    if not 300 <= mhz <= 3003:
        raise ValueError(f"cap {mhz} MHz is outside 300..3003")
    return ["sudo", "-n", NVSMI, "-lgc", f"0,{mhz}"]


def nonce(run_id: str, phase: str, i: int) -> str:
    return f"[{run_id} {phase} {i}] "


def long_prompt(run_id: str, i: int, repeats: int) -> str:
    return nonce(run_id, "prefill", i) + FILLER * repeats + "\nSummarize the text."


def decode_prompt(run_id: str, phase: str, i: int) -> str:
    return nonce(run_id, phase, i) + DECODE_PROMPT


Runner = Callable[[list[str]], str]


def run_local(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def on_worker(worker: str) -> Runner:
    def run(cmd: list[str]) -> str:
        return run_local(["ssh", "-o", "BatchMode=yes", worker, " ".join(cmd)])

    return run


def set_arm(arm: str, runners: Sequence[Runner]) -> None:
    for run in runners:
        run(clock_command(arm))
    logger.info("clocks set to %s on %d node(s)", arm, len(runners))


#: The boot-time unit that holds the standing clock cap (#1319). An A/B
#: changes the clocks, so it restarts this unit at the end, where installed,
#: rather than leave the node uncapped until the next reboot.
STANDING_UNIT = "gpu-clock-cap.service"


def restore_standing(runners: Sequence[Runner]) -> list[bool]:
    """Restart the standing-cap unit on every node that has it enabled.
    Returns, per node, whether the unit was there to restart."""
    restored: list[bool] = []
    for run in runners:
        try:
            run(["systemctl", "is-enabled", "--quiet", STANDING_UNIT])
        except subprocess.CalledProcessError:
            restored.append(False)
            continue
        run(["sudo", "-n", "systemctl", "restart", STANDING_UNIT])
        restored.append(True)
    logger.info(
        "standing clock cap restored on %d of %d node(s)", sum(restored), len(runners)
    )
    return restored


def gpu_temp(run: Runner) -> int:
    out = run([NVSMI, "--query-gpu=temperature.gpu", "--format=csv,noheader"])
    return int(out.strip().splitlines()[0])


def cool_down(
    runners: Sequence[Runner],
    targets: Sequence[int],
    *,
    min_s: float,
    max_s: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[int]:
    """Wait at least min_s, then until every GPU is at or below its target,
    or max_s passes. Return the temperatures at the end."""
    start = clock()
    sleep(min_s)
    while True:
        temps = [gpu_temp(run) for run in runners]
        if all(t <= target for t, target in zip(temps, targets, strict=True)):
            return temps
        if clock() - start >= max_s:
            logger.warning("cool-down hit %.0f s with GPUs at %s", max_s, temps)
            return temps
        sleep(15)


def complete(
    base_url: str, model: str, prompt: str, max_tokens: int, ignore_eos: bool
) -> dict[str, Any]:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": 0,
            "ignore_eos": ignore_eos,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=1800) as response:
        payload = json.loads(response.read())
    usage = payload.get("usage") or {}
    return {
        "wall": time.monotonic() - start,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }


def phase(
    name: str, prompts: list[str], width: int, send: Callable[[str], dict[str, Any]]
) -> dict[str, Any]:
    """Send the prompts `width` at a time; return the phase totals."""
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=width) as pool:
        got = list(pool.map(send, prompts))
    wall = time.monotonic() - start
    out_tok = sum(r["output_tokens"] for r in got)
    in_tok = sum(r["prompt_tokens"] for r in got)
    result = {
        "phase": name,
        "requests": len(got),
        "width": width,
        "wall_s": round(wall, 3),
        "prompt_tokens": in_tok,
        "output_tokens": out_tok,
        "output_tps": round(out_tok / wall, 3) if wall else 0.0,
        "prompt_tps": round(in_tok / wall, 3) if wall else 0.0,
    }
    logger.info(
        "%s: %d requests, %.1f s, %.1f out tok/s, %.1f prompt tok/s",
        name,
        len(got),
        wall,
        result["output_tps"],
        result["prompt_tps"],
    )
    return result


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def one_run(args: argparse.Namespace, run_id: str) -> list[dict[str, Any]]:
    def send_long(p: str) -> dict[str, Any]:
        return complete(args.base_url, args.model, p, 1, False)

    def send_decode(p: str) -> dict[str, Any]:
        return complete(args.base_url, args.model, p, args.max_tokens, True)

    prefill = [long_prompt(run_id, i, args.repeats) for i in range(args.prefill_n)]
    c1 = [decode_prompt(run_id, "c1", i) for i in range(args.c1_n)]
    c2 = [decode_prompt(run_id, "c2", i) for i in range(args.c2_n)]
    return [
        phase("prefill", prefill, 1, send_long),
        phase("c1", c1, 1, send_decode),
        phase("c2", c2, 2, send_decode),
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8888")
    p.add_argument("--model", required=True)
    p.add_argument("--worker", required=True, help="ssh name of the worker node")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--arms", nargs="+", default=list(DEFAULT_ARMS))
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--prefill-n", type=int, default=8)
    p.add_argument("--repeats", type=int, default=400, help="filler repeats")
    p.add_argument("--c1-n", type=int, default=24)
    p.add_argument("--c2-n", type=int, default=48)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--cool-min-s", type=float, default=180)
    p.add_argument("--cool-max-s", type=float, default=900)
    p.add_argument("--cool-margin", type=int, default=3, help="degrees C")
    p.add_argument("--no-lock", action="store_true", help="tests and smoke only")
    args = p.parse_args(argv)
    logs.configure()

    runners: list[Runner] = [run_local, on_worker(args.worker)]
    plan = schedule(args.arms, args.rounds)
    for arm in args.arms:
        clock_command(arm)  # refuse a bad arm before anything runs
    args.out.parent.mkdir(parents=True, exist_ok=True)

    lock = (
        decode_ab.run_lock("#1319 clock-cap A/B", owner_pid=__import__("os").getpid())
        if not args.no_lock
        else __import__("contextlib").nullcontext()
    )
    with lock:
        set_arm(UNCAPPED, runners)
        idle = [gpu_temp(run) for run in runners]
        targets = [t + args.cool_margin for t in idle]
        logger.info("idle GPU temps %s; cool-down targets %s", idle, targets)
        try:
            for n, (rnd, arm) in enumerate(plan, 1):
                set_arm(arm, runners)
                temps = cool_down(
                    runners, targets, min_s=args.cool_min_s, max_s=args.cool_max_s
                )
                run_id = f"r{rnd}-{arm}-{int(time.time())}"
                logger.info("run %d/%d: round %d, arm %s", n, len(plan), rnd, arm)
                start = utc_now()
                phases = one_run(args, run_id)
                row = {
                    "issue": 1319,
                    "run_id": run_id,
                    "round": rnd,
                    "arm": arm,
                    "start_utc": start,
                    "end_utc": utc_now(),
                    "start_gpu_temps_c": temps,
                    "phases": phases,
                    "settings": {
                        k: getattr(args, k)
                        for k in ("prefill_n", "repeats", "c1_n", "c2_n", "max_tokens")
                    },
                }
                with args.out.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
        finally:
            set_arm(UNCAPPED, runners)
            restore_standing(runners)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
