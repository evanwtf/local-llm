"""Every task must actually remove something, checked without running a trial.

#4's constraint: whatever is added has to keep "a control run that proves the
tests fail". That is verified per trial as `control_fails_as_expected`, which
means a typo in `tasks.toml` costs a twenty-minute trial before it surfaces --
and the suite is growing, with multi-target and docstring-free tasks now in it.

These checks are static. They read the task definitions and the target
repository at its pinned commit, and confirm each named symbol exists and has
a body that can be removed. They skip cleanly when the target repo is not
checked out, so the default suite still needs nothing installed.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib

import pytest

HERE = pathlib.Path(__file__).resolve().parent

# These cases read the real repositories on this machine, so they need the real
# stash paths rather than conftest's per-test redirect.
pytestmark = pytest.mark.real_stash_paths
sys.path.insert(0, str(HERE))

import replay
import run as runner

CFG = tomllib.loads((HERE / "tasks.toml").read_text())
TASKS = CFG["task"]


def _repo(task) -> pathlib.Path:
    """The real checkout -- which is not the configured path during a run.

    While a batch runs, the configured path holds the *export*: the same tree
    with the target symbol excised and no history it was ever there. Reading it
    here made all eight gmail-archive cases fail for the duration of any live
    run, reported as "not at commit". The guarded copy is the one that answers
    the question this test is asking.
    """
    repo = pathlib.Path(runner.task_target(CFG, task)["repo"]).expanduser()
    return runner.guarded_repo(repo)


def _blob(task, rel: str) -> str | None:
    """The file's content at the task's pinned commit, or None."""
    target = runner.task_target(CFG, task)
    got = subprocess.run(
        ["git", "show", f"{target['base_commit']}:{rel}"],
        cwd=_repo(task),
        capture_output=True,
        text=True,
        check=False,
    )
    return got.stdout if got.returncode == 0 else None


def _available(task) -> bool:
    return _repo(task).exists() and (_repo(task) / ".git").exists()


@pytest.mark.parametrize("task", TASKS, ids=[t["name"] for t in TASKS])
def test_every_task_names_at_least_one_target(task):
    """A task that removes nothing leaves the control passing, and every
    trial records control_fails_as_expected: false instead of failing here.

    Script tasks are exempt: they generate a program from an entrypoint rather
    than hollowing out a repository, and run.py branches on `kind == "script"`
    before it ever calls `targets`.
    """
    if task.get("kind") == "script":
        assert task.get("entrypoint"), task["name"]
        return
    if replay.is_replay(task):
        # A replay task (#714) removes the files it reverts, not a symbol.
        assert replay.validate(task) == [], task["name"]
        return
    targets = runner.targets(task)
    assert targets, task["name"]
    for t in targets:
        assert t.get("file") and t.get("symbol"), task["name"]


@pytest.mark.parametrize("task", TASKS, ids=[t["name"] for t in TASKS])
def test_every_task_target_is_excisable(task):
    """The symbol exists at the pinned commit and has a removable body.

    Skips when the target repo is absent, so the default run stays offline.
    """
    if task.get("kind") == "script":
        pytest.skip("script task: nothing is excised from a repo")
    if replay.is_replay(task):
        pytest.skip("replay task: test_a_replay_task_reverts_what_its_commit_touched")
    if not _available(task):
        pytest.skip(f"{_repo(task)} not checked out")
    keep_doc = task.get("keep_docstring", True)
    for target in runner.targets(task):
        source = _blob(task, target["file"])
        assert source is not None, f"{task['name']}: {target['file']} not at commit"
        # Dispatch by extension exactly as run.py does. Handing a .swift file
        # to the Python `ast` is the guess that EXCISERS exists to avoid.
        suffix = pathlib.Path(target["file"]).suffix
        exciser = runner.EXCISERS.get(suffix)
        assert exciser is not None, f"{task['name']}: no exciser for {suffix}"
        span = exciser._span(source, target["symbol"], keep_doc)
        assert span[1] > span[0], f"{task['name']}: {target['symbol']} removes nothing"


@pytest.mark.parametrize("task", TASKS, ids=[t["name"] for t in TASKS])
def test_a_task_prompt_names_the_file_it_edits(task):
    """#54: an agent that guesses a path works in the operator's real tree.
    The prompt naming the file is the cheapest defense against a guess."""
    if task.get("kind") == "script":
        pytest.skip("script task: the prompt names no repository file")
    prompt = task.get("prompt") or ""
    if not prompt:
        pytest.skip("no prompt: inherited")
    if replay.is_replay(task):
        # A replay task's work spans the files its commit touched; what the
        # prompt must name is the failing tests, and the no-edit rule.
        for test in task["tests"]:
            assert test.split("::")[0] in prompt, f"{task['name']} omits {test}"
        assert "Do not modify any test." in prompt, task["name"]
        return
    for target in runner.targets(task):
        assert target["file"] in prompt, (
            f"{task['name']} does not name {target['file']}"
        )


#: Files a replay task keeps at its commit rather than reverting: the test
#: environment. Without C's dependencies the tests cannot import, and adding a
#: dependency is a packaging exercise, not the feature (tasks.toml says so).
REPLAY_KEPT = {"pyproject.toml", "uv.lock"}

REPLAYS = [t for t in TASKS if replay.is_replay(t)]


@pytest.mark.parametrize("task", REPLAYS, ids=[t["name"] for t in REPLAYS])
def test_a_replay_task_reverts_what_its_commit_touched(task):
    """`revert` is exactly the commit's non-test files, less the environment.

    Hand-written on purpose, so a reader sees it; checked here, so a file
    left off -- which would hand the agent part of the answer -- fails a test
    instead of a trial. Skips when the commit is not in the local checkout.
    """
    if not _available(task):
        pytest.skip(f"{_repo(task)} not checked out")
    commit = runner.task_target(CFG, task)["base_commit"]
    got = subprocess.run(
        ["git", "diff", "--name-only", f"{commit}^1", commit],
        cwd=_repo(task),
        capture_output=True,
        text=True,
        check=False,
    )
    if got.returncode != 0:
        pytest.skip(f"{commit} is not in {_repo(task)}")
    touched = set(got.stdout.split())
    source = {p for p in touched if not p.startswith("tests/")} - REPLAY_KEPT
    assert set(task["revert"]) == source, task["name"]
    for test in task["tests"]:
        assert test.split("::")[0] in touched, f"{task['name']}: {test} not in commit"
