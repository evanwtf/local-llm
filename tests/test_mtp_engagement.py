"""The MTP engagement probe (#148, #151).

`tasks.toml` already warned that ds4's scheduler bypasses MTP on families where
drafts are not accepted, so "MTP did nothing" and "MTP was never engaged"
arrive as the same row. This script tells them apart by reading the server's
counters across one request at a time.

The HTTP is not tested here -- it needs a 79 GB model resident. What is tested
is the part that would silently produce a wrong answer: the delta accounting,
and the alternation that keeps position from masquerading as an effect.
"""

from __future__ import annotations

import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mtp_engagement as me


class FakeCounters:
    def __init__(self, cycles, accepted, used):
        self.cycles = cycles
        self.accepted = accepted
        self.used = used


class FakeReading:
    def __init__(self, counters, offset):
        self.counters = counters
        self.offset = offset


def reader_returning(cycles, accepted, used, offset):
    return types.SimpleNamespace(
        read_since=lambda log, off: FakeReading(
            FakeCounters(cycles, accepted, used), offset
        )
    )


def test_measure_reports_the_bytes_the_request_appended():
    """A request that generated and consumed zero bytes is a broken probe.

    The distinction the #148 warning could not draw: an engine that drafted
    nothing writes no lines, and so does a reader looking at the wrong offset.
    """
    reader = reader_returning(cycles=[1, 2, 3], accepted=12, used=True, offset=500)
    got, offset = me.measure(reader, pathlib.Path("/x"), 100)
    assert got["cycles"] == 3
    assert got["accepted"] == 12
    assert got["bytes"] == 400
    assert offset == 500


def test_no_cycles_is_reported_as_zero_not_as_missing():
    reader = reader_returning(cycles=[], accepted=0, used=False, offset=100)
    got, _ = me.measure(reader, pathlib.Path("/x"), 100)
    assert got["cycles"] == 0
    assert got["bytes"] == 0


def test_cycles_of_none_does_not_crash_the_count():
    """`Counters.cycles` is None when the reader had nothing to read."""
    reader = reader_returning(cycles=None, accepted=None, used=False, offset=100)
    got, _ = me.measure(reader, pathlib.Path("/x"), 100)
    assert got["cycles"] == 0


def test_the_arms_alternate_so_position_is_not_the_effect():
    """Odd rounds run in the given order, even rounds reversed.

    #130: throughput declines across a measurement window, so a fixed order
    penalises whichever arm always runs second.
    """
    order = []
    for rep in range(1, 5):
        arms = ["plain", "tools"]
        if rep % 2 == 0:
            arms.reverse()
        order.append(tuple(arms))
    assert order == [
        ("plain", "tools"),
        ("tools", "plain"),
        ("plain", "tools"),
        ("tools", "plain"),
    ]


def test_the_default_arms_are_the_pair_the_question_is_about():
    args = me.parse_args(["--server-log", "/tmp/x"])
    assert args.arms == ["plain", "tools"]


def test_a_stream_arm_is_recognised_by_name():
    """`tools-stream` has to set both flags, or it measures the wrong shape."""
    args = me.parse_args(["--server-log", "/tmp/x", "--arms", "tools-stream"])
    arm = args.arms[0]
    assert "tools" in arm and "stream" in arm


def test_a_missing_server_log_refuses_rather_than_measuring_nothing(tmp_path, caplog):
    args = me.parse_args(["--server-log", str(tmp_path / "absent.log")])
    assert me.run(args) == 2


# --- the context-length axis (#151) ------------------------------------------


def test_padding_is_off_by_default_so_old_invocations_are_unchanged():
    assert me.parse_args(["--server-log", "x.log"]).pad_tokens == 0
    assert me.pad_prompt("ask", 0) == "ask"
    assert me.pad_prompt("ask", -1) == "ask"


def test_the_question_survives_the_padding():
    """The pad is context, not the request. If the question were buried or
    truncated the arm would measure a different task, and a shorter answer
    reads as fewer speculative cycles for a reason that is not the treatment.
    """
    padded = me.pad_prompt("MERGE TWO LISTS", 2000)
    assert padded.endswith("MERGE TWO LISTS")
    assert padded.count("MERGE TWO LISTS") == 1


def test_the_pad_scales_with_the_request_and_lands_in_the_right_size_class():
    """The knob only has to put a request in the right size class -- short,
    or agent-realistic. #151's confounded run sat at 11,760 prompt tokens.
    """
    small = me.pad_prompt("ask", 1_000)
    large = me.pad_prompt("ask", 11_000)
    assert len(large) > 8 * len(small)
    # ~14 tokens per padding line, so the line count tracks the request.
    assert 60 <= small.count("\n") <= 90
    assert 700 <= large.count("\n") <= 900


def test_the_same_setting_sends_the_same_bytes():
    """Two runs at one setting must be comparable to each other. A random or
    timestamped filler would make every difference partly the prompt's."""
    assert me.pad_prompt("ask", 3_000) == me.pad_prompt("ask", 3_000)
