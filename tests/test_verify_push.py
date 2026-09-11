"""A push is verified against the remote, not against `git push`'s exit (#255).

Three incidents in one session had the same shape: a check passed, HEAD moved
between commands, and the next git operation acted on a different branch. The
one check meaningful after a push is whether the branch tip is actually on the
remote. These tests pin that logic and the three incident shapes.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import verify_push as vp


def fake_git(*, head: str, local: dict[str, str], remote: dict[str, str]):
    """A runner returning canned output for the three git reads verify() makes."""

    def run(args):
        a = list(args)
        if a[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return head
        if a[:2] == ["git", "rev-parse"]:
            return local.get(a[2], "")
        if a[:2] == ["git", "ls-remote"]:
            ref = a[3]  # refs/heads/<branch>
            branch = ref.split("/", 2)[2]
            sha = remote.get(branch)
            return f"{sha}\t{ref}" if sha else ""
        raise AssertionError(f"unexpected git call: {a}")

    return run


# --- the pure judgment ---------------------------------------------------


def test_a_matching_tip_is_confirmed():
    ok, msg = vp.check_push("main", "main", "abc123456", "abc123456")
    assert ok
    assert "confirmed" in msg


def test_head_on_a_different_branch_fails():
    """Incident 1/2: you pushed `main` while HEAD was on a feature branch."""
    ok, msg = vp.check_push("main", "port/235-mlx-serve", "aaa", "aaa")
    assert not ok
    assert "not 'main'" in msg


def test_a_ref_that_did_not_move_fails():
    """Incident 3: `git push` reported success but the remote tip did not move."""
    ok, msg = vp.check_push("main", "main", "newlocal", "oldremote")
    assert not ok
    assert "did not land" in msg


def test_a_branch_absent_from_origin_fails():
    ok, msg = vp.check_push("feature", "feature", "abc", None)
    assert not ok
    assert "origin has no" in msg


def test_an_unresolved_local_ref_fails():
    ok, msg = vp.check_push("feature", "feature", None, "abc")
    assert not ok
    assert "does not resolve" in msg


# --- end to end through verify(), with a fake git ------------------------


def test_verify_confirms_a_real_push():
    run = fake_git(head="main", local={"main": "abc"}, remote={"main": "abc"})
    ok, _ = vp.verify(run=run)
    assert ok


def test_verify_catches_the_unpushed_commit_that_reported_success():
    # On the mlx branch; `git push origin main` pushed the unmoved main ref.
    run = fake_git(
        head="port/235-mlx-serve",
        local={"main": "old", "port/235-mlx-serve": "new"},
        remote={"main": "old"},
    )
    ok, msg = vp.verify("main", run=run)
    assert not ok
    assert "did not carry your commits" in msg


def test_verify_defaults_to_the_current_branch():
    run = fake_git(head="feature", local={"feature": "x"}, remote={"feature": "y"})
    ok, msg = vp.verify(run=run)
    assert not ok
    assert "did not land" in msg


def test_verify_refuses_a_detached_head():
    """No branch name to check against; refuse clearly rather than probe
    refs/heads/HEAD and report a confusing 'origin has no HEAD'."""

    def run(args):
        a = list(args)
        if a[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return "HEAD"  # what git prints for a detached HEAD
        raise AssertionError(f"should not resolve refs on a detached HEAD: {a}")

    ok, msg = vp.verify(run=run)
    assert not ok
    assert "detached" in msg
