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

Two extensions make a task harder without a judge model (#726):

  * **A span.** `span_start` names the first of several consecutive commits
    that build one feature, and `base_commit` the last. The revert then puts
    each path back to its state at `span_start^`, so the agent rebuilds the
    whole stack -- 1,000+ lines -- not one commit.
  * **Hidden tests.** `hidden_tests` lists pytest node ids the agent never
    sees. They are cut out of its tree before the starting commit (a whole
    file is deleted; a test function or class is removed from its file), and
    only the oracle puts them back: from `base_commit`, or from the later
    commit `hidden_ref` when they are regression tests the repository added
    afterwards. The row records their verdict separately (`hidden_passed`);
    `passed` stays the visible oracle, so the #714 series keeps its meaning.

Everything here reads the source repository's object store (`git cat-file`)
and never its working tree, so the result depends on the commits and nothing
else.
"""

from __future__ import annotations

import ast
import contextlib
import difflib
import pathlib
import re
import subprocess
from collections.abc import Iterator
from typing import Any

KIND = "replay"

#: Fields a replay task must carry. `tests` is the oracle and `revert` is the
#: work; without either the control check proves nothing about the task.
REQUIRED = ("base_commit", "revert", "tests", "prompt")

#: The node ids a hidden test may have: a file under tests/, then at most a
#: class and a function. No parametrize brackets -- one parameter case cannot
#: be cut out of a file, so it could not be hidden.
_HIDDEN_NODE = re.compile(r"^tests/[\w./-]+\.py(::[A-Za-z_]\w*){0,2}$")

#: The one line the prompt of a hidden-test task must carry, so the agent is
#: told the check is wider than what it can read. Checked in
#: test_task_definitions.py.
HIDDEN_NOTICE = "The check also runs tests you cannot see."


def is_replay(task: dict[str, Any]) -> bool:
    return task.get("kind") == KIND


def suite(task: dict[str, Any]) -> str:
    """Which replay set a task belongs to: "714" (the first seven) or its own.

    `--replay` runs the #714 set, as it always has; the harder tasks (#726)
    carry `suite = "hard"` and run with `--replay-hard`, so neither batch
    quietly grows when the other does.
    """
    return str(task.get("suite") or "714")


def start_of(task: dict[str, Any]) -> str:
    """The first commit whose work the task reverts: `span_start` or C."""
    return task.get("span_start") or task["base_commit"]


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
    for key in ("span_start", "hidden_ref", "suite"):
        if key in task and not (isinstance(task[key], str) and task[key]):
            errors.append(f"{name}: `{key}` must be a non-empty string")
    if task.get("prompt_names_tests", True) is False:
        # #726: the agent has to find what to run. A test path left in the
        # prompt would quietly turn the task back into the ordinary kind.
        for test in task.get("tests") or []:
            path = test.split("::")[0]
            if path in (task.get("prompt") or ""):
                errors.append(f"{name}: prompt names {path} but must name no test")
    elif "prompt_names_tests" in task and task["prompt_names_tests"] is not True:
        errors.append(f"{name}: `prompt_names_tests` must be true or false")
    if task.get("hidden_tests") and HIDDEN_NOTICE not in (task.get("prompt") or ""):
        errors.append(
            f"{name}: a task with hidden tests must say so: {HIDDEN_NOTICE!r}"
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
    return errors + _validate_hidden(name, task, tests)


def _validate_hidden(name: str, task: dict[str, Any], tests: list[str]) -> list[str]:
    if "hidden_tests" not in task:
        if "hidden_ref" in task:
            return [f"{name}: `hidden_ref` without `hidden_tests`"]
        return []
    hidden = task["hidden_tests"]
    if not isinstance(hidden, list) or not hidden:
        return [f"{name}: `hidden_tests` must be a non-empty list"]
    errors = []
    if len(set(hidden)) != len(hidden):
        errors.append(f"{name}: `hidden_tests` lists a test twice")
    for node in hidden:
        if not isinstance(node, str) or not _HIDDEN_NODE.match(node):
            errors.append(
                f"{name}: hidden test {node!r} is not tests/<file>.py[::Class][::test]"
            )
            continue
        # A visible entry inside a hidden one would put hidden tests back in
        # front of the agent (or name a node that no longer exists there).
        for visible in tests:
            if visible == node or visible.startswith(node + "::"):
                errors.append(f"{name}: visible test {visible} is inside hidden {node}")
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


def span_commits(source: pathlib.Path, start: str, end: str) -> int:
    """How many first-parent commits `start..end` covers, both ends included.

    Raises RuntimeError when `start` is not an ancestor of `end`: a span that
    runs backwards would revert to a state after the commit the tests are
    from, and every path would be "restored" to something unrelated.
    """
    first, last = resolve(source, start), resolve(source, end)
    if _git(["merge-base", "--is-ancestor", first, last], source).returncode != 0:
        raise RuntimeError(f"span start {start} is not an ancestor of {end}")
    got = _git(
        [
            "rev-list",
            "--count",
            "--first-parent",
            f"{parent_of(source, first)}..{last}",
        ],
        source,
    )
    return int(got.stdout.decode().strip())


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
    source: pathlib.Path,
    commit: str,
    paths: list[str],
    worktree: pathlib.Path,
    start: str | None = None,
) -> list[dict[str, str]]:
    """Put each path in `worktree` back to its state before the task's work.

    That is the first parent of `start` (a span's first commit), or of
    `commit` when there is no span. A path that did not exist there is
    deleted -- the work created it, so the agent has to as well. Returns what
    was done per path, in order, for the row.
    """
    parent = parent_of(source, start or commit)
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


def commit_size(
    source: pathlib.Path, commit: str, paths: list[str], start: str | None = None
) -> dict[str, int]:
    """Lines the work added and removed in the reverted paths: the task's size.

    For a span this is the net diff from `start^` to `commit`, not the sum of
    each commit's own diff: a line one commit wrote and a later one rewrote
    is work the agent does once.
    """
    parent = parent_of(source, start or commit)
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
    worktree: pathlib.Path,
    source: pathlib.Path,
    commit: str,
    paths: list[str],
    start: str | None = None,
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
    parent = parent_of(source, start or commit)
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


# --- hidden tests (#726) --------------------------------------------------------


def _split(node: str) -> tuple[str, list[str]]:
    path, *names = node.split("::")
    return path, names


def hidden_files(hidden: list[str]) -> list[str]:
    """The test files the hidden node ids live in, sorted, each once."""
    return sorted({_split(node)[0] for node in hidden})


def _find(tree: ast.Module, names: list[str]) -> tuple[ast.stmt, list[ast.stmt]] | None:
    """The def or class `names` points at, and the body that holds it."""
    body: list[ast.stmt] = tree.body
    holder = body
    found: ast.stmt | None = None
    for depth, name in enumerate(names):
        found = next(
            (
                n
                for n in body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and n.name == name
            ),
            None,
        )
        if found is None:
            return None
        if depth < len(names) - 1:
            if not isinstance(found, ast.ClassDef):
                return None
            holder = body = found.body
    assert found is not None
    return found, holder


def remove_test(text: str, names: list[str]) -> str | None:
    """`text` with the def or class `names` cut out, or None if it is absent.

    The cut takes the decorators and any comment block directly above the
    definition (a comment describing a hidden test is a hint about it), and
    the blank lines after it, so the gap the neighbors are left with is the
    one that already separated them. Raises ValueError when the cut would
    leave a class with no body: hide the class instead.
    """
    tree = ast.parse(text)
    found = _find(tree, names)
    if found is None:
        return None
    node, holder = found
    if len(names) > 1 and len(holder) == 1:
        raise ValueError(
            f"hiding {'::'.join(names)} would empty its class; hide the class"
        )
    lines = text.splitlines(keepends=True)
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
    while first > 0 and lines[first - 1].lstrip().startswith("#"):
        first -= 1
    last = node.end_lineno or node.lineno
    while last < len(lines) and not lines[last].strip():
        last += 1
    out = "".join(lines[:first] + lines[last:])
    ast.parse(out)  # a cut that breaks the file must fail here, not in a trial
    return out


def hide(worktree: pathlib.Path, hidden: list[str]) -> list[dict[str, str]]:
    """Take every hidden test out of the tree the agent will be handed.

    A bare file id deletes the file. A `file::name` id cuts that definition
    out of the file. An id whose file or definition is not in the tree is
    `absent` -- a regression test from a later commit (`hidden_ref`) that the
    task's commit never had -- and nothing is done for it.
    """
    done = []
    for node in hidden:
        rel, names = _split(node)
        path = worktree / rel
        if not path.exists():
            done.append({"test": node, "action": "absent"})
        elif not names:
            path.unlink()
            done.append({"test": node, "action": "deleted"})
        else:
            cut = remove_test(path.read_text(), names)
            if cut is None:
                done.append({"test": node, "action": "absent"})
            else:
                path.write_text(cut)
                done.append({"test": node, "action": "removed"})
    return done


def still_visible(worktree: pathlib.Path, hidden: list[str]) -> list[str]:
    """The hidden ids the agent's tree still defines. Empty is the only pass.

    Read back after `hide`, so a cut that silently missed (a name defined
    twice, a file the id spelled differently) stops the trial instead of
    handing the agent the held-out test.
    """
    left = []
    for node in hidden:
        rel, names = _split(node)
        path = worktree / rel
        if not path.exists():
            continue
        if not names or _find(ast.parse(path.read_text()), names) is not None:
            left.append(node)
    return left


@contextlib.contextmanager
def hidden_restored(
    worktree: pathlib.Path, source: pathlib.Path, ref: str, hidden: list[str]
) -> Iterator[None]:
    """Put the hidden tests' files back, at `ref`, for the duration.

    Afterwards every file is exactly as the agent left it (or absent, if it
    was), so the saved patch, the gates and the recall read the agent's work
    and never the held-out tests. Raises RuntimeError if `ref` lacks a file.
    """
    saved: dict[pathlib.Path, bytes | None] = {}
    try:
        for rel in hidden_files(hidden):
            content = blob(source, ref, rel)
            if content is None:
                raise RuntimeError(f"{rel} is not in {ref}")
            path = worktree / rel
            saved[path] = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        yield
    finally:
        for path, before in saved.items():
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)


def packages(tree: pathlib.Path) -> set[str]:
    """The project's own importable packages: `src/<pkg>` or top-level `<pkg>`."""
    found = set()
    for root in (tree / "src", tree):
        if root.is_dir():
            found |= {p.parent.name for p in root.glob("*/__init__.py")}
    return found - {"tests"}


def project_names(tree: pathlib.Path) -> set[str]:
    """Every name the project's own code defines, at the tree's state.

    Module stems, functions, classes and methods at any depth, and
    module-level constants. Taken on the reference tree, it is the API a
    held-out test could depend on.
    """
    names: set[str] = set()
    files: list[pathlib.Path] = []
    for pkg in packages(tree):
        root = tree / "src" / pkg if (tree / "src" / pkg).is_dir() else tree / pkg
        files += root.rglob("*.py")
    if (tree / "src").is_dir():
        files += (tree / "src").glob("*.py")  # single-module src layout
    for path in files:
        names.add(path.stem)
        try:
            module = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(module):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
        for node in module.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            names |= {t.id for t in targets if isinstance(t, ast.Name)}
    return names - {"__init__"}


def names_used(text: str, names: list[str]) -> set[str]:
    """Every identifier one test (or, with no `names`, one file) refers to."""
    module = ast.parse(text)
    found = _find(module, names) if names else None
    root: ast.AST = found[0] if found else module
    if names and not found:
        return set()
    used = set()
    for node in ast.walk(root):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.alias):
            used.add((node.asname or node.name).split(".")[-1])
    return used


def unseen_api(
    hidden: dict[str, str], visible: str, project: set[str]
) -> dict[str, list[str]]:
    """Per hidden test, the project names it uses that the agent cannot read.

    `hidden` maps each node id to the text of the file it runs from;
    `visible` is everything the agent is handed -- the prompt, the visible
    tests and the starting source; `project` is `project_names` of the
    reference tree. A held-out test that calls a function no visible text
    names fails for the name, not for the behavior: a guessing game, not a
    held-out check. The verifier refuses a task with any.
    """
    out = {}
    for node, text in hidden.items():
        _path, names = _split(node)
        missing = sorted(
            n
            for n in names_used(text, names) & project
            if not re.search(rf"\b{re.escape(n)}\b", visible)
        )
        if missing:
            out[node] = missing
    return out


COUNT = re.compile(
    r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)\b"
)


def counts(output: str) -> dict[str, int]:
    """pytest's closing summary as numbers: passed, failed, skipped, errors.

    Reads the last line that carries any count, so a test's own output that
    happens to say "3 passed" earlier cannot be mistaken for the summary.
    """
    got = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for line in reversed(output.splitlines()):
        found = COUNT.findall(line)
        if found:
            for n, word in found:
                key = "errors" if word.startswith("error") else word
                if key in got:
                    got[key] += int(n)
            break
    return got
