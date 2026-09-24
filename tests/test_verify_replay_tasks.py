"""#714, #726: the replay-task checker reads pytest's summary into the right
counts, and proves held-out tests on a real repository."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import verify_replay_tasks as verify


def test_counts_reads_the_closing_summary():
    out = "tests/test_x.py ....F\n1 failed, 4 passed, 2 skipped in 0.31s\n"
    assert verify.counts(out) == {"passed": 4, "failed": 1, "skipped": 2, "errors": 0}


def test_counts_reads_a_collection_error():
    """A test file that imports a module the revert removed errors at
    collection; that is a failing control, and it must read as one."""
    assert verify.counts("ERROR tests/test_x.py\n1 error in 0.20s\n")["errors"] == 1
    assert verify.counts("2 errors in 0.2s")["errors"] == 2


def test_counts_ignores_an_earlier_line_that_looks_like_a_summary():
    out = "the fixture says 3 passed\n5 passed in 0.1s\n"
    assert verify.counts(out)["passed"] == 5


def test_a_row_shows_run_and_skip_counts_and_the_verdict():
    got = {
        "task": "replay-x",
        "commit": "0ce03e6",
        "added": 31,
        "removed": 6,
        "reverted": 1,
        "deleted": 0,
        "at_commit": {"passed": 19, "failed": 0, "skipped": 2, "errors": 0},
        "after": {"passed": 16, "failed": 3, "skipped": 2, "errors": 0},
        "valid": True,
        "why": "",
    }
    line = verify.row(got)
    assert "| 19 run / 2 skip |" in line
    assert "16 passed, 3 failed, 0 errors" in line
    assert line.endswith("| ok |")


def test_an_invalid_definition_is_reported_not_raised():
    line = verify.row({"task": "replay-x", "valid": False, "why": "no revert"})
    assert "INVALID: no revert" in line


# --- #726: held-out tests, proven on a real repository ----------------------------

TESTS = """\
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import a


def test_x():
    assert a.x == 2


def test_y():
    assert a.y == 3


def test_constant():
    assert a.x > 0


def test_private():
    assert a._secret() == 7


def test_flaky():
    marker = pathlib.Path(__file__).parent / "ran-once"
    first = not marker.exists()
    marker.touch()
    assert first
"""


@pytest.fixture
def repo(tmp_path):
    """P: `x = 1`. C: `x = 2`, `y = 3`, `_secret()`, and tests for each."""
    git = verify.harness.git
    root = tmp_path / "target"
    (root / "src").mkdir(parents=True)
    (root / "src/a.py").write_text("x = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n")
    git(["init", "-q", "-b", "main"], root)

    def commit(message):
        git(["add", "-A"], root)
        git(
            ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message],
            root,
        )
        return git(["rev-parse", "HEAD"], root)

    commit("parent")
    (root / "src/a.py").write_text("x = 2\ny = 3\n\n\ndef _secret():\n    return 7\n")
    (root / "tests").mkdir()
    (root / "tests/test_a.py").write_text(TESTS)
    return root, commit("feature")


def _verify(repo_and_commit, tmp_path, monkeypatch, hidden, visible=None):
    root, sha = repo_and_commit
    monkeypatch.setattr(verify, "sync", lambda tree: None)  # no lock to sync
    task = {
        "name": "replay-toy",
        "kind": "replay",
        "base_commit": sha,
        "revert": ["src/a.py"],
        "tests": visible or ["tests/test_a.py::test_x"],
        "hidden_tests": hidden,
        "test_command": f"{sys.executable} -m pytest -q -p no:cacheprovider",
        # Names y, which only the held-out test_y uses; never _secret.
        "prompt": "Make x 2 and add y = 3. Do not modify any test. "
        "The check also runs tests you cannot see.",
    }
    cfg = {"repo": str(root), "base_commit": sha}
    work = tmp_path / "work"
    work.mkdir()
    return verify.verify(cfg, task, root, work)


def test_a_good_held_out_test_is_proven(repo, tmp_path, monkeypatch):
    got = _verify(repo, tmp_path, monkeypatch, ["tests/test_a.py::test_y"])
    assert got["valid"], got["why"]
    assert got["hidden_at"]["passed"] == 1
    assert got["hidden_repeat_same"] is True
    assert got["hidden_after"]["returncode"] != 0
    assert got["hid"] == [{"test": "tests/test_a.py::test_y", "action": "removed"}]
    line = verify.row(got)
    assert "| 1 ids: 1 run / 0 skip |" in line
    assert "hidden 1 passed (same twice)" in line
    assert line.endswith("| ok |")


def test_a_held_out_test_that_passes_after_the_revert_checks_nothing(
    repo, tmp_path, monkeypatch
):
    got = _verify(repo, tmp_path, monkeypatch, ["tests/test_a.py::test_constant"])
    assert not got["valid"]
    assert "hidden tests still pass after the revert" in got["why"]


def test_a_held_out_test_that_calls_an_unseen_name_is_refused(
    repo, tmp_path, monkeypatch
):
    """`_secret` is named by no prompt, visible test or starting source."""
    got = _verify(repo, tmp_path, monkeypatch, ["tests/test_a.py::test_private"])
    assert not got["valid"]
    assert "cannot read" in got["why"] and "_secret" in got["why"]


def test_a_held_out_test_that_differs_between_runs_is_refused(
    repo, tmp_path, monkeypatch
):
    got = _verify(repo, tmp_path, monkeypatch, ["tests/test_a.py::test_flaky"])
    assert not got["valid"]
    assert "not deterministic" in got["why"]


def test_the_visible_tests_run_on_the_tree_with_the_held_out_ones_cut(
    repo, tmp_path, monkeypatch
):
    """Visible `tests/test_a.py` runs whole; test_y must not be in it."""
    got = _verify(
        repo,
        tmp_path,
        monkeypatch,
        ["tests/test_a.py::test_y", "tests/test_a.py::test_flaky"],
        visible=["tests/test_a.py::test_x", "tests/test_a.py::test_constant"],
    )
    assert got["at_commit"]["passed"] == 2
