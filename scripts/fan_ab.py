#!/usr/bin/env python3
"""Fans auto vs fans max, interleaved, on one build. #276 (parent #116)

The arm here is the **machine state**, not the code. One engine tree, one
GGUF, one sweep definition; the only thing that changes between phases is
whether the fans are under macOS thermal control or pinned to maximum.

    uv run python scripts/fan_ab.py ~/git/ds4-pr1014-base <gguf> out/

## Why interleaved, and why a cooldown

Thermal state carries across runs, so running all of A and then all of B lets
slow ambient drift align with condition. The phases alternate A,B,A,B,A,B and
each is preceded by a cooldown **with the fans on auto**, so every phase
starts from the same thermal policy rather than inheriting the previous
phase's.

The cooldown is a fixed wait rather than a wait-for-temperature. That is a
deliberate downgrade from what #276 specifies: the office is independently
air-conditioned (correlation with outdoor maximum 0.37, against 0.80 for
unconditioned rooms), so a target expressed against ambient can move while
you are waiting for it. A fixed 180 s is honest about being arbitrary; a
temperature target would look principled and still be chasing a moving room.
Each phase records the die temperature it actually started from, which is the
number that makes the comparison auditable either way.

## What it measures

Two outputs, and they are different findings:

- **throughput** -- does fan mode change tok/s at all?
- **within-phase drift** -- first rep to last. This is the one that matters.
  #274 measured decode falling 4.2-8.4% and prefill 8.5-14.7% inside a single
  run, and a 5-11% loss across 24 minutes of continuous work. If forced
  cooling flattens that, every paired A/B on this machine gets cheaper.

Reporting only a median answers the first and misses the second.

## Fans

`max` and `auto` only. `fancontrol set` is never called: it is the one
command that can hold fans *below* what thermal policy asks for. The restore
runs from a `finally`, an `atexit` hook and a SIGINT/SIGTERM handler, because
fans left forced are loud and nothing expires them but `auto` or a reboot.
"""

from __future__ import annotations

import argparse
import atexit
import datetime as dt
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import child
import decode_ab

import logs

logger = logging.getLogger(__name__)

FANCONTROL = "/usr/local/bin/fancontrol"

#: Phase order. Interleaved so ambient drift cannot align with condition.
PHASES = ("auto", "max", "auto", "max", "auto", "max")

#: Seconds of cooldown before each phase, fans on auto.
COOLDOWN_S = 180

CTX_START, CTX_MAX, STEP, GEN = 2048, 16384, 2048, 128


