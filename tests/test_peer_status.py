"""Tests for the peer status line (#160).

The status is a diff against the previous run: one line when nothing changed,
an itemized block when something did. The wrong answer here is a status that
reports a change that did not happen, or misses one that did -- a new comment
that never appears is a signal the watcher was told to ignore. So the tests
pin the diff on each signal and the one-line summary.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import peer_status


def test_summary_counts_signals():
    cur = {
        "branches": {"peer/160": "abc", "peer/78": "def"},
        "prs": {"161": "evidence"},
        "comments": {158: 3, 148: 1},
        "trees": {"ds4-metal": "ba01f5d"},
        "lock": "free",
        "servers": [{"short": "ds4-server", "pid": 42, "gib": 12.3, "age": "4h"}],
    }
    line = peer_status._summary(cur)
    assert "2 peer branch(es)" in line
    assert "1 PR(s) open" in line
    assert "4 comment(s)" in line
    assert "1 server(s) up (12.3 GiB)" in line
    assert "lock free" in line


def test_diff_detects_new_comment():
    prev = {"comments": {158: 2}}
    cur = {"comments": {158: 3}}
    changed = peer_status._diff(prev, cur)
    assert any("issue #158: 2 -> 3 comments" in c for c in changed)


def test_diff_detects_new_pr():
    prev = {"prs": {}}
    cur = {"prs": {"161": "evidence"}}
    changed = peer_status._diff(prev, cur)
    assert any("PR #161: opened -> evidence" in c for c in changed)


def test_diff_detects_branch_moved():
    prev = {"branches": {"peer/160": "abc"}}
    cur = {"branches": {"peer/160": "def"}}
    changed = peer_status._diff(prev, cur)
    assert any("branch peer/160: abc -> def" in c for c in changed)


def test_diff_detects_server_up():
    prev = {"servers": []}
    cur = {"servers": [{"short": "ds4-server", "pid": 42}]}
    changed = peer_status._diff(prev, cur)
    assert any("server up: ds4-server (pid 42)" in c for c in changed)


def test_diff_detects_server_down():
    prev = {"servers": [{"short": "ds4-server", "pid": 42}]}
    cur = {"servers": []}
    changed = peer_status._diff(prev, cur)
    assert any("server down: ds4-server (pid 42)" in c for c in changed)


def test_diff_detects_lock_change():
    prev = {"lock": "free"}
    cur = {"lock": "held"}
    changed = peer_status._diff(prev, cur)
    assert any("run lock: free -> held" in c for c in changed)


def test_diff_detects_tree_moved():
    prev = {"trees": {"ds4-metal": "ba01f5d"}}
    cur = {"trees": {"ds4-metal": "236cb2a"}}
    changed = peer_status._diff(prev, cur)
    assert any("tree ds4-metal: ba01f5d -> 236cb2a" in c for c in changed)


def test_diff_empty_when_nothing_changed():
    cur = {
        "branches": {"peer/160": "abc"},
        "prs": {"161": "evidence"},
        "comments": {158: 2},
        "trees": {"ds4-metal": "ba01f5d"},
        "lock": "free",
        "servers": [{"short": "ds4-server", "pid": 42}],
    }
    assert peer_status._diff(cur, cur) == []
