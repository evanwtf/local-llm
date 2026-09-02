"""Secondary measurements taken around a trial: quality, and recall.

The oracle is binary and stays that way -- pytest passed or it did not, decided
by `results.verdict()`. Nothing here feeds into that. These are measurements
recorded *alongside* the verdict, because a benchmark where seven of eight
backends score 100% has stopped measuring anything (issue #4), and the first
question a ceiling raises is whether the passing solutions are equally good.

Three things get recorded.

**The solution itself.** Until now `run.py` deleted the worktree in a `finally`
and every trial's produced code went with it -- 398 trials, no artifact. Any
claim about which model writes better code had nothing behind it and could not
have. The patch is small; keep it.

**The repository's own gates.** ruff and mypy, configured by gmail-archive, not
by this benchmark. That matters: a rubric invented here would be a judgement,
and the harness's whole claim is that it does not judge. Counts are taken twice,
once on the excised tree and once after the agent, and reported as a delta --
gmail-archive carries 18 pre-existing mypy errors, so an absolute count would
mostly measure the repo.

**Whether the body came back verbatim.** gmail-archive was written with Claude.
Restoring a function that a model's own family authored is recall, not problem
solving, and METHODOLOGY.md flags it as the reason the hosted reference cannot
calibrate difficulty. It has never been checked. It is checkable: `excise` hands
back the exact body it removed, so compare.

Every function here is failure-tolerant on purpose. A measurement must never
take down a trial that has already cost twenty minutes of wall clock -- the same
rule `probe_server` follows. Absent is recorded as absent, never as zero: a zero
reads as "clean" and becomes a published claim.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import subprocess
from typing import Any

import excise
import swift_excise

logger = logging.getLogger(__name__)

# ruff and mypy are gmail-archive's own dev dependencies, declared in its
# pyproject. `uv run` inside the worktree gets the versions the repo pins.
DEFAULT_TOOLS = ["ruff", "mypy"]


def _normalize(body: str) -> str:
    """Collapse a body to what it does, ignoring how it is spaced.

    Reformatting is not a different solution. Comparing raw text would call a
    reflowed line a fresh implementation and hide exactly the recall this is
    looking for.
    """
    return " ".join(body.split())


def restored_verbatim(
    path: pathlib.Path, symbol: str, original: str, keep_docstring: bool = True
) -> bool | None:
    """Did the agent reproduce the original body?

    True means byte-for-byte modulo whitespace -- strong evidence of recall
    rather than reasoning, and the reason this benchmark cannot use a repo a
    frontier model wrote to calibrate difficulty.

    Returns None when the answer is unknowable: the agent may have left the file
    unparseable or removed the function outright. That is a failed trial, which
    the oracle already records; it is not a crash here.
    """
    # Same dispatch as run.py: the Python `ast` parser raises on Swift, which
    # would surface as `restored_verbatim: None` -- an unreadable file -- and
    # quietly lose the recall signal for a whole repository.
    reader = swift_excise if pathlib.PurePath(path).suffix == ".swift" else excise
    try:
        produced = reader.body_source(path, symbol, keep_docstring)
    except (
        OSError,
        SyntaxError,
        ValueError,
        excise.TargetNotFound,
        swift_excise.TargetNotFound,
    ) as exc:
        logger.debug("cannot read %s from %s: %s", symbol, path, exc)
        return None
    return _normalize(produced) == _normalize(original)


def save_solution(
    dest: pathlib.Path, name: str, worktree: pathlib.Path
) -> dict[str, Any]:
    """Keep the agent's diff, and hash it.

    The hash is the cheap half and the interesting one: identical hashes across
    trials say the model emitted the same bytes twice, which at temperature 1.0
    is worth knowing (#26). It lands in results.jsonl even when the patch file
    itself is later cleaned up.

    Patches hold repository content, so this follows `--client-log` and defaults
    outside this repo. Returns {} if anything at all goes wrong.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode != 0:
            logger.debug("git diff failed in %s: %s", worktree, proc.stderr.strip())
            return {}
        patch = proc.stdout
        if not patch.strip():
            return {"solution_empty": True}
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{name}.patch"
        out.write_text(patch)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("cannot save solution for %s: %s", name, exc)
        return {}
    return {
        "solution_patch": str(out),
        "solution_sha256": hashlib.sha256(patch.encode()).hexdigest(),
    }


def _count_ruff(stdout: str) -> int:
    """ruff prints one line per violation, then a summary."""
    return sum(1 for ln in stdout.splitlines() if ": " in ln and ln[:1] not in " -")


def _count_mypy(stdout: str) -> int:
    return sum(1 for ln in stdout.splitlines() if ": error:" in ln)


COUNTERS = {"ruff": _count_ruff, "mypy": _count_mypy}
ARGV = {"ruff": ["ruff", "check", "."], "mypy": ["mypy"]}


def gates(
    worktree: pathlib.Path, timeout: int, tools: list[str] | None = None
) -> dict[str, int]:
    """Run the repository's own checkers and count what they say.

    Returns a count per tool that ran. A tool that is missing, times out, or
    crashes is simply absent from the result -- never present as 0. Read a
    missing key as "not measured", and pair the result with `delta()`.
    """
    got: dict[str, int] = {}
    for tool in tools or DEFAULT_TOOLS:
        argv = ARGV.get(tool, [tool])
        try:
            proc = subprocess.run(
                ["uv", "run", *argv],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("gate %s did not run: %s", tool, exc)
            continue
        # `uv run` exits non-zero both for "tool reported problems" and for
        # "tool is not installed". Only the first has a count to read, and the
        # second must not be recorded as a clean zero.
        if proc.returncode != 0 and not proc.stdout.strip():
            logger.debug(
                "gate %s produced nothing: %s", tool, proc.stderr.strip()[:200]
            )
            continue
        counter = COUNTERS.get(tool)
        if counter is None:
            continue
        got[tool] = counter(proc.stdout)
    return got


def delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """How much worse did the agent make it?

    Measured against the excised tree, not against zero: gmail-archive has 18
    mypy errors of its own at the pinned commit, and an absolute count would
    report the repository's debt as the model's.

    Both sides must have measured the same tools. A one-sided delta is a
    subtraction against an unknown, so this returns nothing rather than a
    number that looks meaningful.
    """
    if not before or not after or set(before) != set(after):
        return {}
    return {k: after[k] - before[k] for k in before}


def all_restored_verbatim(
    excised: list[tuple[pathlib.Path, str, str]], keep_docstring: bool = True
) -> bool | None:
    """Did every hollowed-out symbol come back unchanged?

    True only if all of them did. A task that removes two coupled functions and
    gets one back verbatim is not a recall case -- it is a solved half -- so a
    partial match reports False.

    None if any target is unreadable, because "some were verbatim and one file
    no longer parses" is not an answer worth writing down.
    """
    answers = [
        restored_verbatim(p, sym, body, keep_docstring) for p, sym, body in excised
    ]
    if not answers or None in answers:
        return None
    return all(answers)
