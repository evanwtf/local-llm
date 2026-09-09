"""#148, mtplx half: deltas are additive, totals are not."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mtplx_trace


def rec(run="r1", accepted=3, drafted=6, total_accepted=None, **extra):
    row = {
        "run_id": run,
        "accepted_drafts_delta": accepted,
        "drafted_tokens_delta": drafted,
        "verify_calls_delta": 2,
        "generated_tokens_delta": accepted + 1,
        "accepted_drafts_total": total_accepted
        if total_accepted is not None
        else accepted,
        **extra,
    }
    return json.dumps(row)


def test_deltas_sum_across_records():
    got = mtplx_trace.read(f"{rec()}\n{rec()}")
    assert got.records == 2
    assert got.accepted == 6 and got.drafted == 12
    assert got.accept_rate == 0.5


def test_totals_that_restart_per_request_do_not_corrupt_the_sum():
    """The trace object is per generation call, so totals reset. Summing
    deltas must be immune to that; differencing totals would not be."""
    text = "\n".join(
        [
            rec(run="r1", accepted=4, drafted=8, total_accepted=4),
            rec(run="r1", accepted=4, drafted=8, total_accepted=8),
            rec(run="r2", accepted=2, drafted=8, total_accepted=2),  # reset
        ]
    )
    got = mtplx_trace.read(text)
    assert got.requests == 2
    assert got.accepted == 10, "a totals reset must not subtract from the count"
    assert got.drafted == 24


def test_no_free_token_is_subtracted():
    """Unlike ds4, mtplx already excludes the primary token."""
    got = mtplx_trace.read(rec(accepted=3, drafted=6))
    assert got.accepted == 3, "subtracting here would under-report a working head"


def test_a_head_that_accepts_nothing_is_the_finding():
    got = mtplx_trace.read("\n".join([rec(accepted=0, drafted=6)] * 5))
    assert got.drafted == 30 and got.accepted == 0
    assert got.used is False
    assert got.accept_rate == 0.0


def test_nothing_drafted_gives_no_rate_rather_than_zero():
    got = mtplx_trace.read(rec(accepted=0, drafted=0))
    assert got.accept_rate is None


def test_a_torn_final_line_is_skipped_not_counted_as_zero():
    """A trace read while it is being written can end mid-line."""
    text = rec() + "\n" + rec()[:20]
    got = mtplx_trace.read(text)
    assert got.records == 1
    assert got.accepted == 3


def test_unrelated_json_lines_are_ignored():
    text = "\n".join([json.dumps({"event": "startup"}), rec()])
    assert mtplx_trace.read(text).records == 1


def test_an_empty_trace_is_distinguishable_from_zero_accepted(tmp_path):
    empty = tmp_path / "t.jsonl"
    empty.write_text("")
    assert mtplx_trace.main([str(empty), "--require-used"]) == 2

    dead = tmp_path / "d.jsonl"
    dead.write_text(rec(accepted=0, drafted=6) + "\n")
    assert mtplx_trace.main([str(dead), "--require-used"]) == 1

    good = tmp_path / "g.jsonl"
    good.write_text(rec() + "\n")
    assert mtplx_trace.main([str(good), "--require-used"]) == 0


def test_read_since_attributes_only_the_new_slice(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text(rec() + "\n")
    first = mtplx_trace.read_since(trace)
    assert first.counters.accepted == 3
    with trace.open("a") as handle:
        handle.write(rec(accepted=1, drafted=6) + "\n")
    second = mtplx_trace.read_since(trace, first.offset)
    assert second.counters.records == 1 and second.counters.accepted == 1
