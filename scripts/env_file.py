#!/usr/bin/env python3
"""Set and verify keys in a shell-sourced `.env`, so a value cannot go missing.

Third-party serving recipes keep their configuration in a `.env` that the
launcher **sources as bash**. A multi-word value written without quotes does
not fail: bash assigns the first word and then tries to *run* the rest.

    EXTRA_VLLM_ARGS=--default-chat-template-kwargs '{"reasoning_effort":"low"}'
    -> .env: line 182: {"reasoning_effort":"low"}: command not found

    EXTRA_DOCKER_ARGS=--cap-add IPC_LOCK -e VLLM_NVFP4_GEMM_BACKEND=marlin
    -> EXTRA_DOCKER_ARGS is UNSET, and bash tried to run `IPC_LOCK`

The second is worth reading twice. `VAR=value command args` is a *prefix
assignment*: it sets VAR only in the environment of that one command, so
after sourcing, the variable does not exist at all -- it is not left holding
the first word, which is the intuitive but wrong guess.

Both happened here on 2026-09-22, the second an hour after the first was
written up. Knowing the rule did not prevent repeating it, which makes it a
mechanism problem: nothing checked that the value survived. The cost is not
the typo, it is that a 320B-class two-node model takes about ten minutes to
load before anything reveals the setting never arrived.

So: write with `shlex.quote`, then source the file in bash and assert the
value came back byte for byte.

    uv run python scripts/env_file.py set .env GPU_MEMORY_UTILIZATION=0.78
    uv run python scripts/env_file.py set .env \\
        "EXTRA_DOCKER_ARGS=--cap-add IPC_LOCK -e VLLM_USE_FLASHINFER_MOE_FP4=0"
    uv run python scripts/env_file.py check .env EXTRA_DOCKER_ARGS

`check` is the part worth running even when something else wrote the file.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import shlex
import subprocess
import sys

logger = logging.getLogger(__name__)

#: Sourcing runs arbitrary code from the file, which is the whole point -- the
#: launcher will do exactly that. `set -a` exports, so a child sees what the
#: recipe would see. Bounded so a file that blocks cannot hang a session.
SOURCE_TIMEOUT_SECONDS = 20


class EnvRoundTripError(RuntimeError):
    """A key did not survive being sourced as shell."""


def parse_assignment(text: str) -> tuple[str, str]:
    """`KEY=value` -> (KEY, value). The value may contain anything, `=` included."""
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise ValueError(f"expected KEY=value, got {text!r}")
    if not key.replace("_", "").isalnum():
        raise ValueError(f"not a shell-safe key: {key!r}")
    return key, value


def render(key: str, value: str) -> str:
    """One assignment line, quoted so bash reproduces the value exactly.

    `shlex.quote` is the whole trick: it single-quotes when needed and escapes
    embedded single quotes, which is precisely the case that broke here --
    a JSON argument that itself carries single quotes.
    """
    return f"{key}={shlex.quote(value)}"


def read_via_bash(path: pathlib.Path, key: str) -> str | None:
    """Source the file the way the launcher does and print one variable.

    Returns None when the key is unset after sourcing, which is the silent
    failure this module exists to catch.
    """
    # Exit 3 for "unset" rather than a sentinel string: any sentinel could in
    # principle be the real value, and a printf escape is not portable across
    # bash builds -- an earlier version used \x00 and bash emitted it literally,
    # so "unset" and the four characters backslash-x-0-0 were indistinguishable.
    script = (
        f"set -a; . {shlex.quote(str(path))} >/dev/null 2>&1; set +a; "
        f'if [ -z "${{{key}+set}}" ]; then exit 3; fi; printf "%s" "${{{key}}}"'
    )
    r = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=SOURCE_TIMEOUT_SECONDS,
        check=False,
    )
    if r.returncode == 3:
        return None
    return r.stdout


def set_keys(path: pathlib.Path, assignments: list[str]) -> None:
    """Append or replace each assignment, then verify every one round-trips."""
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    for item in assignments:
        key, value = parse_assignment(item)
        line = render(key, value)
        for i, existing in enumerate(lines):
            if existing.split("=", 1)[0].strip() == key:
                lines[i] = line
                break
        else:
            lines.append(line)
        logger.info("set %s", line)
    path.write_text("\n".join(lines) + "\n")

    for item in assignments:
        key, value = parse_assignment(item)
        got = read_via_bash(path, key)
        if got != value:
            raise EnvRoundTripError(
                f"{key} did not survive sourcing: wrote {value!r}, bash gives {got!r}"
            )
        logger.info("verified %s", key)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="set keys, then verify they round-trip")
    s.add_argument("path", type=pathlib.Path)
    s.add_argument("assignment", nargs="+", help="KEY=value")
    c = sub.add_parser("check", help="verify keys are non-empty after sourcing")
    c.add_argument("path", type=pathlib.Path)
    c.add_argument("key", nargs="+")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "set":
        set_keys(args.path, args.assignment)
        return 0

    bad = False
    for key in args.key:
        got = read_via_bash(args.path, key)
        if got is None:
            logger.error("%s: UNSET after sourcing", key)
            bad = True
        elif got == "":
            logger.error(
                "%s: EMPTY after sourcing (a quoting error looks like this)", key
            )
            bad = True
        else:
            logger.info("%s=%s", key, got)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
