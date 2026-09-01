"""One real trial, end to end, with a scripted agent instead of a model.

The unit tests cover each measurement alone. What they cannot cover is the part
that has actually gone wrong before: a measurement wired into `one_trial` in the
wrong place. `gates_before` must be taken on the excised tree and not after the
agent; the solution must be saved *before* the `finally` deletes the worktree.
Both are ordering bugs, both produce rows that look fine and mean nothing, and
neither is visible without running the whole thing.

The stand-in agent restores the pristine file from the reference repo. That
makes it a guaranteed pass and a guaranteed verbatim restore, so the assertions
here are exact rather than approximate.

Needs ~/git/gmail-archive at the pinned commit and a working `uv`; skips
cleanly without them. Marked `integration`: it runs the oracle twice and both
gates, which is seconds, not milliseconds.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tomllib

import pytest
import results
import run

HERE = pathlib.Path(__file__).parent
CFG = tomllib.loads((HERE / "tasks.toml").read_text())
REPO = pathlib.Path(CFG["repo"]).expanduser()

pytestmark = pytest.mark.integration


def _available() -> bool:
    if not REPO.is_dir() or shutil.which("uv") is None:
        return False
    got = subprocess.run(
        ["git", "cat-file", "-e", f"{CFG['base_commit']}^{{commit}}"],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    return got.returncode == 0


needs_repo = pytest.mark.skipif(
    not _available(), reason="gmail-archive at the pinned commit, and uv"
)


@pytest.fixture
def scripted_agent(monkeypatch):
    """A client that solves the task by copying the original file back."""

    def argv(task, backend, worktree=None):
        files = [t["file"] for t in run.targets(task)]
        script = (
            "import pathlib,shutil;"
            f"src=pathlib.Path({str(REPO)!r});"
            f"[shutil.copy(src/f, pathlib.Path(f)) for f in {files!r}]"
        )
        return ["python3", "-c", script]

    monkeypatch.setitem(run.CLIENTS, "scripted", (argv, lambda _out: {}))
    return "scripted"


def _task(name: str) -> dict:
    return next(t for t in CFG["task"] if t["name"] == name)


def _run(task_name, tmp_path, client, **kw):
    return run.one_trial(
        CFG,
        _task(task_name),
        "stub",
        {"model": "stub", "context_tokens": 1},
        trial=1,
        workdir=tmp_path,
        timeout=600,
        dry_run=False,
        client=client,
        solutions=tmp_path / "solutions",
        # The stub agent copies from the un-excised reference repo, which the
        # #54 sandbox denies. That denial is correct; it just means these
        # plumbing tests have to opt out of it.
        sandbox=False,
        **kw,
    )


@needs_repo
def test_a_solved_trial_records_a_verdict_and_every_measurement(
    scripted_agent, tmp_path
):
    row = _run("mbox-strip-envelope", tmp_path, scripted_agent)

    assert row["control_fails_as_expected"] is True
    assert results.verdict(row) is True
    assert row["touched_tests"] is False
    assert row["source_repo_intact"] is True

    # Restoring the original file byte for byte is exactly what recall looks
    # like. If this reports False the comparison is measuring the wrong span.
    assert row["restored_verbatim"] is True

    # The baseline was taken on the excised tree, so putting the body back can
    # only improve on it -- never make it worse.
    assert row["gates_before"], "no baseline recorded"
    assert row["gates_after"], "no post-agent measurement recorded"
    assert all(v <= 0 for v in row["gates_delta"].values()), row["gates_delta"]


@needs_repo
def test_the_solution_survives_the_worktree(scripted_agent, tmp_path):
    """The whole point. The worktree is gone; the patch is not."""
    row = _run("mbox-strip-envelope", tmp_path, scripted_agent)
    patch = pathlib.Path(row["solution_patch"])
    assert patch.exists()
    assert "strip_envelope" in patch.read_text()
    assert row["solution_sha256"]
    assert not (tmp_path / "mbox-strip-envelope-stub-scripted-1").exists()


@needs_repo
def test_a_multi_file_task_hollows_out_every_target(scripted_agent, tmp_path):
    row = _run("mbox-quoting-both-halves", tmp_path, scripted_agent)
    assert row["removed_symbols"] == ["strip_envelope", "unquote_mbox"]
    assert results.verdict(row) is True
    assert row["restored_verbatim"] is True
    # Two files in one patch, or the multi-target excision only reached one.
    patch = pathlib.Path(row["solution_patch"]).read_text()
    assert "src/gmail_archive/mbox.py" in patch
    assert "src/gmail_archive/parser.py" in patch


@needs_repo
def test_a_no_docstring_task_really_withholds_the_contract(scripted_agent, tmp_path):
    """The variant must differ from its twin in the one way it claims to."""
    row = _run("parser-mbox-quoting-nodoc", tmp_path, scripted_agent)
    assert row["keep_docstring"] is False
    assert results.verdict(row) is True
    # The docstring states the mboxrd reasoning outright. It has to be gone
    # from what the agent was shown, which means it is part of the diff.
    patch = pathlib.Path(row["solution_patch"]).read_text()
    assert "mboxrd" in patch, "the docstring was left in place; the task is easy"


@needs_repo
def test_an_agent_that_does_nothing_fails_and_says_so(monkeypatch, tmp_path):
    """The negative case: no edit, no pass, and no crash on the way out."""
    monkeypatch.setitem(
        run.CLIENTS, "idle", (lambda t, b, w=None: ["true"], lambda _o: {})
    )
    row = _run("mbox-strip-envelope", tmp_path, "idle")
    assert results.verdict(row) is False
    assert row["solution_empty"] is True
    assert "solution_patch" not in row


@needs_repo
def test_the_agent_is_handed_a_working_environment(monkeypatch, tmp_path):
    """METHODOLOGY section 9 claimed the opposite. It was wrong.

    The "empty-virtualenv confound" says a fresh checkout has no `.venv`, so
    part of every wall-time number is the agent working out how to run pytest,
    and prescribes running `uv sync` before handover -- a change that would
    start a new series nobody may pool across.

    None of it is true, and it never was. The control check runs `uv run pytest`
    in the worktree before the agent is invoked, which materialises `.venv` with
    the project and pytest installed. That has been the control's exact form
    since the harness's first commit, and all 482 recorded rows carry a control
    result, so no trial has ever met an empty environment.

    This test is the guard. The agent here does nothing but the thing the
    METHODOLOGY said would fail -- run the tests through `.venv/bin/python`, the
    same command the first ds4 trial was cited as evidence for. If a future
    change moves the control after the agent, or drops `uv run`, this goes red
    and the confound becomes real without anyone noticing.
    """

    def argv(task, backend, worktree=None):
        files = [t["file"] for t in run.targets(task)]
        script = (
            "import pathlib,shutil,subprocess,sys;"
            "py=pathlib.Path('.venv/bin/python');"
            "sys.exit('no virtualenv was prepared') if not py.exists() else None;"
            f"src=pathlib.Path({str(REPO)!r});"
            f"[shutil.copy(src/f, pathlib.Path(f)) for f in {files!r}];"
            "sys.exit(subprocess.run([str(py),'-m','pytest','-q','tests/']).returncode)"
        )
        return ["python3", "-c", script]

    monkeypatch.setitem(run.CLIENTS, "venv-user", (argv, lambda _o: {}))
    row = _run("mbox-strip-envelope", tmp_path, "venv-user")
    assert results.verdict(row) is True
