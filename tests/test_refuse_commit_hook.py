"""#227 defect 2: the pre-commit guard against a live A/B (#227).

pre-commit runs the hook as a subprocess on every commit, so the behaviour
tests run the real script against the real pgrep and a genuinely live fake A/B,
not a mocked match -- a mocked return would only prove the function body reads
that return back. The branches that need no real process main() and the
override are unit-tested the same way, and the config's `language: python` and
"first in the list" are pinned as artifacts so a refactor cannot move them.

These tests assume no real benchmark holds the machine: the root conftest
refuses the suite while the preflight run lock is set, so a live stack_agent
run blocks pytest before these cases run, and the "clear" case guards itself
with a real pgrep check that skips if a genuine A/B happens to be live.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
HOOK = ROOT / "scripts" / "refuse_commit_during_benchmark.py"
CONFIG = ROOT / ".pre-commit-config.yaml"

sys.path.insert(0, str(SCRIPTS))
refuse = importlib.import_module("refuse_commit_during_benchmark")


def _spawn_ab(timeout: int = 30) -> subprocess.Popen[bytes]:
    """A live process whose command line names stack_agent_ab.sh.

    `exec -a` replaces bash's argv[0] with the given name, so pgrep -f sees
    `stack_agent_ab.sh` in the command line exactly like a real A/B launch.
    """
    return subprocess.Popen(
        ["bash", "-c", f"exec -a stack_agent_ab.sh sleep {timeout}"]
    )


def _any_real_ab() -> bool:
    """Whether a stack_agent A/B is live right now, independent of the hook.

    The bracket keeps this probe (whose command line carries the pattern) from
    matching itself; pgrep also excludes its own pid.
    """
    done = subprocess.run(
        ["pgrep", "-f", "[s]tack_agent_ab.sh"], capture_output=True, check=False
    )
    return done.returncode == 0


# --- main(): refuse on a live run, allow otherwise, override wins -----------


def test_refuses_when_a_live_run_is_detected(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", raising=False)
    monkeypatch.setattr(refuse, "_live_run", lambda: True)
    assert refuse.main() == 1


def test_allows_when_no_run_is_detected(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", raising=False)
    monkeypatch.setattr(refuse, "_live_run", lambda: False)
    assert refuse.main() == 0


def test_override_allows_commit_even_during_a_live_run(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_ALLOW_COMMIT_DURING_RUN", "1")
    monkeypatch.setattr(refuse, "_live_run", lambda: True)
    assert refuse.main() == 0


# --- _live_run(): the real pgrep, against a real process --------------------


def test_live_run_sees_a_real_ab_process() -> None:
    proc = _spawn_ab()
    try:
        assert refuse._live_run() is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_no_live_run_counts_as_clear() -> None:
    if _any_real_ab():
        pytest.skip("a stack_agent A/B is genuinely live; cannot test the clear case")
    assert refuse._live_run() is False


def test_hook_process_refuses_against_a_live_run() -> None:
    """The full entry pre-commit invokes, exercising real pgrep end to end."""
    proc = _spawn_ab()
    try:
        done = subprocess.run(
            [sys.executable, str(HOOK)], capture_output=True, text=True, check=False
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    assert done.returncode == 1, done.stderr
    assert "A/B is running" in done.stdout, done.stdout  # house rule: stdout


# --- the pattern and the config, pinned as artifacts ------------------------
#
# The bracket is the fix: `[s]tack` matches the text "stack", never the literal
# "[s]tack" this very hook's pgrep argument carries, so a shell or parent that
# quotes the pattern is not the thing the regex sees.


def test_the_pgrep_pattern_is_bracketed_so_it_does_not_match_itself() -> None:
    assert refuse._PATTERN == "[s]tack_agent_ab.sh"


def test_the_config_declares_a_language_python_hook_first() -> None:
    """`language: python` (not `system`) and first in the list are the point:
    a bare `git commit` must invoke the same hermetic env a `pre-commit run`
    does, and nothing reformats a file before this guard runs."""
    data = yaml.safe_load(CONFIG.read_text())
    hooks = data["repos"][0]["hooks"]
    assert hooks[0]["id"] == "refuse-commit-during-benchmark"
    assert hooks[0]["language"] == "python"
    assert hooks[0]["pass_filenames"] is False
    assert hooks[0]["always_run"] is True
    assert hooks[0]["stages"] == ["pre-commit"]
    assert hooks[0]["entry"].startswith("python ")
    assert hooks[0]["entry"].endswith("scripts/refuse_commit_during_benchmark.py")


def test_the_pre_commit_hook_is_installed_into_the_repo() -> None:
    """#227: a config file alone is the 'skipping test' the repo rejects -- the
    guard reads as covered while a clone that never ran `pre-commit install`
    commits straight past it. Fail closed on a missing or non-executable hook:
    a bare `git commit` must actually run this guard, or the repo is not
    protected. `uv run pre-commit install` wires it in (README Build and run;
    CI runs it before pytest), so the hook must be present and executable."""
    git_hook = ROOT / ".git" / "hooks" / "pre-commit"
    assert git_hook.exists(), (
        "pre-commit stage hook missing; run `uv run pre-commit install`"
    )
    assert os.access(git_hook, os.X_OK), git_hook
    assert "pre-commit" in git_hook.read_text()
