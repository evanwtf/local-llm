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
sys.path.insert(0, str(HERE))

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
    The prompt naming the file is the cheapest defence against a guess."""
    if task.get("kind") == "script":
        pytest.skip("script task: the prompt names no repository file")
    prompt = task.get("prompt") or ""
    if not prompt:
        pytest.skip("no prompt: inherited")
    for target in runner.targets(task):
        assert target["file"] in prompt, (
            f"{task['name']} does not name {target['file']}"
        )
