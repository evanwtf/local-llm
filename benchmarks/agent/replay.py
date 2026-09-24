"""Replay tasks (#714): rebuild a real feature commit from its own tests.

An excision task deletes one function body and asks for it back. On the
cluster ledger seven of eight stacks pass every trial of those, so they have
stopped separating models (#711). A replay task is a larger, multi-file unit
of real work taken from the target repository's own history:

  * export the tree at commit C, exactly as an excision task exports its base;
  * put every non-test file the commit touched back to its state at C^ (the
    first parent), deleting the ones C created;
  * keep C's tests, which must now fail -- the ordinary control check;
  * the oracle is those tests passing, and `touched_tests` guards them the
    same way it guards an excision task's.

The revert list is written out in tasks.toml rather than derived from the
commit, so a reader sees exactly which files were withheld and a change of
policy (say, keeping `pyproject.toml` at C so the test environment still
resolves) is a reviewed edit, not an accident of `git diff --name-only`.

Everything here reads the source repository's object store (`git cat-file`)
and never its working tree, so the result depends on C and nothing else.
"""

from __future__ import annotations

import difflib
import pathlib
import subprocess
from typing import Any

KIND = "replay"

#: Fields a replay task must carry. `tests` is the oracle and `revert` is the
#: work; without either the control check proves nothing about the task.
REQUIRED = ("base_commit", "revert", "tests", "prompt")


def is_replay(task: dict[str, Any]) -> bool:
    return task.get("kind") == KIND


def validate(task: dict[str, Any]) -> list[str]:
    """Why this replay task definition cannot run. Empty means it can.

    Checked statically so a typo costs a test failure, not a trial.
    """
    name = task.get("name", "?")
    errors = []
    for key in REQUIRED:
        if not task.get(key):
            errors.append(f"{name}: replay task needs a non-empty `{key}`")
    for key in ("file", "symbol", "targets"):
        if key in task:
            errors.append(
                f"{name}: replay task restores whole files; `{key}` is for excision"
            )
    revert = task.get("revert") or []
    tests = task.get("tests") or []
    if len(set(revert)) != len(revert):
        errors.append(f"{name}: `revert` lists a path twice")
    for path in revert:
        # A reverted test would take away the oracle it is judged by.
        if path in tests or pathlib.PurePosixPath(path).parts[:1] == ("tests",):
            errors.append(f"{name}: `revert` must not include a test file ({path})")
        if pathlib.PurePosixPath(path).is_absolute() or ".." in path.split("/"):
            errors.append(f"{name}: `revert` path must be repo-relative ({path})")
    return errors


def _git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def resolve(source: pathlib.Path, rev: str) -> str:
    """The full sha of `rev` in `source`. Raises RuntimeError if absent."""
    got = _git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], source)
    if got.returncode != 0:
        raise RuntimeError(f"{rev} is not a commit in {source}")
    return got.stdout.decode().strip()


def parent_of(source: pathlib.Path, commit: str) -> str:
    """C^ -- the FIRST parent, which is the branch the feature landed on."""
    return resolve(source, f"{commit}^1")


def blob(source: pathlib.Path, rev: str, path: str) -> bytes | None:
    """The bytes of `path` at `rev`, or None when the path does not exist there."""
    got = _git(["cat-file", "blob", f"{rev}:{path}"], source)
    return got.stdout if got.returncode == 0 else None


def _prune_empty_parents(path: pathlib.Path, root: pathlib.Path) -> None:
    """Remove directories a deletion left empty, up to but never including root.

    git does not track directories, so C^ has no empty `sources/` package; the
    export should not have one either, or its presence is a hint.
    """
    parent = path.parent
    root = root.resolve()
    while parent.resolve() != root and root in parent.resolve().parents:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def revert(
    source: pathlib.Path, commit: str, paths: list[str], worktree: pathlib.Path
) -> list[dict[str, str]]:
    """Put each path in `worktree` back to its state at C's first parent.

    A path that did not exist at C^ is deleted -- the commit created it, so
    the agent has to as well. Returns what was done per path, in order, for
    the row.
    """
    parent = parent_of(source, commit)
    done = []
    for rel in paths:
        target = worktree / rel
        content = blob(source, parent, rel)
        if content is None:
            if target.exists():
                target.unlink()
                _prune_empty_parents(target, worktree)
            done.append({"path": rel, "action": "deleted"})
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            done.append({"path": rel, "action": "restored"})
    return done


def track_new_files(worktree: pathlib.Path) -> None:
    """Make files the agent created visible to `git diff HEAD`.

    The harness reads both the saved patch and `touched_tests` from
    `git diff HEAD`, which does not list untracked files. An excision task
    rarely needs a new file; a replay task usually does -- the commit created
    a module, and the agent has to as well -- so without this the patch would
    omit most of the solution, and a conftest.py the agent added under tests/
    would not count as touching the tests. `add --intent-to-add` records only
    that the path exists, respects .gitignore (caches stay out), and changes
    no file content. Failure-tolerant: it runs after a trial has already cost
    its wall clock.
    """
    _git(["add", "--intent-to-add", "--all", "."], worktree)


def commit_size(source: pathlib.Path, commit: str, paths: list[str]) -> dict[str, int]:
    """Lines C added and removed in the reverted paths: the size of the task."""
    parent = parent_of(source, commit)
    got = _git(["diff", "--numstat", parent, commit, "--", *paths], source)
    added = removed = 0
    for line in got.stdout.decode().splitlines():
        a, r, _ = line.split("\t", 2)
        if a != "-":  # binary files report "-"
            added += int(a)
            removed += int(r)
    return {"added": added, "removed": removed}


def _lines_differing(a: bytes | None, b: bytes | None) -> int:
    """Added plus removed lines between two versions (absent is empty)."""
    left = (a or b"").decode("utf-8", "replace").splitlines()
    right = (b or b"").decode("utf-8", "replace").splitlines()
    return sum(
        1
        for line in difflib.unified_diff(left, right, lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    )


def recall(
    worktree: pathlib.Path, source: pathlib.Path, commit: str, paths: list[str]
) -> dict[str, Any]:
    """How close the agent's files came to C's own, per reverted path.

    `verbatim` is byte-for-byte equality with C's version (for a file C
    deleted: still absent). It is the replay analogue of `restored_verbatim`
    -- a recall signal, since the target repository is public and a model
    may have trained on it -- and it never reaches the verdict.

    `lines_vs_commit` counts the lines that differ from C's version, and
    `lines_vs_parent` those that differ from the reverted starting state: the
    size of what the agent wrote in that file.
    """
    parent = parent_of(source, commit)
    files = []
    for rel in paths:
        path = worktree / rel
        try:
            produced = path.read_bytes() if path.exists() else None
        except OSError:
            files.append({"path": rel, "verbatim": None})
            continue
        want = blob(source, commit, rel)
        files.append(
            {
                "path": rel,
                "verbatim": produced == want,
                "lines_vs_commit": _lines_differing(want, produced),
                "lines_vs_parent": _lines_differing(
                    blob(source, parent, rel), produced
                ),
            }
        )
    answers = [f["verbatim"] for f in files]
    return {
        "verbatim": None if None in answers or not answers else all(answers),
        "files": files,
    }
