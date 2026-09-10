"""Paired decode-rate A/B for two GGUFs of the same model (#48). #235 stage 4.

Port of `scripts/decode_ab.sh`. The `.sh` stays until a run agrees with it.

Why a sweep and not three runs: a 3-trial agent median carries +/-28% (#23),
and the effect we are chasing is ~9.5%. `ds4-bench` decodes greedily at fixed
token counts across N context frontiers, so one invocation yields N paired
points per model -- a far tighter instrument than the agent suite, and it
measures decode rate directly instead of inferring it from wall time.

    uv run python scripts/decode_ab.py q4 ~/models/a.gguf q8 ~/models/b.gguf

## Why this driver, second

`greedy_mtp_ab` was the first port and it is a server-and-agent driver. This
one is deliberately the opposite shape -- no server, no agent, no shim: it
runs `ds4-bench` directly and writes CSV. If `ab_driver` only fits drivers
that start servers, that is worth finding out on the second adopter rather
than the ninth. It fits: an arm here declares `ab_driver.nothing` as its
server, which is the seam saying "this driver manages its own process".

## What the shell got right, and this keeps

**The refusals happen before the lock is held and 84 GiB is resident.** A typo
should cost a second, not a model load.

`PREFILL_CHUNK=0` is refused rather than passed through: `0` means
*unspecified* to ds4, not *unlimited* -- `ds4_prefill_cap_for_prompt`
(`ds4.c:12986 at ds4-main 9ab70534`) takes the `requested_chunk != 0` branch
or nothing, so 0 falls through to the variant default.

Above 8192 it warns instead of refusing, because measuring that on purpose is
valid: `metal_graph_prefill_chunked` (`ds4.c:36867 at ds4-main 9ab70534`)
clamps every prefill after the first to `raw_cap`, which
`metal_graph_raw_cap_for_context` (`ds4.c:37541 at ds4-main 9ab70534`) ceilings
at 8192. Frontier 1 honours the request and the rest are cut -- one run, two
quantities.

The shell's own test file records that its `case` guard did not catch `"00"`,
and that a numeric test added afterwards is what refuses it. Here the parse is
`int()` against an explicit digits-only check, and `"00"` is in the test
parameters for the same reason it is in the shell's.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import logging
import os
import pathlib
import subprocess
import sys
from collections.abc import Iterator, Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import ab_driver
import child
import preflight

import logs

logger = logging.getLogger(__name__)

RAW_CAP = 8192
"""`metal_graph_raw_cap_for_context` ceilings raw_cap here."""


class Refusal(RuntimeError):
    """A reason not to start, raised before anything expensive happens."""


def prefill_chunk(raw: str | None) -> int | None:
    """Validate PREFILL_CHUNK. None when unset; raises Refusal when unusable.

    Digits only, and positive. `"00"` parses as 0 under `int()` and must be
    refused like `"0"` -- the shell's `case` guard missed exactly that, and
    the test that caught it is still in `test_decode_ab.py`.
    """
    if raw is None or raw == "":
        return None
    if not raw.isdigit():
        raise Refusal(f"PREFILL_CHUNK={raw!r} is not a positive integer")
    value = int(raw)
    if value == 0:
        raise Refusal(
            f"PREFILL_CHUNK={raw!r} means 'unspecified' to ds4, not 'unlimited'"
        )
    if value > RAW_CAP:
        logger.warning(
            "PREFILL_CHUNK=%d exceeds the raw_cap ceiling (%d). Frontier 1 will "
            "use %d; every later frontier will use %d. "
            "ds4.c:36867 at ds4-main 9ab70534 and "
            "ds4.c:37541 at ds4-main 9ab70534. "
            "One run, two quantities.",
            value,
            RAW_CAP,
            value,
            RAW_CAP,
        )
    return value


def bench_argv(
    gguf: pathlib.Path,
    csv: pathlib.Path,
    prompt: pathlib.Path,
    *,
    binary: pathlib.Path,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    chunk: int | None = None,
) -> list[str]:
    """One `ds4-bench` command line.

    `chunk` is either present or absent -- there is no empty-flag case to get
    wrong. The shell needed `${prefill_flag[@]+"${prefill_flag[@]}"}` here
    because bash 3.2 aborts on an empty array under `set -u`, and that is the
    same defect that cost `greedy_mtp_ab.sh` its control arm.
    """
    argv = [
        str(binary),
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
    if chunk is not None:
        argv += ["--prefill-chunk", str(chunk)]
    return argv


def engines_text(
    ds4: pathlib.Path,
    labels: Sequence[tuple[str, pathlib.Path]],
    *,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    reps: int,
    chunk: int | None,
    head: str | None,
    dirty: bool,
    binary_mtime: str | None,
) -> str:
    """The provenance sidecar, written before the first arm runs.

    #192: `DS4` selects the binary AND the `metal/*.metal` shaders it loads at
    runtime, so the tree is part of the measurement, not a path detail. It is
    written first because a sweep that dies halfway should still say what it
    was.
    """
    when = (
        datetime.datetime.now(datetime.UTC).astimezone().isoformat(timespec="seconds")
    )
    lines = [f"# weights A/B, {when}", f"engine tree={ds4} @ {head or 'unknown'}"]
    if dirty:
        lines.append(
            "engine_dirty=true   # uncommitted code: the sha does not name this binary"
        )
    lines.append(f"binary_mtime={binary_mtime or 'unknown'}")
    for position, (label, gguf) in enumerate(labels):
        lines.append(f"{'AB'[position]} label={label} gguf={gguf}")
    lines.append(
        f"sweep ctx_start={ctx_start} ctx_max={ctx_max} step={step} "
        f"gen={gen} reps={reps}"
    )
    lines.append(f"prefill_chunk={chunk if chunk is not None else '<unset>'}")
    for var in ("DS4_METAL_PREFILL_CHUNK", "DS4_METAL_GRAPH_RAW_CAP"):
        lines.append(f"{var}={os.environ.get(var, '<unset>')}")
    return "\n".join(lines) + "\n"


def git_out(tree: pathlib.Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(tree), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None


@contextlib.contextmanager
def run_lock(what: str, owner_pid: int) -> Iterator[None]:
    """Claim the machine for the whole sweep.

    #133: claim before loading anything. preflight sees the process table but
    cannot see intent, and this script spends minutes between arms with
    nothing running -- a scan in that window truthfully says "all clear" while
    the machine is committed for hours.
    """
    taken, why = preflight.acquire_lock(what, pid=owner_pid)
    if not taken:
        raise Refusal(f"could not claim the machine: {why}")
    logger.info("machine lock held: %s", why)
    try:
        yield
    finally:
        released, why = preflight.release_lock(pid=owner_pid)
        logger.info("machine lock released=%s: %s", released, why)


def sweep(
    arms_in: Sequence[tuple[str, pathlib.Path]],
    reps: int,
    out: pathlib.Path,
    prompt: pathlib.Path,
    *,
    ds4: pathlib.Path,
    ctx_start: int,
    ctx_max: int,
    step: int,
    gen: int,
    chunk: int | None,
    owner_pid: int,
    allow_odd: bool = False,
) -> int:
    """The whole sweep. Returns a process exit code."""
    # Refuse an uneven rep count HERE, before the lock and before any build
    # check. `ab_driver.run` refuses it too, but it is called inside the lock,
    # and a refusal after the machine is claimed is a refusal that already
    # cost something. The shell checked it at line 56, a hundred lines before
    # it took the lock, and that ordering is part of the behavior.
    if not ab_driver.leads_equally(reps, len(arms_in)) and not allow_odd:
        raise Refusal(
            f"reps={reps} over {len(arms_in)} arms does not let each arm lead "
            "equally often, so alternation cannot cancel the position bias "
            "(#130, #201) -- up to +5.9% on a cold first rep, larger than most "
            "effects this measures. Use an even count, or --allow-odd-reps."
        )
    out.mkdir(parents=True, exist_ok=True)
    binary = ds4 / "ds4-bench"

    stamp_prompt(prompt, sidecar=out)
    mtime = None
    if binary.exists():
        mtime = (
            datetime.datetime.fromtimestamp(binary.stat().st_mtime, datetime.UTC)
            .astimezone()
            .isoformat(timespec="seconds")
        )
    text = engines_text(
        ds4,
        arms_in,
        ctx_start=ctx_start,
        ctx_max=ctx_max,
        step=step,
        gen=gen,
        reps=reps,
        chunk=chunk,
        head=git_out(ds4, "rev-parse", "--short", "HEAD"),
        dirty=bool(git_out(ds4, "status", "--porcelain")),
        binary_mtime=mtime,
    )
    (out / "engines.txt").write_text(text)
    logger.info("%s", text.rstrip())

    built = [
        ab_driver.Arm(name=label, backend=str(gguf), serve=ab_driver.nothing)
        for label, gguf in arms_in
    ]

    def one_arm(arm: ab_driver.Arm, tag: str, rep: int) -> int:
        csv = out / f"{arm.name}-rep{rep}.csv"
        position = [a.name for a in ab_driver.order(built, rep)].index(arm.name) + 1
        # Record the order this arm ran in, so a later reader can test for
        # positional bias instead of assuming it away (#130 item 3).
        with (out / "run-order.txt").open("a") as handle:
            handle.write(
                f"rep={rep} position={position} of {len(built)} label={arm.name}\n"
            )
        log = out / f"{arm.name}-rep{rep}.log"
        logger.info("%s rep %d (position %d) -> %s", arm.name, rep, position, csv)
        # child.run, not subprocess.run: a driver stopped mid-sweep must take
        # ds4-bench with it (#268). This also moves the arm's stderr from the
        # batch stream into its own file, which the shell did not do -- ds4
        # prints its Metal route and pipeline fallbacks at startup, and
        # interleaved in one stream those cannot be diffed between arms.
        #
        # ds4-bench resolves metal/*.metal relative to its own tree, so run
        # from there. Without this it dies with
        # "metal/activations.metal not found".
        rc = child.run(
            bench_argv(
                pathlib.Path(arm.backend),
                csv,
                prompt,
                binary=binary,
                ctx_start=ctx_start,
                ctx_max=ctx_max,
                step=step,
                gen=gen,
                chunk=chunk,
            ),
            cwd=ds4,
            log=log,
        )
        if rc != 0:
            logger.error("FAILED: %s rep %d -- see %s", arm.name, rep, log)
            for line in log.read_text(errors="replace").splitlines()[-20:]:
                logger.error("  %s", line)
            return rc
        # Stamp immediately, not at the end: a run that dies halfway still
        # leaves CSVs, and an unstamped one cannot be told from a
        # differently-prompted one.
        stamp_prompt(prompt, stamp=csv)
        return rc

    with run_lock(f"decode_ab.py {built[0].name} vs {built[1].name}", owner_pid):
        failed = ab_driver.run(built, reps, one_arm, allow_uneven=allow_odd)
    logger.info("done: %s", out)
    return ab_driver.report(failed, reps, len(built))


def stamp_prompt(
    prompt: pathlib.Path,
    *,
    sidecar: pathlib.Path | None = None,
    stamp: pathlib.Path | None = None,
) -> None:
    """#140: name the prompt, or the prefill half of this A/B is not well-posed.

    @adamlawi measured the same Q4-vs-Q8 question on one box at +2.5% with a
    135 kB prompt and at parity with a 405 kB one. The prompt is an input to
    the result, so it goes on the rows and in a sidecar, not in a default
    nobody wrote down.
    """
    argv = [
        "uv",
        "run",
        "python",
        str(REPO / "scripts" / "prompt_meta.py"),
        "--prompt",
        str(prompt),
    ]
    if sidecar is not None:
        argv += ["--sidecar", str(sidecar), "--show"]
    if stamp is not None:
        argv += ["--stamp", str(stamp)]
    subprocess.run(argv, cwd=REPO, check=False)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Paired decode-rate A/B (#48).")
    p.add_argument("label_a")
    p.add_argument("gguf_a", type=pathlib.Path)
    p.add_argument("label_b")
    p.add_argument("gguf_b", type=pathlib.Path)
    p.add_argument("outdir", nargs="?", type=pathlib.Path, default=None)
    p.add_argument("--reps", type=int, default=int(os.environ.get("REPS", "4")))
    p.add_argument(
        "--ctx-start", type=int, default=int(os.environ.get("CTX_START", "2048"))
    )
    p.add_argument(
        "--ctx-max", type=int, default=int(os.environ.get("CTX_MAX", "16384"))
    )
    p.add_argument("--step", type=int, default=int(os.environ.get("STEP", "2048")))
    p.add_argument("--gen", type=int, default=int(os.environ.get("GEN", "128")))
    p.add_argument(
        "--allow-odd-reps",
        action="store_true",
        default=os.environ.get("ALLOW_ODD_REPS") == "1",
    )
    args = p.parse_args(argv)

    logs.configure()

    ds4 = pathlib.Path(os.environ.get("DS4", pathlib.Path.home() / "git" / "ds4"))
    prompt = pathlib.Path(
        os.environ.get("PROMPT", ds4 / "speed-bench" / "promessi_sposi.txt")
    )
    # #203: absolutize before the lock. The arm runs with cwd=$DS4, so a
    # relative outdir is created under the repo but resolved under the ds4
    # tree by --csv, and the CSV files land nowhere. The default is absolute,
    # which is why the bug only shows on a hand-passed relative path.
    out = (args.outdir or REPO / "benchmarks" / "ds4" / "decode-ab").resolve()

    try:
        chunk = prefill_chunk(os.environ.get("PREFILL_CHUNK"))
    except Refusal as exc:
        logger.error("REFUSING: %s", exc)
        return 1

    if args.reps % 2 and not args.allow_odd_reps:
        logger.error(
            "REFUSING: REPS=%d is odd. Alternation cancels the position bias "
            "only on an even count, and the bias is up to +5.9%% on a cold "
            "first rep (#201) -- larger than most effects this script is used "
            "to measure. Use an even REPS, or --allow-odd-reps to override.",
            args.reps,
        )
        return 2

    try:
        return sweep(
            [(args.label_a, args.gguf_a), (args.label_b, args.gguf_b)],
            args.reps,
            out,
            prompt,
            ds4=ds4,
            ctx_start=args.ctx_start,
            ctx_max=args.ctx_max,
            step=args.step,
            gen=args.gen,
            chunk=chunk,
            owner_pid=os.getpid(),
            allow_odd=args.allow_odd_reps,
        )
    except (Refusal, ValueError) as exc:
        logger.error("REFUSING: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