def _fan(action: str) -> dict[str, object] | None:
    """`fancontrol max|auto`, returning its JSON. Never raises.

    `sudo -n` refuses instead of prompting: a plain `sudo` waits on a password
    prompt that is invisible here and hangs until the timeout.
    """
    if action not in ("max", "auto"):  # `set` is out of scope, permanently.
        raise ValueError(f"refusing fan action {action!r}; only max and auto")
    try:
        got = subprocess.run(
            ["sudo", "-n", FANCONTROL, action, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("fancontrol %s failed to run: %s", action, exc)
        return None
    if got.returncode != 0:
        logger.error(
            "fancontrol %s exited %d: %s", action, got.returncode, got.stderr.strip()
        )
        return None
    try:
        return json.loads(got.stdout)
    except json.JSONDecodeError:
        logger.error("fancontrol %s printed non-JSON: %s", action, got.stdout[:200])
        return None


def fan_status() -> dict[str, object] | None:
    """Read-only; needs no sudo."""
    try:
        got = subprocess.run(
            [FANCONTROL, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return json.loads(got.stdout) if got.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


_RESTORED = False


def restore_fans() -> None:
    """Hand the fans back to macOS. Safe to call more than once."""
    global _RESTORED
    if _RESTORED:
        return
    _RESTORED = True
    logger.info("restoring fans to auto")
    got = _fan("auto")
    after = fan_status()
    mode = None
    if isinstance(after, dict):
        fans = after.get("fans")
        if isinstance(fans, list) and fans:
            mode = {f.get("mode") for f in fans if isinstance(f, dict)}
    if got is None or mode != {"auto"}:
        # Loud: fans left forced are loud and stay that way, and a silent
        # failure here is exactly the state nobody notices until the room is
        # noticeably wrong hours later.
        logger.error("FANS MAY STILL BE FORCED -- run: sudo -n %s auto", FANCONTROL)
    else:
        logger.info("fans confirmed auto")


def die_c() -> float | None:
    """Current GPU die temperature, or None. Never raises."""
    try:
        import thermals

        got = thermals.reading()
        value = got.get("die_max_c")
        return float(value) if isinstance(value, int | float) else None
    except Exception:  # a sensor must not stop a benchmark
        logger.debug("thermals unavailable", exc_info=True)
        return None


def now() -> str:
    """ISO 8601 with an explicit offset, matching the ledger and the logs."""
    return dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def one_phase(
    index: int,
    condition: str,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    prompt: pathlib.Path,
    reps: int,
    out: pathlib.Path,
) -> tuple[int, dict[str, object]]:
    """One condition's sweep. Returns (exit code, the phase record)."""
    tag = f"{index:02d}-{condition}"
    started_c = die_c()
    if condition == "max":
        _fan("max")
    else:
        _fan("auto")
    record: dict[str, object] = {
        "phase": index,
        "condition": condition,
        "started_iso": now(),
        "start_die_c": started_c,
        "fans_at_start": fan_status(),
        "reps": [],
    }
    logger.info(
        "phase %d/%d condition=%s start_die=%s",
        index,
        len(PHASES),
        condition,
        f"{started_c:.2f}C" if started_c is not None else "unreadable",
    )
    rc = 0
    for rep in range(1, reps + 1):
        csv = out / f"{tag}-rep{rep}.csv"
        log = out / f"{tag}-rep{rep}.log"
        began = now()
        rc = child.run(
            decode_ab.bench_argv(
                gguf,
                csv,
                prompt,
                binary=tree / "ds4-bench",
                ctx_start=CTX_START,
                ctx_max=CTX_MAX,
                step=STEP,
                gen=GEN,
            ),
            cwd=tree,
            log=log,
        )
        rep_rec = {
            "rep": rep,
            "started_iso": began,
            "ended_iso": now(),
            "die_c_after": die_c(),
            "csv": csv.name,
            "rc": rc,
        }
        record["reps"].append(rep_rec)  # type: ignore[union-attr]
        logger.info(
            "  %s rep %d -> rc=%d die=%s",
            tag,
            rep,
            rc,
            f"{rep_rec['die_c_after']:.2f}C"
            if rep_rec["die_c_after"] is not None
            else "?",
        )
        if rc != 0:
            logger.error("FAILED: %s rep %d -- see %s", tag, rep, log)
            for line in log.read_text(errors="replace").splitlines()[-15:]:
                logger.error("  %s", line)
            break
    record["ended_iso"] = now()
    record["end_die_c"] = die_c()
    return rc, record


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tree", type=pathlib.Path, help="engine tree with ds4-bench built")
    p.add_argument("gguf", type=pathlib.Path)
    p.add_argument("out", type=pathlib.Path)
    p.add_argument("--reps", type=int, default=4, help="sweeps per phase")
    p.add_argument("--cooldown", type=int, default=COOLDOWN_S)
    p.add_argument("--prompt", type=pathlib.Path, default=None)
    args = p.parse_args(argv)

    logs.configure()
    tree = args.tree.expanduser().resolve()
    gguf = args.gguf.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    binary = tree / "ds4-bench"
    if not binary.exists():
        logger.error("no ds4-bench in %s -- build it before taking the machine", tree)
        return 2
    if not gguf.exists():
        logger.error("no such gguf: %s", gguf)
        return 2
    prompt = args.prompt or (tree / "speed-bench" / "promessi_sposi.txt")
    if not prompt.exists():
        logger.error("no prompt file: %s", prompt)
        return 2

    # Restore on every exit path: normal return, exception, atexit, signal.
    atexit.register(restore_fans)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sys.exit(130))

    manifest: dict[str, object] = {
        "issue": 276,
        "started_iso": now(),
        "tree": str(tree),
        "tree_sha": subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip(),
        "gguf": gguf.name,
        "prompt": prompt.name,
        "phases_planned": list(PHASES),
        "reps_per_phase": args.reps,
        "cooldown_s": args.cooldown,
        "sweep": {"ctx_start": CTX_START, "ctx_max": CTX_MAX, "step": STEP, "gen": GEN},
        "phases": [],
    }
    rc = 0
    try:
        with decode_ab.run_lock("fan_ab.py auto vs max (#276)", os.getpid()):
            for i, condition in enumerate(PHASES, start=1):
                # Cool with the fans on AUTO before every phase, including the
                # max ones: each phase must start from the same thermal policy,
                # not inherit the previous phase's.
                _fan("auto")
                logger.info(
                    "cooldown %ds on auto before phase %d (%s); die=%s",
                    args.cooldown,
                    i,
                    condition,
                    f"{die_c():.2f}C" if die_c() is not None else "?",
                )
                time.sleep(args.cooldown)
                rc, record = one_phase(i, condition, tree, gguf, prompt, args.reps, out)
                manifest["phases"].append(record)  # type: ignore[union-attr]
                (out / "fan-ab-manifest.json").write_text(
                    json.dumps(manifest, indent=2)
                )
                if rc != 0:
                    logger.error("stopping after phase %d", i)
                    break
    finally:
        restore_fans()
        manifest["ended_iso"] = now()
        (out / "fan-ab-manifest.json").write_text(json.dumps(manifest, indent=2))
        logger.info("manifest: %s", out / "fan-ab-manifest.json")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
