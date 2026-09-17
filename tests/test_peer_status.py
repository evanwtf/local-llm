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
import preflight
from lib import peer_state

_REPO = pathlib.Path(__file__).resolve().parents[1]


def test_peer_state_lives_outside_the_repo() -> None:
    """#238: state a tool writes for its own bookkeeping must not sit in a tree
    whose cleanliness is a measured property. `.claude/peer/status.json` inside
    the repo flipped `harness_dirty` on every row written after it -- splitting
    one A/B's provenance across the arm boundary it exists to qualify."""
    assert _REPO not in peer_status.STATE_FILE.parents, (
        f"peer state is inside the repo at {peer_status.STATE_FILE}"
    )
    assert ".local-llm-bench" in peer_status.STATE_FILE.parts


def test_writer_and_reader_agree_on_the_peer_status_path() -> None:
    """The writer (peer_status) and the reader (machine_state, via preflight)
    must name the same file. A path they disagree on is the #265 defect in the
    other direction: one writes where the other never looks."""
    assert peer_status.STATE_FILE == preflight.PEER_STATUS_PATH


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


# --- the queue, from labels (#463) --------------------------------------------


def _issue(number, *labels, title="t"):
    return {"number": number, "title": title, "labels": [{"name": n} for n in labels]}


def test_next_top10_ranks_one_platform_p0_before_p1(monkeypatch):
    """The committed NEXT.md this used to parse went stale and covered only
    macOS. The queue is now the labels, filtered to one platform."""
    monkeypatch.setattr(
        peer_state,
        "open_p0p1",
        lambda: [
            _issue(300, "P1", "platform:Nvidia"),
            _issue(100, "P0", "platform:Nvidia"),
            _issue(50, "P0", "platform:macOS"),
            _issue(200, "P1", "platform:Nvidia"),
            _issue(250, "P0", "P1", "platform:Nvidia"),  # two priorities: a defect
        ],
    )
    items = peer_state.next_top10("platform:Nvidia")
    assert [i["issue"] for i in items] == [100, 200, 300]
    assert [i["rank"] for i in items] == [1, 2, 3]
    assert [i["priority"] for i in items] == ["P0", "P1", "P1"]


def test_next_top10_defaults_to_this_hosts_platform(monkeypatch):
    monkeypatch.setattr(
        peer_state,
        "open_p0p1",
        lambda: [_issue(1, "P0", "platform:macOS"), _issue(2, "P0", "platform:Nvidia")],
    )
    monkeypatch.setattr(peer_state.platform, "system", lambda: "Linux")
    assert [i["issue"] for i in peer_state.next_top10()] == [2]
    monkeypatch.setattr(peer_state.platform, "system", lambda: "Darwin")
    assert [i["issue"] for i in peer_state.next_top10()] == [1]


def test_open_p0p1_asks_for_either_label_not_both(monkeypatch):
    """gh ANDs repeated `--label` flags, so `--label P0 --label P1` matched
    only issues carrying both -- none. The search qualifier is an OR."""
    seen = {}

    def fake_run(cmd, *args, **kwargs):
        seen["cmd"] = cmd
        return "[]"

    monkeypatch.setattr(peer_state, "_run", fake_run)
    assert peer_state.open_p0p1() == []
    assert "label:P0,P1" in seen["cmd"]
    assert "--label" not in seen["cmd"]
