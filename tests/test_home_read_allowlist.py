"""Tests for #780: a trial cannot read the operator's home directory.

On 2026-09-25 a trial listed ~/Downloads, extracted a personal mail archive
into $TMPDIR and ran a script over it. The macOS profile was a deny-list of
answer paths, so everything else under $HOME was readable. These tests run the
real profile under `sandbox-exec` against a fake home directory, so they check
the kernel's decision, not the profile's text.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import run

SANDBOX_EXEC = pathlib.Path("/usr/bin/sandbox-exec")
needs_sandbox = pytest.mark.skipif(
    not SANDBOX_EXEC.exists(), reason="needs sandbox-exec (macOS)"
)


@pytest.fixture
def home(tmp_path: pathlib.Path) -> pathlib.Path:
    root = (tmp_path / "home").resolve()
    (root / "Downloads").mkdir(parents=True)
    (root / "Downloads" / "archive.mbox").write_text("From a personal mailbox\n")
    (root / ".cache" / "uv").mkdir(parents=True)
    (root / ".cache" / "uv" / "wheel.txt").write_text("cached\n")
    return root


def _profile(tmp_path, home, worktree=None, repo=None):
    worktree = worktree or tmp_path / "trial"
    worktree.mkdir(parents=True, exist_ok=True)
    repo = repo or home / "git" / "target"
    profile, _denied = run.sandbox_profile(worktree, repo, home=home)
    path = tmp_path / "confine.sb"
    path.write_text(profile)
    return path


def _cat(profile: pathlib.Path, target: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SANDBOX_EXEC), "-f", str(profile), "/bin/cat", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )


@needs_sandbox
def test_a_personal_file_under_home_is_unreadable(tmp_path, home):
    result = _cat(_profile(tmp_path, home), home / "Downloads" / "archive.mbox")
    assert result.returncode != 0
    assert "Operation not permitted" in result.stderr
    assert "personal" not in result.stdout


@needs_sandbox
def test_the_home_directory_cannot_be_listed(tmp_path, home):
    result = subprocess.run(
        [str(SANDBOX_EXEC), "-f", str(_profile(tmp_path, home)), "/bin/ls", str(home)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Downloads" not in result.stdout


@needs_sandbox
def test_an_allowed_tool_directory_stays_readable(tmp_path, home):
    result = _cat(_profile(tmp_path, home), home / ".cache" / "uv" / "wheel.txt")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "cached\n"


@needs_sandbox
def test_a_worktree_under_home_stays_readable(tmp_path, home):
    worktree = home / "bench-work" / "trial-1"
    worktree.mkdir(parents=True)
    (worktree / "a.py").write_text("x = 1\n")
    result = _cat(_profile(tmp_path, home, worktree=worktree), worktree / "a.py")
    assert result.returncode == 0, result.stderr


@needs_sandbox
def test_an_answer_inside_an_allowed_directory_stays_denied(tmp_path, home):
    """The answer denies must beat the allow-list. A deny on `file-read*`
    alone loses to an earlier allow on `file-read-data`, so the profile must
    name `file-read-data` in the deny too."""
    repo = home / ".cache" / "uv" / "target"
    repo.mkdir()
    (repo / "answer.py").write_text("the original body\n")
    result = _cat(_profile(tmp_path, home, repo=repo), repo / "answer.py")
    assert result.returncode != 0
    assert "original" not in result.stdout


def test_answer_rules_follow_the_allow_list(tmp_path, home):
    """Order is the mechanism: the last matching rule wins."""
    repo = home / "git" / "target"
    profile, _ = run.sandbox_profile(tmp_path / "trial", repo, home=home)
    lines = profile.splitlines()
    last_allow = max(
        i for i, line in enumerate(lines) if line.startswith("(allow file-read-data")
    )
    answer = lines.index(f'(deny file-read-data (subpath "{repo}"))')
    assert answer > last_allow
    assert lines.index(f'(deny file-read-data (subpath "{home}"))') < last_allow
