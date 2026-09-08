"""Per-trial attribution of the #39 recovery-failed events (#39).

The #39 comment on the MTP pass gap ended with the admission that the
per-trial attribution was not done. These tests pin the join: a death is a FAIL
verdict, a recovery-failed kills a trial only when it is the trial's last server
event, and the recovery-failed position is a frontier-short position. The
numbers (42 recovery-failed, 9 deaths) are re-derived from the log, never
inherited.

The join is by time window [prev verdict, this verdict]. The tests use synthetic
client and server logs so the window rule, the last-event rule, and the
frontier-short set are each pinned in isolation.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mtp_recovery_attribution as mra


def _write(path: pathlib.Path, lines: list[str]) -> pathlib.Path:
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    return path


def _client_log(path: pathlib.Path, tri: list[tuple[str, str, str]]) -> pathlib.Path:
    lines = []
    for task, verdict, t in tri:
        lines.append(
            f"2026-09-07 {t},123 INFO [x@m] {task}-qwen38fnds4mtp7shim-opencode-1: {verdict} in 3.0s"
        )
    return _write(path, lines)


def _server_log(path: pathlib.Path, evs: list[str]) -> pathlib.Path:
    return _write(path, evs)


def test_parse_client_verdicts_in_order(tmp_path: pathlib.Path) -> None:
    p = _client_log(
        tmp_path / "c.log",
        [("task-a", "PASS", "08:43:00"), ("task-b", "FAIL", "08:44:00")],
    )
    trials = mra.parse_client_verdicts(p)
    assert [t.task for t in trials] == ["task-a", "task-b"]
    assert [t.verdict for t in trials] == ["PASS", "FAIL"]
    assert trials[1].end == dt.datetime(2026, 9, 7, 8, 44, 0, tzinfo=dt.UTC)


def test_parse_server_records_finish_and_recovery_position(
    tmp_path: pathlib.Path,
) -> None:
    p = _server_log(
        tmp_path / "s.log",
        [
            "0907 08:43:29 ds4-server: chat ctx=0..10:10 gen=1 finish=stop",
            '0907 08:43:30 ds4-server: chat ctx=0..10:10 gen=1 finish=error error="invalid tool call recovery failed: metal Qwen prefill failed at position 11722"',
            '0907 08:43:31 ds4-server: chat ctx=0..10:10 gen=1 finish=error error="invalid tool call"',
        ],
    )
    events = mra.parse_server_events(p)
    assert [(e.finish, e.is_recovery, e.position) for e in events] == [
        ("stop", False, None),
        ("error", True, 11722),
        ("error", False, None),
    ]


def test_frontier_short_positions(tmp_path: pathlib.Path) -> None:
    p = _server_log(
        tmp_path / "s.log",
        [
            "ds4: Qwen MTP history frontier short cache=11664 expected=11721 rows=60 pos=11722 trunk=1",
            "ds4: Qwen MTP history frontier short cache=17339 expected=17460 rows=466 pos=17461 trunk=1",
        ],
    )
    assert mra.frontier_short_positions(p) == {11722, 17461}


def test_window_join_assigns_events_to_the_trial_that_contains_them(
    tmp_path: pathlib.Path,
) -> None:
    """Trial A runs [sweep_start, 08:44], trial B runs [08:44, 08:46]. A
    recovery-failed at 08:45 is in B, not A."""
    trials_path = _client_log(
        tmp_path / "c.log", [("a", "PASS", "08:44:00"), ("b", "FAIL", "08:46:00")]
    )
    server_path = _server_log(
        tmp_path / "s.log",
        [
            '0907 08:45:30 ds4-server: chat ctx=0..10:10 gen=1 finish=error error="invalid tool call recovery failed: metal Qwen prefill failed at position 5"'
        ],
    )
    trials = mra.parse_client_verdicts(trials_path)
    events = mra.parse_server_events(server_path)
    start = dt.datetime(2026, 9, 7, 8, 42, 0, tzinfo=dt.UTC)
    windows = mra.assign_to_trials(events, trials, start)
    assert len(windows[0]) == 0
    assert len(windows[1]) == 1


def test_a_recovery_failed_early_in_a_trial_is_retried_not_a_death(
    tmp_path: pathlib.Path,
) -> None:
    """A recovery-failed followed by a finish=stop is mid-trial; the trial's
    last event is stop, so it did not die of the recovery-failed."""
    trials_path = _client_log(tmp_path / "c.log", [("a", "PASS", "08:44:00")])
    server_path = _server_log(
        tmp_path / "s.log",
        [
            '0907 08:43:30 ds4-server: chat ctx=0..10:10 gen=1 finish=error error="invalid tool call recovery failed: metal Qwen prefill failed at position 11722"',
            "0907 08:43:35 ds4-server: chat ctx=0..10:10 gen=1 finish=stop",
        ],
    )
    trials = mra.parse_client_verdicts(trials_path)
    events = mra.parse_server_events(server_path)
    windows = mra.assign_to_trials(
        events, trials, dt.datetime(2026, 9, 7, 8, 42, 0, tzinfo=dt.UTC)
    )
    rows = mra.per_trial_rows(trials, windows)
    assert rows[0].n_recovery == 1
    assert rows[0].last_finish == "stop"
    assert rows[0].last_is_recovery is False


def test_only_a_recovery_failed_as_the_last_event_is_a_death(
    tmp_path: pathlib.Path,
) -> None:
    trials_path = _client_log(tmp_path / "c.log", [("a", "FAIL", "08:44:00")])
    server_path = _server_log(
        tmp_path / "s.log",
        [
            '0907 08:43:59 ds4-server: chat ctx=0..10:10 gen=1 finish=error error="invalid tool call recovery failed: metal Qwen prefill failed at position 7"'
        ],
    )
    trials = mra.parse_client_verdicts(trials_path)
    events = mra.parse_server_events(server_path)
    windows = mra.assign_to_trials(
        events, trials, dt.datetime(2026, 9, 7, 8, 42, 0, tzinfo=dt.UTC)
    )
    rows = mra.per_trial_rows(trials, windows)
    assert rows[0].verdict == "FAIL"
    assert rows[0].last_is_recovery is True
    assert rows[0].death is True
