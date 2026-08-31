"""Tests for script tasks -- the agent builds a runnable artifact from nothing.

The excision tasks hand the agent a repository, a failing suite and a function
signature, so the only thing measured is the body. A script task starts from an
empty directory: the agent must choose the filename, read argv, and print to
stdout. Trivial logic, real boilerplate -- and boilerplate is exactly what is
never exercised when the scaffolding already exists.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import run

CHECKS = [["Benchmarking", "gnikramhcneB"], ["a", "a"], ["ab cd", "dc ba"]]


@pytest.fixture
def workdir():
    return pathlib.Path(tempfile.mkdtemp())


def write(workdir, body: str) -> None:
    (workdir / "reverse.py").write_text(body)


def test_empty_directory_fails_its_own_oracle(workdir) -> None:
    """The control. If an empty directory passed, the task would prove nothing."""
    ok, summary = run.script_checks(workdir, "reverse.py", CHECKS, 30)
    assert not ok
    assert "never created" in summary


def test_a_working_script_passes(workdir) -> None:
    write(workdir, "import sys\nprint(sys.argv[1][::-1])\n")
    ok, summary = run.script_checks(workdir, "reverse.py", CHECKS, 30)
    assert ok, summary


def test_hardcoding_the_demonstrated_case_fails(workdir) -> None:
    """The prompt shows `reverse.py hello` -> `olleh`; no check uses 'hello'.

    A script that prints the demonstrated answer must fail, or the example in
    the prompt is a giveaway rather than a clarification.
    """
    write(workdir, 'print("olleh")\n')
    ok, _ = run.script_checks(workdir, "reverse.py", CHECKS, 30)
    assert not ok


def test_missing_trailing_newline_passes_but_is_noted(workdir) -> None:
    """Formatting compliance is recorded, not conflated with capability."""
    write(workdir, "import sys\nsys.stdout.write(sys.argv[1][::-1])\n")
    ok, summary = run.script_checks(workdir, "reverse.py", CHECKS, 30)
    assert ok
    assert "trailing newline" in summary


def test_a_crashing_script_fails_without_raising(workdir) -> None:
    """A traceback is a failed trial, never a failed batch."""
    write(workdir, "raise SystemExit('boom')\n")
    ok, summary = run.script_checks(workdir, "reverse.py", CHECKS, 30)
    assert not ok
    assert summary


def test_a_script_ignoring_argv_fails(workdir) -> None:
    """Reading argv is the boilerplate this task exists to measure."""
    write(workdir, 'print("gnikramhcneB")\n')
    ok, _ = run.script_checks(workdir, "reverse.py", CHECKS, 30)
    assert not ok  # passes check 1, fails 'a' and 'ab cd'


def test_a_hanging_script_times_out_rather_than_blocking(workdir) -> None:
    write(workdir, "import time\ntime.sleep(30)\n")
    ok, summary = run.script_checks(workdir, "reverse.py", CHECKS, 2)
    assert not ok
    assert "timed out" in summary


def test_the_task_is_declared_and_its_checks_avoid_the_prompt_example() -> None:
    """Guards the property across future edits to either the prompt or checks."""
    import tomllib

    cfg = tomllib.loads((pathlib.Path(__file__).parent / "tasks.toml").read_text())
    task = next(t for t in cfg["task"] if t["name"] == "script-reverse")
    assert task["kind"] == "script"
    assert "hello" in task["prompt"] and "olleh" in task["prompt"]
    for arg, want in task["checks"]:
        assert arg != "hello" and want != "olleh", "the checked case is given away"
