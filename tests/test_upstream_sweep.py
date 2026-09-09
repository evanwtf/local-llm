"""The sweep must see an open PR, not only what has landed on main.

`ddalcu/mlx-serve` was already in WATCHED and had been for weeks. It was
watched the way every repo was: commits and releases. PR383 -- a fix for a
speculative-decoding bug that drops the prefix cache, on our exact model and
our exact machine -- sat open in a fork for a day while #191 spent three and a
half hours benchmarking the five-day-old release that carried the bug. Nothing
in the sweep could have said so.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import upstream_sweep as us

SINCE = "2026-09-07T00:00:00Z"


def _pulls(monkeypatch, payload) -> None:
    monkeypatch.setattr(us, "gh", lambda path: payload if "pulls" in path else [])


def test_an_open_pr_inside_the_window_is_reported(monkeypatch):
    _pulls(
        monkeypatch,
        [
            {
                "number": 383,
                "title": "fix(qwen4): EOS-first spec rounds",
                "updated_at": "2026-09-08T08:23:22Z",
            }
        ],
    )
    got = us.open_pulls("ddalcu/mlx-serve", SINCE)
    assert got == ["#383 fix(qwen4): EOS-first spec rounds"]


def test_a_pr_older_than_the_window_is_not(monkeypatch):
    _pulls(
        monkeypatch,
        [{"number": 1, "title": "ancient", "updated_at": "2026-01-01T00:00:00Z"}],
    )
    assert us.open_pulls("ddalcu/mlx-serve", SINCE) == []


def test_the_listing_stops_at_the_first_stale_pr(monkeypatch):
    """The API is asked for updated-desc, so the first stale entry ends it.

    Without the break a repo with hundreds of open PRs is walked in full on
    every sweep for no extra information.
    """
    _pulls(
        monkeypatch,
        [
            {"number": 9, "title": "fresh", "updated_at": "2026-09-08T00:00:00Z"},
            {"number": 8, "title": "stale", "updated_at": "2026-01-01T00:00:00Z"},
            {"number": 7, "title": "unreachable", "updated_at": "2026-09-08T00:00:00Z"},
        ],
    )
    assert us.open_pulls("r", SINCE) == ["#9 fresh"]


@pytest.mark.parametrize("payload", [None, {}, "not a list", [None, 3]])
def test_a_malformed_pulls_response_is_not_a_crash(monkeypatch, payload):
    """A sweep runs unattended; a shape it did not expect must not end it."""
    _pulls(monkeypatch, payload)
    assert us.open_pulls("r", SINCE) == []


def test_sweep_carries_pulls_so_quiet_empty_cannot_hide_them(monkeypatch):
    """A repo whose only news is an open PR must not read as idle.

    This is the whole failure: main was quiet, no release was cut, and the one
    thing that mattered was open in a fork.
    """

    def fake(path: str):
        if "pulls" in path:
            return [
                {
                    "number": 383,
                    "title": "the fix",
                    "updated_at": "2026-09-08T08:00:00Z",
                }
            ]
        return []

    monkeypatch.setattr(us, "gh", fake)
    got = us.sweep("ddalcu/mlx-serve", SINCE)
    assert got["commits"] == [] and got["releases"] == []
    assert got["pulls"] == ["#383 the fix"], "an idle main is not an idle repo"
