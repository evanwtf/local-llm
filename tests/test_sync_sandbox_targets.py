"""#145: the harness clones its own copies; it never writes to ~/git."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import sync_sandbox_targets as sync

TASKS = """
# File-level defaults. A task inherits these unless it names its own -- 558
# recorded rows depend on that, so the fixture has to exercise it.
repo = "~/git/gmail-archive"
base_commit = "bbbbbbb"

[[task]]
name = "inherits-the-defaults"

[[task]]
name = "one"
repo = "~/git/monitor"
base_commit = "aaaaaaa"

[[task]]
name = "two"
repo = "~/git/monitor"
base_commit = "aaaaaaa"

[[task]]
name = "script-only"
kind = "script"
entrypoint = "x"
"""


def write_tasks(tmp_path, text=TASKS):
    p = tmp_path / "tasks.toml"
    p.write_text(text)
    return p


def test_one_entry_per_repo_not_per_task(tmp_path):
    got = sync.targets(write_tasks(tmp_path))
    assert got == {"~/git/monitor": "aaaaaaa", "~/git/gmail-archive": "bbbbbbb"}


def test_a_task_inherits_the_file_level_repo(tmp_path):
    """The rule run.task_target owns. Re-deriving it is how run.py and
    provenance ended up disagreeing about what "dirty" meant."""
    assert sync.targets(write_tasks(tmp_path))["~/git/gmail-archive"] == "bbbbbbb"


def test_a_script_task_is_skipped(tmp_path):
    """Script tasks start from an empty directory: nothing to clone."""
    got = sync.targets(write_tasks(tmp_path))
    assert set(got) == {"~/git/monitor", "~/git/gmail-archive"}


def test_two_commits_for_one_repo_is_refused(tmp_path):
    """One checkout cannot be at two commits, and taking the last silently
    would make half the tasks unbuildable with nothing reporting it."""
    bad = TASKS.replace(
        '''[[task]]
name = "two"
repo = "~/git/monitor"
base_commit = "aaaaaaa"''',
        '''[[task]]
name = "two"
repo = "~/git/monitor"
base_commit = "ccccccc"''',
    )
    assert "ccccccc" in bad, "fixture surgery missed; the test would pass blind"
    with pytest.raises(SystemExit, match="cannot be at\\s+two commits"):
        sync.targets(write_tasks(tmp_path, bad))


def test_a_missing_operator_checkout_is_refused_not_guessed(tmp_path):
    """A guessed URL clones some other project, and every task then fails on a
    missing file -- which reads as a model result."""
    with pytest.raises(SystemExit, match="no remote to clone from"):
        sync.origin_of(tmp_path / "nope")


def _upstream(tmp_path):
    up = tmp_path / "upstream"
    up.mkdir()
    sh = lambda *a: subprocess.run(a, cwd=up, check=True, capture_output=True)
    sh("git", "init", "-q")
    sh("git", "config", "user.email", "t@t")
    sh("git", "config", "user.name", "t")
    (up / "a.py").write_text("x = 1\n")
    sh("git", "add", "-A")
    sh("git", "commit", "-qm", "first")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=up,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return up, head


def test_it_checks_out_the_pinned_commit_detached(tmp_path, monkeypatch):
    up, head = _upstream(tmp_path)
    monkeypatch.setattr(sync, "SANDBOX", tmp_path / "sandbox")
    dest = sync.sync_one("thing", str(up), head, dry_run=False)
    assert (dest / "a.py").read_text() == "x = 1\n"
    assert sync.git(["rev-parse", "HEAD"], dest) == head
    branch = sync.git(["rev-parse", "--abbrev-ref", "HEAD"], dest)
    assert branch == "HEAD", "a branch would drift when upstream moves"


def test_a_second_sync_discards_local_changes(tmp_path, monkeypatch):
    """The sandbox copy is disposable; an agent that reached it leaves nothing
    behind for the next run to inherit."""
    up, head = _upstream(tmp_path)
    monkeypatch.setattr(sync, "SANDBOX", tmp_path / "sandbox")
    dest = sync.sync_one("thing", str(up), head, dry_run=False)
    (dest / "a.py").write_text("tampered\n")
    (dest / "litter.txt").write_text("junk\n")
    sync.sync_one("thing", str(up), head, dry_run=False)
    assert (dest / "a.py").read_text() == "x = 1\n"
    assert not (dest / "litter.txt").exists()


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    up, head = _upstream(tmp_path)
    sandbox = tmp_path / "sandbox"
    monkeypatch.setattr(sync, "SANDBOX", sandbox)
    sync.sync_one("thing", str(up), head, dry_run=True)
    assert not sandbox.exists()
