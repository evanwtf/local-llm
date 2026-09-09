#!/usr/bin/env python3
"""Greedy coherence check before trusting any new GGUF (#25, #48).

Port of `scripts/coherence_check.sh` (#235).

A model can load, serve, and report entirely plausible token counts while
emitting noise -- that is #25, and it cost hours. Run this at `--temp 0` on
every new or requantized model BEFORE any benchmark, and read the output with
your own eyes. A benchmark cannot tell prose from gibberish.

    uv run python scripts/coherence_check.py ~/models/qwen-Q4_K.gguf

## Every refusal happens before the first model loads

The shell looped and let each `ds4` invocation fail on its own, so a typo in
the fourth path was found after three models had been paged in. A missing
binary, a missing tree, or a missing GGUF is known from the arguments alone,
so all of them are checked first and nothing starts.

## `TOKENS` did not do what it says

The shell read `TOKENS=${TOKENS:-200}` and spent it on `tail -n "$TOKENS"`.
It trimmed the OUTPUT; it never bounded the generation. A reader setting
`TOKENS=50` to get a quick look still paid for a full-length generation and
then saw 50 lines of it.

ds4 has the flag the name promised -- `-n, --tokens N  Maximum generated
tokens` (`~/git/ds4` at 399acbbe, `./ds4 --help`) -- so `--tokens` is passed
to ds4 here and the whole output is shown. This is a deliberate behaviour
change, not a transcription: the check gets shorter and cheaper, and the
number in the flag is now the number of tokens generated.

The full output of every model is kept in `--log-dir` regardless, so a run
that turns out to be interesting can be re-read without re-running it.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import shutil
import sys
import time
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import child

import logs

logger = logging.getLogger(__name__)

#: The prompt has to be answerable in prose AND in code, because the two fail
#: separately: #25's model wrote clean Python and then explained it in word
#: salad. One or the other alone would have passed it.
PROMPT = (
    "Write a Python function that returns the nth Fibonacci number, then "
    "explain in one sentence why the iterative form is preferred over the "
    "naive recursive one."
)

DEFAULT_TREE = pathlib.Path.home() / "git" / "ds4"
DEFAULT_LOG_DIR = pathlib.Path.home() / "bench-logs" / "coherence"
DEFAULT_TOKENS = 200
DEFAULT_CTX = 8192

#: The model's own words, quoted rather than timestamped. Its own logger with
#: its own handler: the driver's lines keep the ISO 8601 stamp, and 200 lines
#: of generated prose do not get a timestamp glued to each one -- this output
#: is read by eye to judge prose from gibberish, and a stamp per line is the
#: thing that makes that hard.
transcript = logging.getLogger("coherence.transcript")


def check_inputs(tree: pathlib.Path, ggufs: Sequence[pathlib.Path]) -> list[str]:
    """Everything wrong that is knowable without loading a model."""
    problems = []
    binary = tree / "ds4"
    if not tree.is_dir():
        problems.append(f"no ds4 tree at {tree}")
    elif not (binary.is_file() and shutil.which(str(binary))):
        problems.append(f"{binary} is not an executable file")
    if not ggufs:
        problems.append("no model given")
    for gguf in ggufs:
        if not gguf.is_file():
            problems.append(f"missing {gguf}")
    return problems


def argv_for(
    tree: pathlib.Path,
    gguf: pathlib.Path,
    *,
    prompt: str,
    tokens: int,
    ctx: int,
) -> list[str]:
    """The command line, stated here so a test can read it without a GPU.

    `--temp 0` is the whole point: #25's noise was not reproducible under
    sampling, so a check that sampled could not be re-run against a fix.
    """
    return [
        str(tree / "ds4"),
        "-m",
        str(gguf),
        "-p",
        prompt,
        "--temp",
        "0",
        "--tokens",
        str(tokens),
        "--ctx",
        str(ctx),
    ]


def check_one(
    gguf: pathlib.Path,
    *,
    tree: pathlib.Path,
    log_dir: pathlib.Path,
    prompt: str,
    tokens: int,
    ctx: int,
    timeout: float | None,
) -> int:
    """Run one model and show what it said. Returns ds4's exit status."""
    stamp = time.strftime("%Y%m%dT%H%M%S")
    log = log_dir / f"{stamp}-{gguf.stem}.log"
    logger.info("MODEL %s", gguf.name)
    logger.info("log %s", log)
    # cwd is the ds4 tree: ds4 resolves metal/*.metal relative to its own
    # tree, so a run started anywhere else loads no Metal kernels.
    status = child.run(
        argv_for(tree, gguf, prompt=prompt, tokens=tokens, ctx=ctx),
        cwd=tree,
        log=log,
        timeout=timeout,
    )
    transcript.info("=" * 60)
    transcript.info("MODEL: %s", gguf.name)
    transcript.info("=" * 60)
    transcript.info("%s", log.read_text(errors="replace").rstrip())
    transcript.info("")
    if status != 0:
        logger.error("%s: ds4 exited %d -- read %s", gguf.name, status, log)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("gguf", nargs="+", type=pathlib.Path)
    p.add_argument("--tree", type=pathlib.Path, default=DEFAULT_TREE)
    p.add_argument("--log-dir", type=pathlib.Path, default=DEFAULT_LOG_DIR)
    p.add_argument("--prompt", default=PROMPT)
    p.add_argument(
        "--tokens",
        type=int,
        default=DEFAULT_TOKENS,
        help="maximum tokens GENERATED (the shell trimmed output instead)",
    )
    p.add_argument("--ctx", type=int, default=DEFAULT_CTX)
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="seconds before the whole process group is stopped",
    )
    args = p.parse_args(argv)

    logs.configure()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(logs.PLAIN))
    # Replace rather than append, and set the level here rather than inherit
    # it. `logs.configure` is `basicConfig`, which is a no-op once the root
    # has a handler -- so under a caller that configured logging first, the
    # root can still be at WARNING and every line of the model's output is
    # dropped before it reaches this handler. The transcript IS the product
    # of this script; it does not get to depend on somebody else's level.
    transcript.handlers = [handler]
    transcript.setLevel(logging.INFO)
    transcript.propagate = False

    problems = check_inputs(args.tree, args.gguf)
    if problems:
        for problem in problems:
            logger.error("%s", problem)
        return 2

    args.log_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for gguf in args.gguf:
        if check_one(
            gguf,
            tree=args.tree,
            log_dir=args.log_dir,
            prompt=args.prompt,
            tokens=args.tokens,
            ctx=args.ctx,
            timeout=args.timeout,
        ):
            failed += 1
    if failed:
        logger.error("%d of %d models did not run", failed, len(args.gguf))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
