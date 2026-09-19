"""The post-boot daemon gate: is macOS's background work done? #499, #214"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import daemons_idle

PS = """ %CPU COMM
214.8 /System/Library/PrivateFrameworks/MediaAnalysis.framework/Versions/A/mediaanalysisd
 94.9 /System/Library/Frameworks/CoreSpotlight.framework/spotlightknowledged
  9.2 /Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper
  3.0 /usr/libexec/backupd
  4.0 /usr/libexec/backupd
"""


def test_parse_keeps_a_command_path_with_spaces() -> None:
    rows = daemons_idle.parse_ps(PS)
    assert (9.2, "Claude Helper") in rows
    assert (214.8, "mediaanalysisd") in rows


def test_parse_skips_the_header_and_blank_lines() -> None:
    assert daemons_idle.parse_ps(" %CPU COMM\n\n") == []


def test_busy_names_only_watched_daemons_over_the_limit() -> None:
    busy = daemons_idle.busy(daemons_idle.parse_ps(PS), limit=10.0)
    assert busy == {"mediaanalysisd": 214.8, "spotlightknowledged": 94.9}


def test_busy_sums_several_processes_of_one_daemon() -> None:
    """Two backupd at 3% and 4% are 7% of work, not two idle processes."""
    busy = daemons_idle.busy(daemons_idle.parse_ps(PS), limit=5.0)
    assert busy["backupd"] == 7.0


def test_a_daemon_exactly_at_the_limit_is_idle() -> None:
    rows = [(10.0, "mediaanalysisd")]
    assert daemons_idle.busy(rows, limit=10.0) == {}


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _sampler(outputs: list[str]):
    it = iter(outputs)
    return lambda: daemons_idle.parse_ps(next(it))


IDLE = " %CPU COMM\n 1.0 /usr/libexec/backupd\n"
BUSY = " %CPU COMM\n 50.0 /usr/libexec/hybridsearchd\n"


def test_settled_needs_consecutive_idle_samples() -> None:
    """An idle sample between two busy ones is a lull, not settled."""
    clock = _Clock()
    ok = daemons_idle.wait_settled(
        _sampler([IDLE, BUSY, IDLE, IDLE]),
        samples=2,
        interval=60,
        deadline=10_000,
        limit=10.0,
        clock=clock,
    )
    assert ok is True
    assert clock.now == 180  # four samples, three waits between them


def test_the_deadline_refuses_rather_than_waiting_forever() -> None:
    clock = _Clock()
    ok = daemons_idle.wait_settled(
        _sampler([BUSY] * 100),
        samples=2,
        interval=60,
        deadline=300,
        limit=10.0,
        clock=clock,
    )
    assert ok is False
    assert clock.now <= 300


def test_one_sample_that_is_idle_is_enough_when_one_is_asked_for() -> None:
    clock = _Clock()
    assert daemons_idle.wait_settled(
        _sampler([IDLE]), samples=1, interval=60, deadline=0, limit=10.0, clock=clock
    )
    assert clock.now == 0
