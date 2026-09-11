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


def test_acquire_lock_creates_its_own_directory(tmp_path) -> None:
    """A missing `~/.local-llm-bench/` must not abort a run.

    The bug this pins: the directory was only ever created as a side effect of
    the target-repo stash, so a machine that had never stashed had no
    directory, and `acquire_lock` died on the bare errno --

        cannot take the run lock: [Errno 2] No such file or directory

    -- after preflight had passed and the model was already resident. On
    2026-09-09 that cost a loaded server and a smoke-probe pass on the Ryzen
    box. The lock owns its directory; nothing else may be relied on to make it.
    """
    lock = tmp_path / "never-existed" / "run-lock.json"
    assert not lock.parent.exists()

    ok, why = preflight.acquire_lock("a run", path=lock)

    assert ok, why
    assert lock.exists()


def test_no_new_reader_tests_a_checkout_root_run_lock() -> None:
    """#265: ab_status.sh tested `[ -f .run-lock.json ]` in the checkout, so it
    read `lock=free` forever after the lock moved to ~/.local-llm-bench/. That
    reader is ported; this keeps a new one from reappearing.

    The risk grows as #235 proceeds: every driver or monitor that moves to
    Python is a chance to hardcode the old path by habit. The lock is the
    language-agnostic signal (`preflight.read_lock` / `machine_state`), and the
    only code that may still NAME a checkout-root `.run-lock.json` is preflight's
    legacy-lock warning, which looks for the stale artifact on purpose; the two
    docstrings below only record the history.
    """
    import subprocess

    repo = pathlib.Path(__file__).resolve().parents[1]
    found = subprocess.run(
        ["git", "grep", "-l", r"\.run-lock\.json", "--", "scripts", "benchmarks"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    allowed = {
        "benchmarks/agent/preflight.py",  # the legacy-lock detector + its warning
        "scripts/refuse_commit_during_benchmark.py",  # refuses on a legacy lock (#242)
        "scripts/ab_status.py",  # docstring recording the move
        "scripts/machine_claim.py",  # docstring: same lock file, extended
    }
    # Test files legitimately name the path -- they exercise the legacy-lock
    # detector. The repo's convention is a `test_` basename prefix.
    offenders = {
        f for f in found if not pathlib.PurePosixPath(f).name.startswith("test_")
    }
    offenders -= allowed
    assert not offenders, (
        "new reference to a checkout-root .run-lock.json -- the lock lives at "
        f"~/.local-llm-bench/, read it via preflight/machine_state (#265): {sorted(offenders)}"
    )
