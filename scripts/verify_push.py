"""Verify a push landed, instead of trusting that `git push` reported success (#255).

A successful `git push` is not evidence that what you committed went anywhere.
Three incidents in one #235 session had this shape: a check ran, one command
later HEAD had moved, and the next `git` operation acted on a different branch
than the one just verified. The switch happens *between* commands, so a check
before an operation proves nothing about the operation after it. The one check
that is meaningful *after* the push is: is my branch's tip actually on the
remote?

This compares three facts:

  * the branch HEAD is on (`git rev-parse --abbrev-ref HEAD`),
  * that branch's local tip (`git rev-parse <branch>`),
  * that branch's tip on the remote (`git ls-remote origin <branch>`).

`ls-remote`, not the `origin/<branch>` tracking ref: a push that pushed the
wrong ref leaves the tracking ref stale too, so reading it back would repeat the
same lie. `ls-remote` asks the remote itself.

It catches all three incidents:

  * committed on a different branch than intended -> HEAD is not on `branch`;
  * `git push origin main` while on another branch -> HEAD is not on `main`;
  * the pushed ref did not move -> local tip != remote tip.

Usage, after a push::

    uv run python scripts/verify_push.py            # the current branch
    uv run python scripts/verify_push.py main       # a named branch

Exit status is 0 when the push is confirmed on the remote, 1 otherwise, so it
can gate whatever wraps a push.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import subprocess
import sys
from collections.abc import Callable, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[1]

#: How a git command is run. Injectable so tests can supply canned output
#: without a repository or a network.
Runner = Callable[[Sequence[str]], str]


def check_push(
    branch: str,
    current: str,
    local: str | None,
    remote: str | None,
) -> tuple[bool, str]:
    """Judge a push from three resolved facts. Pure, so the logic is testable.

    `local`/`remote` are None when the ref does not resolve -- an unknown ref is
    a failure, never a silent pass.
    """
    if current != branch:
        return False, (
            f"HEAD is on {current!r}, not {branch!r}: a push of {branch!r} did "
            f"not carry your commits. Checkout {branch!r} and push again."
        )
    if local is None:
        return False, f"local branch {branch!r} does not resolve"
    if remote is None:
        return False, (
            f"origin has no {branch!r}: the push did not create it (or pushed a "
            "different ref)."
        )
    if local != remote:
        return False, (
            f"{branch} is at {local[:7]} but origin/{branch} is at {remote[:7]}: "
            "the push did not land. `git push` may have reported success while "
            "pushing a ref that did not move."
        )
    return True, f"confirmed: {branch} is on origin at {local[:7]}"


def _run(args: Sequence[str]) -> str:
    proc = subprocess.run(
        list(args),
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def current_branch(*, run: Runner = _run) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])


def local_tip(branch: str, *, run: Runner = _run) -> str | None:
    return run(["git", "rev-parse", branch]) or None


def remote_tip(branch: str, *, run: Runner = _run) -> str | None:
    """The branch tip on the remote itself, via ls-remote (never the tracking ref)."""
    line = run(["git", "ls-remote", "origin", f"refs/heads/{branch}"])
    return line.split()[0] if line else None


def verify(branch: str | None = None, *, run: Runner = _run) -> tuple[bool, str]:
    """Resolve the three facts and judge the push. `branch` defaults to HEAD's.

    Scope: a same-name push (`git push origin <branch>`), which is every shape in
    #255. A refspec push like `feature:main` is not something this can verify --
    pass the destination branch name explicitly if you must. A detached HEAD is
    refused rather than guessed: there is no branch name to check against.
    """
    current = current_branch(run=run)
    if not current or current == "HEAD":
        return False, (
            "HEAD is detached (not on a branch): there is no branch to verify. "
            "Check out the branch you pushed, or pass its name."
        )
    target = branch or current
    return check_push(
        target,
        current,
        local_tip(target, run=run),
        remote_tip(target, run=run),
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "branch",
        nargs="?",
        default=None,
        help="the branch you pushed (default: the branch HEAD is on)",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    ok, message = verify(args.branch)
    if ok:
        logger.info("%s", message)
        return 0
    logger.error("%s", message)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
