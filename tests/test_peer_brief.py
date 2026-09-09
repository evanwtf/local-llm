"""Tests for the handoff brief (#160).

The brief is the derivable half of a handoff, rendered as Markdown. The wrong
answer here is a brief that reads as authoritative while silently dropping a
signal -- a P0 that never appears, a dirty tree reported clean, a held lock
reported free. So the tests pin the rendering of each signal and the NEXT.md
invariant, with the network and filesystem calls monkeypatched away.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import peer_brief
from lib import peer_state


def _monkey_state(monkeypatch, **overrides) -> None:
    """Point every peer_state call at canned values."""
    defaults = {
        "git_head": lambda repo: "abc1234",
        "git_dirty": lambda repo: [],
        "next_top10": lambda: [
            {"rank": 1, "issue": 158, "title": "upstream the fork"},
            {"rank": 2, "issue": 148, "title": "prove the draft head"},
        ],
        "open_p0p1": lambda: [
            {"number": 158, "title": "upstream the fork", "labels": [{"name": "P0"}]},
            {
                "number": 148,
                "title": "prove the draft head",
                "labels": [{"name": "P0"}],
            },
        ],
        "run_lock": lambda: ("free", "no lock held"),
        "servers": list,
        "ds4_trees": list,
        "tree_drift": lambda tree: None,
        "commits_since": lambda repo, ref: [],
    }
    defaults.update(overrides)
    for name, fn in defaults.items():
        monkeypatch.setattr(peer_state, name, fn)


def test_brief_renders_head_and_dirty(monkeypatch):
    _monkey_state(monkeypatch, git_dirty=lambda repo: [" M scripts/evidence.py"])
    out = peer_brief.brief(pathlib.Path("/x/local-llm"), None)
    assert "abc1234" in out
    assert "scripts/evidence.py" in out


def test_brief_renders_top10_with_labels(monkeypatch):
    _monkey_state(monkeypatch)
    out = peer_brief.brief(pathlib.Path("/x/local-llm"), None)
    assert "1. #158 (P0) -- upstream the fork" in out
    assert "2. #148 (P0) -- prove the draft head" in out


def test_brief_renders_lock_and_servers(monkeypatch):
    _monkey_state(
        monkeypatch,
        run_lock=lambda: ("held", "pid 42 is running a batch"),
        servers=lambda: [
            type(
                "P",
                (),
                {"short": "ds4-server", "pid": 42, "rss_gib": 12.3, "age": "4h13m"},
            )()
        ],
    )
    out = peer_brief.brief(pathlib.Path("/x/local-llm"), None)
    assert "held (pid 42 is running a batch)" in out
    assert "ds4-server (pid 42) 12.3 GiB after 4h13m" in out


def test_brief_renders_trees(monkeypatch):
    _monkey_state(
        monkeypatch,
        ds4_trees=lambda: [pathlib.Path("/x/ds4-metal")],
        tree_drift=lambda tree: {
            "head": "ba01f5d",
            "dirty": True,
            "stale": True,
            "note": "3 behind upstream/main",
        },
    )
    out = peer_brief.brief(pathlib.Path("/x/local-llm"), None)
    assert "ds4-metal @ ba01f5d -- 3 behind upstream/main [UNCOMMITTED]" in out


def test_brief_renders_commits_since(monkeypatch):
    _monkey_state(monkeypatch, commits_since=lambda repo, ref: ["abc1234 add thing"])
    out = peer_brief.brief(pathlib.Path("/x/local-llm"), "9ab7053")
    assert "## Commits since 9ab7053" in out
    assert "abc1234 add thing" in out


def test_invariant_holds_when_top10_matches_p0p1(monkeypatch):
    _monkey_state(monkeypatch)
    assert peer_brief._invariant_check() is None


def test_invariant_warns_on_mismatch(monkeypatch):
    _monkey_state(
        monkeypatch,
        open_p0p1=lambda: [
            {"number": 158, "title": "upstream the fork", "labels": [{"name": "P0"}]},
            {"number": 999, "title": "not in top 10", "labels": [{"name": "P1"}]},
        ],
    )
    warning = peer_brief._invariant_check()
    assert warning is not None
    assert "INVARIANT BROKEN" in warning
    assert "999" in warning


def test_dirty_line_truncates(monkeypatch):
    paths = [f" M file{i}.py" for i in range(12)]
    line = peer_brief._dirty_line(paths)
    assert "file0.py" in line
    assert "+4 more" in line


def test_lock_line_free(monkeypatch):
    assert peer_brief._lock_line("free", "no lock held") == "free"


def test_lock_line_held(monkeypatch):
    assert peer_brief._lock_line("held", "pid 1 is running a batch") == (
        "held (pid 1 is running a batch)"
    )
