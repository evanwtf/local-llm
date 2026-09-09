#!/usr/bin/env python3
"""Paired decode-rate A/B for one Metal knob env var within one tree. #162

Port of `scripts/metal_knob_ab.sh` (#235). The knob table and every refusal
already lived in Python; this replaces the shell that called them.

`decode_ab.py` varies the weights and `decode_ab_engine.sh` varies the tree.
Nothing varied the environment within one tree, which is what this does: one
Metal knob env var, two arms, same tree, same GGUF.

    uv run python scripts/metal_knob_ab.py <knob> <on-value> <off-value> \\
        <tree> <gguf> [outdir]

## Why the port is more than a translation here

The shell ran `uv run python scripts/metal_knob_ab.py <subcommand>` **ten
times** to fetch values it then interpolated back into a command line -- shell
calling Python calling shell, paying an interpreter start per lookup and
turning every value into a string that had to survive word splitting. The knob
table was always the source of truth; only the caller was in the wrong
language. The library moved to `lib/metal_knob.py` and this calls it.

**The presence knob is why `unitctl.start` grew `unset`.** `gathered-heads` is
on by default and has no REQUIRE spelling, so its ON arm must *unset*
`DS4_METAL_DISABLE_DECODE_RAW_GATHERED_ATTN` -- `=0` still counts as set
(`getenv(...) != NULL`) and would take the raw-only path in **both** arms,
producing two identical arms wearing different labels. The shell said this with
`env -u`; a dict cannot say it, which is the same absence #149's R arm is
defined by.

The unquoted `env $env_prefix` expansion the shell relied on -- `-u VAR`
splitting into two words and `VAR=value` into one -- is gone with it. That
worked, and it worked because both values came from a validated table; it was
one hand-passed value away from not working.

## What must not be lost

**An even rep count is refused, not warned.** Alternation cancels the position
bias only on an even count. At REPS=3, reps 1 and 3 run A-first and only rep 2
runs B-first, so the bias lands 2:1 on one arm. Across #171's twelve reps
whichever arm ran first was faster in 9, median +0.9%, +5.9% on the first rep
of a cold session. An odd sweep produces a complete CSV, a plausible number,
and no indication that half the design is missing.

**OUT is absolutized before the lock (#203).** Each arm runs with `cwd=TREE`,
so a relative OUT is created under the repo by `mkdir` and resolved under the
ds4 tree by `--csv` -- the CSV files land nowhere. The default OUT is absolute,
which is why the bug only ever showed on a hand-passed relative path.

**A knob with no admission signal is refused unless acknowledged.** Its rows
are marked `admission_signal: none` so they cannot later be read as verified.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import pathlib
import sys
import time
from collections.abc import Iterator, Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import child
import metal_knob
import preflight
import prompt_meta

import logs

logger = logging.getLogger(__name__)

# The same frontiers and gen budget as decode_ab and ds4's own speed-bench, so
# the numbers stay comparable to speed-bench/m5_max.csv.
CTX_START = 2048
CTX_MAX = 16384
STEP = 2048
GEN = 128
REPS = 4


class Refusing(RuntimeError):
    """A wrong arm, refused before the lock or any measurement."""


def absolutize(out: pathlib.Path) -> pathlib.Path:
    """#203: resolve OUT before anything runs with a different cwd.

    An arm runs with `cwd=TREE`, so a relative OUT is created under the repo by
    `mkdir` and resolved under the ds4 tree by `--csv`. The CSVs land nowhere
    and the run looks fine until the read-out finds no rows.
    """
    return out if out.is_absolute() else (pathlib.Path.cwd() / out).resolve()


def bench_argv(
    gguf: pathlib.Path,
    prompt: pathlib.Path,
    csv: pathlib.Path,
    *,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
) -> list[str]:
    """`ds4-bench` for one arm. Relative binary: it resolves metal/*.metal
    against its own tree, so it runs with `cwd=TREE`."""
    return [
        "./ds4-bench",
        "-m",
        str(gguf),
        "--metal",
        "--prompt-file",
        str(prompt),
        "--ctx-start",
        str(ctx_start),
        "--ctx-max",
        str(ctx_max),
        "--step-incr",
        str(step),
        "--gen-tokens",
        str(gen),
        "--csv",
        str(csv),
    ]


def run_bench(
    argv: Sequence[str],
    tree: pathlib.Path,
    log: pathlib.Path,
    header: str,
    env: dict[str, str],
    unset: Sequence[str],
) -> int:
    """One `ds4-bench` invocation, with the arm's environment and its header.

    The header goes on the log so every measurement carries its own evidence:
    which var, which value, and whether the on arm unsets it. The admission
    probe can then read the log rather than trust the table.
    """
    log.write_text(header + "\n")
    # child.run with append=True: the header stays, the arm's env and unset go
    # to the child, and the whole process group dies with this driver (#268).
    return child.run(argv, cwd=tree, log=log, env=env, unset=unset, append=True)


def engagement(
    knob: str,
    label: str,
    value: str,
    *,
    out: pathlib.Path,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    prompt: pathlib.Path,
) -> int:
    """One short single-frontier pass with the trace on. Returns the count.

    The trace prints per dispatch, so it stays off the timed arms -- tracing
    inside a timed arm would add I/O to one arm and not the other.
    """
    log = out / f"engagement-{label}.log"
    env, unset = metal_knob.arm_env(knob, label, value)
    trace = metal_knob.trace_var(knob)
    if trace:
        # A print knob has no trace var. The shell had to build the assignment
        # rather than interpolate it, because an empty var name hands `env` a
        # bare `=1` and kills the arm. A dict cannot have an empty key here.
        env = {**env, trace: "1"}
    argv = bench_argv(
        gguf,
        prompt,
        out / f"engagement-{label}.csv",
        ctx_start=CTX_START,
        ctx_max=CTX_START,
        step=STEP,
        gen=GEN,
    )
    header = (
        f"# engagement: {label} knob={knob} "
        f"env {metal_knob.arm_cmd(knob, label, value)}"
        f"{' ' + trace + '=1' if trace else ''} {' '.join(argv)}"
    )
    logger.info("engagement %s -> %s", label, log)
    run_bench(argv, tree, log, header, env, unset)
    return metal_knob.count_trace_lines(log, pattern=metal_knob.admission_pattern(knob))


def check_admission(
    knob: str,
    on_value: str,
    off_value: str,
    *,
    out: pathlib.Path,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    prompt: pathlib.Path,
) -> tuple[int, int]:
    """Run the engagement passes this knob's signal needs. Returns (on, off).

    (0, 0) for a knob whose signal is neither `print` nor `count` -- there is
    nothing to run, and the rows say `admission_signal: none` so they are never
    read as verified.
    """
    signal = metal_knob.admission_signal(knob)
    if signal not in ("print", "count"):
        return 0, 0
    kw = {"out": out, "tree": tree, "gguf": gguf, "prompt": prompt}
    on = engagement(knob, "on", on_value, **kw)
    off = engagement(knob, "off", off_value, **kw)
    if signal == "print" and not metal_knob.print_admission_ok(on, off):
        raise Refusing(
            f"knob {knob} did not engage as expected (on={on} off={off} "
            "admission lines); the on arm must print at least one and the off "
            "arm none"
        )
    if signal == "count" and not metal_knob.count_admission_ok(on, off):
        raise Refusing(
            f"knob {knob} did not engage (on={on} off={off} trace lines); the "
            "on arm must exceed the off arm and both must be non-zero"
        )
    return on, off


def run_meta(
    knob: str,
    on_value: str,
    off_value: str,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    reps: int,
    counts: tuple[int, int],
) -> dict[str, object]:
    """What went on the run, so a later reader can tell what produced the rows.

    Built as a dict and written with `json.dump`, not as three heredocs with a
    conditional comma between them. The shell's version could emit invalid JSON
    if the middle block was ever skipped for a reason the comma did not follow.
    """
    signal = metal_knob.admission_signal(knob)
    meta: dict[str, object] = {
        "knob": knob,
        "on_var": metal_knob.on_var(knob),
        "off_var": metal_knob.off_var(knob),
        "on_value": on_value,
        "off_value": off_value,
        "admission_signal": signal,
        "tree": str(tree),
        "gguf": str(gguf),
        "reps": str(reps),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if signal in ("count", "print"):
        meta["engagement"] = {
            "trace_var": metal_knob.trace_var(knob),
            "pattern": metal_knob.admission_pattern(knob),
            "on_count": str(counts[0]),
            "off_count": str(counts[1]),
        }
    return meta


def order(rep: int) -> tuple[str, str]:
    """#130: odd reps on-off, even reps off-on, so drift divides across arms."""
    return ("off", "on") if rep % 2 == 0 else ("on", "off")


def run_arm(
    knob: str,
    label: str,
    value: str,
    rep: int,
    position: int,
    *,
    out: pathlib.Path,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    prompt: pathlib.Path,
    ctx_max: int,
) -> None:
    """One timed arm. Raises Refusing if the on arm failed closed."""
    csv = out / f"{label}-rep{rep}.csv"
    log = out / f"{label}-rep{rep}.log"
    env, unset = metal_knob.arm_env(knob, label, value)
    argv = bench_argv(
        gguf, prompt, csv, ctx_start=CTX_START, ctx_max=ctx_max, step=STEP, gen=GEN
    )
    logger.info("%s rep %d (position %d) -> %s", label, rep, position, csv)
    header = (
        f"# arm: {label} knob={knob} env "
        f"{metal_knob.arm_cmd(knob, label, value)} {' '.join(argv)}"
    )
    run_bench(argv, tree, log, header, env, unset)
    # The on arm's REQUIRE spelling must not fail closed. There is no positive
    # admission print, so absence of the error is the only signal, and it must
    # be checked rather than assumed.
    if label == "on" and metal_knob.check_fail_closed(knob, log):
        raise Refusing(f"{label} failed closed (knob {knob} did not take effect)")
    prompt_meta.stamp(csv, prompt)


@contextlib.contextmanager
def machine(what: str, owner_pid: int) -> Iterator[None]:
    """#133: claim the machine before loading anything.

    preflight sees the process table but cannot see intent, and this driver
    spends minutes between arms with nothing running -- a scan in that window
    truthfully says "all clear" while the machine is committed for hours.
    """
    taken, why = preflight.acquire_lock(what, pid=owner_pid)
    if not taken:
        raise Refusing(f"the machine is claimed by another run: {why}")
    logger.info("machine lock held: %s", why)
    try:
        yield
    finally:
        released, why = preflight.release_lock(pid=owner_pid)
        logger.info("machine lock released=%s: %s", released, why)


def sweep(
    knob: str,
    on_value: str,
    off_value: str,
    tree: pathlib.Path,
    gguf: pathlib.Path,
    out: pathlib.Path,
    *,
    reps: int,
    prompt: pathlib.Path,
    ctx_max: int,
    ack_no_signal: bool,
    owner_pid: int,
) -> int:
    # Refuse a wrong arm before the lock or any measurement. The negative cases
    # are the whole job: an empty value is a wrong arm waiting to happen, an
    # unknown knob is a typo that would run a different experiment, and a knob
    # with no admission signal must never be measured by accident.
    metal_knob.validate(knob, on_value, off_value, acknowledge_no_signal=ack_no_signal)

    # The cheap check first: a missing build used to fail mid-run, after the
    # lock was held and the model loaded.
    bench = tree / "ds4-bench"
    if not (bench.exists() and os.access(bench, os.X_OK)):
        raise Refusing(f"{bench} is missing or not executable -- build it first")

    with machine(f"metal_knob_ab.py {knob}", owner_pid):
        out.mkdir(parents=True, exist_ok=True)
        prompt_meta.sidecar(prompt, out, show=True)
        counts = check_admission(
            knob, on_value, off_value, out=out, tree=tree, gguf=gguf, prompt=prompt
        )
        (out / "run-meta.json").write_text(
            json.dumps(
                run_meta(knob, on_value, off_value, tree, gguf, reps, counts), indent=2
            )
            + "\n"
        )
        for rep in range(1, reps + 1):
            for position, label in enumerate(order(rep), 1):
                value = on_value if label == "on" else off_value
                with (out / "run-order.txt").open("a") as handle:
                    handle.write(f"rep={rep} position={position} of 2 label={label}\n")
                run_arm(
                    knob,
                    label,
                    value,
                    rep,
                    position,
                    out=out,
                    tree=tree,
                    gguf=gguf,
                    prompt=prompt,
                    ctx_max=ctx_max,
                )
    logger.info("done: %s", out)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("knob")
    p.add_argument("on_value")
    p.add_argument("off_value")
    p.add_argument("tree", type=pathlib.Path)
    p.add_argument("gguf", type=pathlib.Path)
    p.add_argument(
        "out",
        nargs="?",
        type=pathlib.Path,
        default=REPO / "benchmarks" / "ds4" / "metal-knob-ab",
    )
    p.add_argument("--reps", type=int, default=int(os.environ.get("REPS", REPS)))
    p.add_argument(
        "--ctx-max", type=int, default=int(os.environ.get("CTX_MAX", CTX_MAX))
    )
    p.add_argument("--prompt", type=pathlib.Path, default=None)
    p.add_argument(
        "--allow-odd-reps",
        action="store_true",
        default=os.environ.get("ALLOW_ODD_REPS") == "1",
    )
    p.add_argument(
        "--ack-no-signal",
        action="store_true",
        default=os.environ.get("METAL_KNOB_ACK_NO_SIGNAL") == "1",
        help="measure a knob with no admission signal. Without it validate() "
        "refuses, so a knob that cannot be verified is never measured by "
        "accident.",
    )
    args = p.parse_args(argv)

    logs.configure()

    if args.reps % 2:
        if not args.allow_odd_reps:
            logger.error(
                "REFUSING: REPS=%d is odd. Alternation cancels the position "
                "bias only on an even count, and the bias is up to +5.9%% on a "
                "cold first rep (#201) -- larger than most effects this script "
                "is used to measure. Use an even count, or --allow-odd-reps.",
                args.reps,
            )
            return 2
        logger.warning(
            "REPS=%d is odd; alternation cannot cancel the position bias "
            "(#201). Proceeding because --allow-odd-reps was given.",
            args.reps,
        )

    prompt = args.prompt or (args.tree / "speed-bench" / "promessi_sposi.txt")
    try:
        return sweep(
            args.knob,
            args.on_value,
            args.off_value,
            args.tree,
            args.gguf,
            absolutize(args.out),
            reps=args.reps,
            prompt=prompt,
            ctx_max=args.ctx_max,
            ack_no_signal=args.ack_no_signal,
            owner_pid=os.getpid(),
        )
    except (Refusing, ValueError, SystemExit) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
