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
from lib import peer_state


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


def test_diff_survives_a_snapshot_that_came_back_from_json():
    """The state file round-trips through JSON, which makes every key a string,
    while a fresh snapshot's comment counts are keyed by int. Comparing them
    raised TypeError on sorted() and took the whole status report down -- the
    tests above never caught it because they key both sides the same way."""
    prev = {"comments": {"158": 2}, "prs": {"161": "evidence"}}
    cur = {"comments": {158: 3}, "prs": {161: "evidence"}}
    changed = peer_status._diff(prev, cur)
    assert any("issue #158: 2 -> 3 comments" in c for c in changed)
    # And the PR did not move, so it must not be reported as opened or closed.
    assert not any("PR #161" in c for c in changed)


def test_diff_orders_issues_numerically():
    prev = {"comments": {}}
    cur = {"comments": {"9": 1, "112": 1, "39": 1}}
    changed = peer_status._diff(prev, cur)
    issues = [c for c in changed if c.startswith("issue #")]
    assert issues == [
        "issue #9: 0 -> 1 comments",
        "issue #39: 0 -> 1 comments",
        "issue #112: 0 -> 1 comments",
    ]


# --- the NEXT.md parse, against the real file (#231) -------------------------


def test_next_top10_parses_the_committed_file():
    """This broke silently on 2026-09-08 and nothing failed.

    NEXT.md became generated, its heading changed from `## The top 10` to
    `## The queue -- 5 P0, 4 P1`, and its items gained a priority prefix.
    `next_top10()` matched neither, returned [], and `peer_status` reported
    "#39: 19 -> 0 comments" for the entire queue -- a peer comment on any of
    them would have been invisible. Every existing test stubbed the function,
    so the suite stayed green.

    Parsing the real file is the only version of this test that would have
    caught it.
    """
    items = peer_state.next_top10()
    assert items, "next_top10() found nothing in the committed NEXT.md"
    assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
    assert all(i["issue"] > 0 for i in items)
    assert all(i["title"] for i in items)
    assert all(i["priority"] in ("P0", "P1") for i in items)


def test_next_md_is_found_in_this_checkout_not_on_this_laptop():
    """The test above went red in CI and green here. `NEXT_MD` was
    `~/git/local-llm/NEXT.md`, a path that resolves on the operator's machine
    and on no runner, so `next_top10()` read nothing and returned []. AGENTS.md
    already carries this shape from `d9a223e`; a hardcoded HOME path is how it
    keeps coming back."""
    root = pathlib.Path(__file__).resolve().parent.parent
    assert peer_state.NEXT_MD == root / "NEXT.md"
    assert peer_state.NEXT_MD.is_file()
    assert pathlib.Path.home() not in peer_state.NEXT_MD.parents or (
        root.is_relative_to(pathlib.Path.home())
    ), "the path must come from the module's location, not from HOME"


# The staleness comparison -- parsed file against the live P0/P1 labels --
# deliberately does NOT live here. It needs the GitHub API, and open_p0p1()
# returns [] on failure by design, so as a test it would pass green whenever
# the network was down: a skipping test wearing a passing test's clothes.
# scripts/make_next.py --check makes the same comparison, in CI, where a
# network failure is visible as a failure.
