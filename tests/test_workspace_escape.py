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


def test_a_tree_holding_answers_is_recognised() -> None:
    """~/bench-solutions holds one correct patch per trial; ~/git/local-llm's
    tracked results.jsonl records their absolute paths. Either one can hand the
    agent the answer, so a trial that worked in them is confounded (#54)."""
    assert run.ANSWER_TREES.intersection(f"{HOME}/bench-solutions".split("/"))
    assert run.ANSWER_TREES.intersection(f"{HOME}/git/local-llm".split("/"))


def test_an_ordinary_target_repo_is_not_treated_as_tainted() -> None:
    """Escaping into a target repo is still wrong, but it is a different fault
    from reading the answers, and must not be silently reclassified."""
    assert not run.ANSWER_TREES.intersection(f"{HOME}/git/monitor".split("/"))
    assert not run.ANSWER_TREES.intersection(
        f"{HOME}/git/local-llm-testing/gmail-archive".split("/")
    )


def test_shell_state_does_not_reach_a_trial() -> None:
    """A benchmark whose result depends on which shell started it is not
    reproducible. VIRTUAL_ENV is the one that actually leaked: an agent was
    observed reading uv's mismatched-venv warning in its own tool output."""
    import os

    for key in run.LEAKY_ENV:
        os.environ[key] = "/should/not/reach/the/agent"
    try:
        env = run.agent_env({"model": "m", "context_tokens": 1})
        for key in run.LEAKY_ENV:
            assert key not in env, f"{key} leaked into the agent environment"
    finally:
        for key in run.LEAKY_ENV:
            os.environ.pop(key, None)


def test_a_client_naming_its_own_installation_is_not_an_escape() -> None:
    """Aider prints its own interpreter when it runs the suite.

    "## Running: ~/.local/share/uv/tools/aider-chat/bin/python -m pytest" was
    flagged on 2 of 2 qwen36coding trials, against 0 of 30 on backends where
    aider happened not to print that line. Recording it would have published
    "Aider escapes on Ollama backends" from a tool invoking its own binary.
    """
    home = pathlib.Path.home()
    stdout = f"## Running: {home}/.local/share/uv/tools/aider-chat/bin/python -m pytest"
    assert run.paths_outside(stdout, "/tmp/worktree") == []


def test_a_real_escape_is_still_caught_beside_a_self_reference() -> None:
    """The filter must not swallow the thing it sits next to."""
    home = pathlib.Path.home()
    stdout = (
        f"## Running: {home}/.local/share/uv/tools/aider-chat/bin/python -m pytest\n"
        f"reading {home}/git/gmail-archive/src/gmail_archive/parser.py\n"
    )
    assert run.paths_outside(stdout, "/tmp/worktree") == [f"{home}/git/gmail-archive"]


def test_the_task_definitions_are_denied_to_the_agent() -> None:
    """tasks.toml holds the prompts AND the script tasks' expected outputs.

    "Benchmarking" -> "gnikramhcneB" is in that file. An agent that reads it
    has the test. On 2026-08-31 this cost the entire OpenCode script-task cell:
    9 of 12 rows auto-excluded for answer exposure, leaving nothing citable
    about the project's own designated primary harness.
    """
    import tempfile

    profile, denied = run.sandbox_profile(
        tempfile.mkdtemp(), str(pathlib.Path.home() / "git/gmail-archive")
    )
    assert any(d.endswith("tasks.toml") for d in denied)
    assert 'literal "' in profile, "a file must be denied as a literal, not a subpath"


def test_the_repo_itself_is_still_readable() -> None:
    """Denying ~/git/local-llm wholesale kills OpenCode: it lstat()s the
    launcher's cwd and dies with EPERM in 0.4s. The fix must be the two files,
    never the tree."""
    _, denied = run.sandbox_profile(
        "/tmp/worktree", str(pathlib.Path.home() / "git/gmail-archive")
    )
    repo = str(pathlib.Path.home() / "git/local-llm")
    assert repo not in denied
