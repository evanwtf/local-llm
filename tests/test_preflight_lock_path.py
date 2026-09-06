"""Tests for the preflight run-lock path (#160).

The lock is a claim on a MACHINE, so it must live at a machine-absolute path,
never derived from `__file__`. The bug this pins: a benchmark launched from a
worktree resolved `__file__` into that worktree and took
`.claude/worktrees/<name>/.run-lock.json` -- a different file from the main
checkout's, so two agents each held a lock the other could not see and both
reported the machine free. The wrong answer here is a lock path that moves
when the module is imported from a different checkout, or a lock that sits
inside a repo.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import preflight


def test_lock_path_is_machine_absolute() -> None:
    """The lock lives under the home directory, not under any checkout."""
    assert (
        preflight.LOCK_PATH
        == pathlib.Path.home() / ".local-llm-bench" / "run-lock.json"
    )


def test_lock_path_unchanged_from_worktree(monkeypatch) -> None:
    """Importing from a worktree-shaped path must not move the lock.

    This is the regression the fix pins: before, `__file__` resolved into the
    worktree and the lock landed inside it. The path must be identical no
    matter where the module is imported from.
    """
    worktree = pathlib.Path("/x/local-llm/.claude/worktrees/peer+160")
    monkeypatch.setattr(
        preflight, "__file__", str(worktree / "benchmarks" / "agent" / "preflight.py")
    )
    assert (
        preflight.LOCK_PATH
        == pathlib.Path.home() / ".local-llm-bench" / "run-lock.json"
    )


def test_lock_path_outside_repo() -> None:
    """The lock must never sit inside a checkout."""
    repo = pathlib.Path(__file__).resolve().parents[1]
    try:
        preflight.LOCK_PATH.relative_to(repo)
    except ValueError:
        return
    raise AssertionError(f"lock path {preflight.LOCK_PATH} is inside the repo {repo}")


def test_legacy_lock_paths_cover_main_and_worktrees(tmp_path, monkeypatch) -> None:
    """The legacy scan covers the main checkout and every worktree."""
    repo = tmp_path / "local-llm"
    wt = repo / ".claude" / "worktrees" / "peer+160"
    wt.mkdir(parents=True)
    (wt / "run-lock.json").write_text("{}")
    monkeypatch.setattr(
        preflight, "__file__", str(repo / "benchmarks" / "agent" / "preflight.py")
    )
    paths = preflight._legacy_lock_paths()
    assert repo / ".run-lock.json" in paths
    assert repo / ".claude" / "worktrees" / "peer+160" / "run-lock.json" in paths


def test_warn_legacy_lock_warns_once(tmp_path, monkeypatch, caplog) -> None:
    """A legacy lock in a checkout is reported, once."""
    legacy = tmp_path / "run-lock.json"
    legacy.write_text("{}")
    monkeypatch.setattr(preflight, "_legacy_lock_paths", lambda: [legacy])
    monkeypatch.setattr(preflight, "_legacy_warned", False)
    with caplog.at_level("WARNING", logger="preflight"):
        preflight.warn_legacy_lock()
        preflight.warn_legacy_lock()
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "legacy run lock" in warnings[0].getMessage()


def test_warn_legacy_lock_silent_when_clean(monkeypatch, caplog) -> None:
    """No legacy lock, no warning."""
    monkeypatch.setattr(preflight, "_legacy_lock_paths", list)
    monkeypatch.setattr(preflight, "_legacy_warned", False)
    with caplog.at_level("WARNING", logger="preflight"):
        preflight.warn_legacy_lock()
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
