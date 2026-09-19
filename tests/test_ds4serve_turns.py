"""Tests for scripts/ds4serve_turns.py (#158): per-turn re-prefill from a ds4-server log."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import ds4serve_turns

# Real lines from the #158 stack A/B, 2026-09-19, ds4 main 8db1d1d1.
TOOLS = "0919 07:07:27 ds4-server: chat ctx=24086..35830:11744 TOOLS prompt done 8.245s"
FIRST = "0919 07:04:58 ds4-server: chat ctx=0..53:53 prompt done 0.272s"


def test_a_turn_reads_cached_total_and_seconds():
    t = ds4serve_turns.parse_line(TOOLS)
    assert t is not None
    assert t.cached == 24086
    assert t.total == 35830
    assert t.prefilled == 11744
    assert t.seconds == 8.245


def test_the_prefilled_count_is_what_the_server_printed_not_a_difference():
    """The line prints A..B:N. N is the engine's own count; keep it."""
    line = "0919 07:07:27 ds4-server: chat ctx=100..200:150 TOOLS prompt done 1.0s"
    t = ds4serve_turns.parse_line(line)
    assert t is not None
    assert t.prefilled == 150


def test_a_first_turn_prefills_everything():
    t = ds4serve_turns.parse_line(FIRST)
    assert t is not None
    assert t.cached == 0
    assert t.prefilled == 53
    assert t.rate == pytest.approx(53 / 0.272)


@pytest.mark.parametrize(
    "line",
    [
        "0919 07:04:58 ds4-server: chat ctx=0..53:53 prompt start",
        (
            "0919 07:04:58 ds4-server: chat ctx=0..53:53 prefill chunk 53/53"
            " (100.0%) chunk=0.00 t/s avg=195.14 t/s 0.272s"
        ),
        (
            "0919 07:05:00 ds4-server: chat ctx=98..148:50 gen=50 THINKING"
            " decoding chunk=55.71 t/s avg=55.71 t/s 0.898s"
        ),
        "ds4: memory detail: ctx=100000 prefill_cap=8192",
    ],
)
def test_other_lines_are_not_turns(line):
    assert ds4serve_turns.parse_line(line) is None


def test_buckets_group_turns_by_total_context():
    turns = [ds4serve_turns.parse_line(x) for x in (TOOLS, FIRST)]
    got = ds4serve_turns.by_bucket([t for t in turns if t], edges=(16384, 32768))
    assert [b.label for b in got] == ["0-16K", "32K+"]
    assert got[1].turns == 1
    assert got[1].median_prefilled == 11744
    assert got[1].total_seconds == pytest.approx(8.245)


def test_main_refuses_a_log_with_no_turns(tmp_path):
    log = tmp_path / "server.log"
    log.write_text("ds4: memory detail: ctx=100000\n")
    assert ds4serve_turns.main([str(log)]) == 1


def test_main_reads_several_logs(tmp_path):
    a = tmp_path / "a.log"
    b = tmp_path / "b.log"
    a.write_text(TOOLS + "\n")
    b.write_text(FIRST + "\n")
    assert ds4serve_turns.main([str(a), str(b)]) == 0
