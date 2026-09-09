#!/usr/bin/env python3
"""Fans auto vs fans max, interleaved, on one build. #276 (parent #116)

The arm here is the **machine state**, not the code. One engine tree, one
GGUF, one sweep definition; the only thing that changes between phases is
whether the fans are under macOS thermal control or pinned to maximum.

    uv run python scripts/fan_ab.py ~/git/ds4-pr1014-base <gguf> out/

## Why interleaved, and why a cooldown

Thermal state carries across runs, so running all of A and then all of B lets
slow ambient drift align with condition. The phases alternate A,B,A,B,A,B and
each is preceded by a cooldown **with the fans on max**, so every phase starts
from the same floor rather than inheriting the previous phase's heat. The
cooldown's fan mode is deliberately NOT the phase's: uniformity is the point,
and cooling on max is the same wait made shorter. The phase's own mode is then
set and held for `SETTLE_IN_S` so the switch transient lands before the first
rep instead of inside it -- otherwise dropping max->auto at t=0 would put a
warming ramp on the auto arm alone.

The cooldown waits for the die temperature to **stop falling**, not to reach
a value. This is a derivative test, not a margin, and the reason is measured:
across 15,027 sustained-idle samples (GPU exactly 0 for >=300 s, CPU < 0.10)
the die median is 36.73 C but p5-p95 spans 31.14-45.12 C, and **within a
single 60 s idle window the reading already wanders a median of 1.77 C
peak-to-peak, 8.27 C at p90**. So "within 1 C of idle" would fire on which
sample you happened to read. Worse, "idle" is not a constant to aim at: it
moves 14 C across the day with ambient and background load.

The test is a **slope**, not a difference of consecutive means. That
distinction cost a revision: two consecutive 30 s medians differing by less
than 0.3 C tests whether the change is *small*, and the question is whether
the change is *over*. A die cooling at a constant `r` C/hour moves `r/120` C
between consecutive 30 s windows, so the first version of this gate passed
every cooling rate below **36 C/hour**. The office ambient watcher hit the
identical failure the same evening -- its own difference-of-means bar was
satisfied while the room fell monotonically at 1.0 C/hour.

The bound is measured, not guessed. Over the three genuinely-idle stretches
in a day of monitord samples (GPU 0 and CPU < 0.10 continuously for >= 300 s,
835 samples past the dwell), the trailing 120 s slope of a settled die has a
median of 0.120 C/min and a p90 of 0.277 C/min -- that is sensor noise fitted
by least squares. `SETTLE_MAX_SLOPE` sits at that p90: below it the wait would
rarely end at true idle, above it the wait ends on noise.

A slope needs no knowledge of the floor, so it survives the office minisplit
stepping the room mid-wait, which an absolute target does not.

It can fail, so it says which way it ended. `outcome` is `plateau`,
`timeout` (the ceiling elapsed and the die was still moving), or `no_sensor`
(thermals unreadable; falls back to a fixed wait). A phase preceded by a
timeout started from a machine still shedding heat, and the manifest has to
let a reader see that rather than infer it.

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
import thermal_settle

import logs

logger = logging.getLogger(__name__)

FANCONTROL = "/usr/local/bin/fancontrol"

#: Phase order. Interleaved so ambient drift cannot align with condition.
PHASES = ("auto", "max", "auto", "max", "auto", "max")

#: Cooldown gate. The die has stopped falling when the least-squares slope
#: over the trailing SETTLE_WINDOW_S seconds is flatter than SETTLE_MAX_SLOPE.
#: See the module docstring for why this is a slope and not a difference of
#: means, and `scripts/calibrate_settle.py` for where the bound comes from.
SETTLE_SAMPLE_S = 5
SETTLE_WINDOW_S = 180
SETTLE_MAX_SLOPE = 0.3  # C/minute; the p90 of a settled die's own noise
SETTLE_MIN_S = 90
#: The bulk cooldown runs with the fans on MAX regardless of the phase that
#: follows. Forced cooling removes 23% of the idle gradient (die 36.53 -> 33.36
#: C measured 2026-09-09), and on auto the die needed >610 s to stop falling
#: from 74 C. Cooling on max is the same wait, shorter.
#:
#: Every phase therefore starts from the SAME floor, which is what the
#: cooldown is for -- uniformity, not a particular temperature. It also biases
#: conservatively: the auto arm gets a cooler start than ordinary auto
#: operation would give it, so if max fans still win, the win is not the
#: starting point.
COOL_ON_MAX = True
#: After the bulk cool, the phase's own fan mode is set and held this long
#: before the first rep. The mode switch has a transient -- dropping from max
#: to auto lets the die climb toward the higher auto floor -- and it must
#: happen BEFORE the measurement, not inside the first rep of the auto arm
#: only.
SETTLE_IN_S = 120
#: 420 s was not enough and the run said so. Measured 2026-09-09: after three
#: reps the die sat at 74.11 C, and 420 s later it had reached 34.13 C but was
#: STILL falling at 0.809 C/min. Fitting a decay to that (floor ~31.5 C, time
#: constant ~3.2 min) puts the 0.30 C/min bound at ~610 s. 900 gives margin
#: without being unbounded -- and a timeout is recorded, not silent, so a
#: ceiling that turns out short is visible in the manifest rather than
#: inferred later from phases that started unequal.
SETTLE_TIMEOUT_S = 900

#: Used only when the sensor is unreadable and the plateau test cannot run.
FALLBACK_COOLDOWN_S = 180

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


def cool_to_plateau(
    label: str,
    min_s: int = SETTLE_MIN_S,
    timeout_s: int = SETTLE_TIMEOUT_S,
    max_slope: float = SETTLE_MAX_SLOPE,
) -> dict[str, object]:
    """Wait until the die temperature stops falling. Returns what happened.

    Least-squares slope over the trailing `SETTLE_WINDOW_S` seconds; settled
    when it is flatter than `max_slope` C/minute. A slope, because a slope is
    zero only when the quantity has stopped moving -- a difference of means is
    satisfied indefinitely by any slow steady fall, which is how the first
    version of this gate came to pass every cooling rate under 36 C/hour.

    Never raises, and always returns a record. The outcome matters as much as
    the wait -- a phase that began after a `timeout` started from a machine
    still shedding heat, and the manifest must say so.
    """
    began = time.monotonic()
    began_iso = now()
    first = die_c()  # read once: two calls can straddle a sensor update
    logger.info(
        "%s: waiting for the die to stop falling (floor %ds, ceiling %ds, "
        "settled at |slope| < %.2f C/min over %ds); die now %s",
        label,
        min_s,
        timeout_s,
        max_slope,
        SETTLE_WINDOW_S,
        f"{first:.2f}C" if first is not None else "?",
    )
    samples: list[tuple[float, float]] = []  # (elapsed seconds, die C)
    outcome = "timeout"
    last_slope: float | None = None
    #: How many times the slope test actually produced a number. A timeout
    #: with zero of these is a different failure from a timeout with many:
    #: the first means the test never ran, the second means the die really
    #: was still moving. The ambient watcher on the other machine spent six
    #: minutes reporting `n/a` and continuing, which reads in a log exactly
    #: like a test that keeps saying "not yet".
    evaluations = 0

    while True:
        elapsed = time.monotonic() - began
        if elapsed >= timeout_s:
            break
        value = die_c()
        if value is not None:
            samples.append((elapsed, value))
        elif elapsed >= SETTLE_WINDOW_S and not samples:
            # The sensor has been silent for a whole window. Do not spin for
            # seven minutes reading nothing -- say so and take a fixed wait.
            outcome = "no_sensor"
            break

        if elapsed >= min_s:
            done, slope = thermal_settle.settled(
                samples,
                elapsed,
                width_s=SETTLE_WINDOW_S,
                max_slope_c_per_min=max_slope,
            )
            if slope is not None:
                last_slope = slope
                evaluations += 1
            if done:
                outcome = "plateau"
                break
        time.sleep(SETTLE_SAMPLE_S)

    waited = int(time.monotonic() - began)
    if outcome == "no_sensor":
        logger.warning(
            "%s: thermals unreadable after %ds -- falling back to a fixed %ds wait",
            label,
            waited,
            FALLBACK_COOLDOWN_S,
        )
        time.sleep(FALLBACK_COOLDOWN_S)
        waited = int(time.monotonic() - began)

    last = die_c()
    record: dict[str, object] = {
        "outcome": outcome,
        "waited_s": waited,
        "started_iso": began_iso,
        "ended_iso": now(),
        "start_die_c": first,
        "end_die_c": last,
        "last_slope_c_per_min": (
            round(last_slope, 4) if last_slope is not None else None
        ),
        "samples": len(samples),
        "evaluations": evaluations,
        "settle": {
            "kind": "slope",
            "window_s": SETTLE_WINDOW_S,
            "sample_s": SETTLE_SAMPLE_S,
            "max_slope_c_per_min": max_slope,
            "min_s": min_s,
            "timeout_s": timeout_s,
        },
    }
    if outcome == "timeout" and evaluations == 0:
        # Never evaluated. Say so as its own outcome rather than letting it
        # read as "the die was still falling for seven minutes".
        outcome = "no_fit"
        logger.error(
            "%s: the settle test never evaluated in %ds -- %d readings, and a "
            "%ds window never held enough to fit. This phase begins on an "
            "UNKNOWN thermal state, not a hot one",
            label,
            waited,
            len(samples),
            SETTLE_WINDOW_S,
        )
    level = logger.warning if outcome in ("timeout", "no_fit") else logger.info
    level(
        "%s: %s after %ds -- die %s -> %s, last slope %s (bound %.2f C/min)",
        label,
        outcome,
        waited,
        f"{first:.2f}C" if first is not None else "?",
        f"{last:.2f}C" if last is not None else "?",
        f"{last_slope:+.3f} C/min" if last_slope is not None else "n/a",
        max_slope,
    )
    return record


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
    p.add_argument(
        "--settle-min", type=int, default=SETTLE_MIN_S, help="floor on each cooldown"
    )
    p.add_argument(
        "--settle-timeout",
        type=int,
        default=SETTLE_TIMEOUT_S,
        help="ceiling on each cooldown; exceeding it is recorded, not fatal",
    )
    p.add_argument(
        "--settle-in",
        type=int,
        default=SETTLE_IN_S,
        help="seconds on the phase's own fan mode before its first rep",
    )
    p.add_argument(
        "--cool-on-max",
        action=argparse.BooleanOptionalAction,
        default=COOL_ON_MAX,
        help="run the bulk cooldown with fans forced to max",
    )
    p.add_argument(
        "--settle-slope",
        type=float,
        default=SETTLE_MAX_SLOPE,
        help="C/minute below which the die counts as settled",
    )
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

    def stop(*_: object) -> None:
        """Exit on the FIRST signal, and ignore every one after it.

        A second signal during teardown raises a second `SystemExit` from
        wherever the first had reached, which skips the rest of the unwind.
        That happened on 2026-09-09: `uv` forwarded the SIGTERM it received
        while a direct one was also sent, the second landed inside
        `child.terminate`, and a `ds4-bench` orphan kept the GPU after the
        driver had already released the machine lock and reported clean.
        """
        for each in (signal.SIGINT, signal.SIGTERM):
            signal.signal(each, signal.SIG_IGN)
        sys.exit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)

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
        "cooldown": {
            "kind": "slope",
            "min_s": args.settle_min,
            "timeout_s": args.settle_timeout,
            "max_slope_c_per_min": args.settle_slope,
            "cooled_on": "max" if args.cool_on_max else "auto",
            "settle_in_s": args.settle_in,
            "window_s": SETTLE_WINDOW_S,
            "sample_s": SETTLE_SAMPLE_S,
        },
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
                _fan("max" if args.cool_on_max else "auto")
                cooled = cool_to_plateau(
                    f"cooldown before phase {i} ({condition})",
                    min_s=args.settle_min,
                    timeout_s=args.settle_timeout,
                    max_slope=args.settle_slope,
                )
                # Set the phase's own fan mode and absorb the switch transient
                # here, where it is not being measured.
                _fan(condition)
                logger.info(
                    "settle-in %ds on %s before phase %d; die=%s",
                    args.settle_in,
                    condition,
                    i,
                    f"{die_c():.2f}C" if die_c() is not None else "?",
                )
                time.sleep(args.settle_in)
                cooled["cooled_on"] = "max" if args.cool_on_max else "auto"
                cooled["settle_in_s"] = args.settle_in
                cooled["die_after_settle_in_c"] = die_c()
                rc, record = one_phase(i, condition, tree, gguf, prompt, args.reps, out)
                record["cooldown"] = cooled
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
