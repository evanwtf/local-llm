"""Tests for #54's workspace-escape detection.

An agent that works in the wrong tree produces a row that looks exactly like a
model failure: no patch, no error, and the control's own test counts. This
check is what keeps those two apart, so it is tested before it is trusted.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import run

HOME = str(pathlib.Path.home())


def test_paths_inside_the_worktree_are_not_escapes() -> None:
    log = f"{HOME}/bench-work/trial-1/src/a.py and {HOME}/bench-work/trial-1/tests/b.py"
    assert run.paths_outside(log, f"{HOME}/bench-work/trial-1") == []


def test_a_real_repository_is_an_escape() -> None:
    log = f'{{"workdir": "{HOME}/git/gmail-archive", "cmd": "pytest"}}'
    assert run.paths_outside(log, f"{HOME}/bench-work/trial-1") == [
        f"{HOME}/git/gmail-archive"
    ]


def test_escapes_are_ranked_by_how_often_they_appear() -> None:
    """The most-touched tree is the one the agent actually worked in."""
    log = (f"{HOME}/git/alpha " * 3) + (f"{HOME}/git/beta " * 7)
    assert run.paths_outside(log, "/nowhere") == [
        f"{HOME}/git/beta",
        f"{HOME}/git/alpha",
    ]


def test_venv_and_cache_noise_is_not_an_escape() -> None:
    """Every `uv run` prints these; flagging them would make the check useless."""
    log = f"{HOME}/.cache/uv/wheels {HOME}/git/proj/.venv/bin/python {HOME}/Library/Caches/x"
    assert run.paths_outside(log, "/nowhere") == []


def test_empty_and_missing_output_are_safe() -> None:
    assert run.paths_outside("", "/nowhere") == []
    assert run.paths_outside(None, "/nowhere") == []


def test_each_escape_is_reported_once() -> None:
    log = f"{HOME}/git/gmail-archive/a {HOME}/git/gmail-archive/b {HOME}/git/gmail-archive/c"
    assert run.paths_outside(log, "/nowhere") == [f"{HOME}/git/gmail-archive"]
