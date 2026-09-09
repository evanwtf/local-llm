"""The drafting audit (#210, #39).

Every test here is drawn from a mistake that reached a published comment. A
test that only proves the arithmetic is self-consistent would have caught none
of them.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mtp_draft_audit as audit

QWEN = "ds4: Qwen MTP timing drafted={d} accepted={a} target_tokens={t} cycle=1.0 ms"


def qwen_log(cycles: list[tuple[int, int]]) -> str:
    """A server log of Qwen cycles as (drafted, accepted) pairs, with noise."""
    lines = ["ds4: Qwen graph allocated: ctx=100000 MTP=Q4_K/Q8_0/BF16"]
    for drafted, accepted in cycles:
        lines.append(QWEN.format(d=drafted, a=accepted, t=accepted + 1))
    lines.append("ds4: some other line that mentions MTP and accepted")
    return "\n".join(lines) + "\n"


def ledger(rows: list[dict], batch: str = "b", backend: str = "arm") -> str:
    out = []
    for r in rows:
        out.append(json.dumps({"batch": batch, "backend": backend, "draft": r}))
    return "\n".join(out) + "\n"


# --- the failure that motivated the script -----------------------------------


def test_a_word_count_is_not_a_drafting_count():
    """2026-09-08: `20394 MTP, 16154 accepted` were published as drafting
    metrics. They were `grep -o` occurrence counts of those two WORDS in the
    log. The parser only counts lines it recognises as cycles, so prose that
    happens to contain either word contributes nothing."""
    text = qwen_log([(3, 2)]) + "MTP MTP MTP accepted accepted accepted\n" * 100
    totals = audit.from_log(text)
    assert totals.cycles == 1
    assert totals.proposed == 3
    assert totals.accepted == 2


def test_total_cycles_is_not_the_drafting_count():
    """The same comment reported 15934 -- the total cycle count -- as the
    number that drafted. Of those, 5074 drafted. A cycle that proposes nothing
    is a cycle the scheduler declined, and counting it as a draft overstated
    drafting by 3.1x."""
    text = qwen_log([(0, 0)] * 7 + [(4, 2)] * 3)
    totals = audit.from_log(text)
    assert totals.cycles == 10
    assert totals.drafting == 3
    assert totals.drafting_share == pytest.approx(0.3)


def test_the_sentence_cannot_be_read_as_a_cycle_count():
    """The rendered claim names both numbers, so 'drafted' can never be quoted
    as the total the way it was."""
    totals = audit.from_log(qwen_log([(0, 0)] * 7 + [(4, 2)] * 3))
    said = audit.sentence(totals)
    assert "3 of 10 cycles" in said
    assert "30.0%" in said
    assert "12 tokens of which 6 were accepted" in said


# --- the two cycle shapes ----------------------------------------------------


def test_the_micro_shape_loses_its_free_first_token_and_the_qwen_shape_does_not():
    """ds4 prints two shapes. The Qwen line's `accepted=` is already
    draft-only; the micro line's `committed=` includes a token verified for
    free. Summing both the same way -- which is what an ad-hoc awk does -- is a
    one-token-per-cycle error in one direction or the other."""
    qwen = audit.from_log(qwen_log([(2, 1)]))
    micro = audit.from_log("ds4: mtp timing micro drafted=2 committed=1 draft=1 ms\n")
    assert qwen.accepted == 1, "the Qwen line needs no subtraction"
    assert micro.accepted == 0, "committed=1 means no DRAFT token was accepted"
    assert micro.proposed == 1, "but one draft token was still proposed"


def test_a_rejected_draft_is_not_the_same_as_no_draft():
    """I got this wrong writing the test above: `drafted=2 committed=1` is a
    cycle that drafted one token and had it rejected, not a cycle that drafted
    nothing. A bypass is `drafted=1 committed=1` -- the scheduler decided
    drafting was a net loss and ran plain decode, and it still prints a cycle.

    The difference is the whole of #210. An arm whose scheduler bypasses every
    cycle has an accept rate computed over nothing, and reads as healthy."""
    rejected = audit.from_log(
        "ds4: mtp timing micro drafted=2 committed=1 draft=1 ms\n"
    )
    bypassed = audit.from_log(
        "ds4: mtp timing micro drafted=1 committed=1 draft=0 ms\n"
    )
    assert rejected.drafting == 1 and rejected.accepted == 0
    assert bypassed.drafting == 0, "nothing was proposed, so nothing drafted"
    assert bypassed.drafting_share == 0.0


# --- log against ledger ------------------------------------------------------


def test_the_two_sources_agree_on_the_share_despite_different_totals():
    """The log sees a whole server process, the ledger sees per-task windows,
    so the totals differ by the warmup and the gaps between tasks. The share
    is what has to agree -- 31.8% from both sides over the one paired reading
    this project has."""
    log = audit.from_log(qwen_log([(0, 0)] * 68 + [(4, 2)] * 32))
    led = audit.from_ledger(
        ledger([{"cycles": 90, "drafting": 29, "proposed": 116, "accepted": 58}]), "b"
    )
    assert log.cycles != led.cycles
    assert audit.shares_agree(log, led)


def test_a_share_that_disagrees_is_an_error_not_a_number_to_publish():
    """If the per-task windows miss cycles the server saw, every per-row
    drafting figure is wrong. That is a failure to report, not a discrepancy
    to average away."""
    log = audit.from_log(qwen_log([(4, 2)] * 90))
    led = audit.from_ledger(
        ledger([{"cycles": 90, "drafting": 5, "proposed": 20, "accepted": 10}]), "b"
    )
    assert not audit.shares_agree(log, led)


def test_a_log_matched_against_an_empty_batch_is_a_disagreement():
    """An empty ledger side is the shape of a log read against the wrong batch
    name. Reporting 0% would look like a finding; it is a typo."""
    log = audit.from_log(qwen_log([(4, 2)] * 10))
    led = audit.from_ledger(ledger([{"cycles": 1, "drafting": 1}], batch="other"), "b")
    assert led.cycles == 0
    assert not audit.shares_agree(log, led)


def test_two_empty_sources_agree():
    empty = audit.from_log("no cycles here\n")
    assert audit.shares_agree(empty, empty)
    assert empty.drafting_share is None
    assert audit.sentence(empty) == "no MTP cycles were observed at all"


# --- ledger selection --------------------------------------------------------


def test_rows_without_a_draft_block_are_skipped_not_counted_as_zero():
    """A non-MTP row counted as zero cycles would dilute the share with work
    that was never observed."""
    text = "\n".join(
        [
            json.dumps(
                {"batch": "b", "backend": "arm", "draft": {"cycles": 10, "drafting": 5}}
            ),
            json.dumps({"batch": "b", "backend": "arm"}),
            json.dumps({"batch": "b", "backend": "arm", "draft": None}),
        ]
    )
    assert len(audit.ledger_rows(text, "b")) == 1
    assert audit.from_ledger(text, "b").cycles == 10


def test_the_backend_filter_separates_the_arms_of_one_batch():
    """Both arms of an A/B share a batch label. Pooling them would report the
    control's zero drafting as part of the treatment's share."""
    text = "\n".join(
        [
            json.dumps(
                {"batch": "b", "backend": "mtp", "draft": {"cycles": 10, "drafting": 8}}
            ),
            json.dumps(
                {
                    "batch": "b",
                    "backend": "plain",
                    "draft": {"cycles": 10, "drafting": 0},
                }
            ),
        ]
    )
    assert audit.from_ledger(text, "b").drafting_share == pytest.approx(0.4)
    assert audit.from_ledger(text, "b", "mtp").drafting_share == pytest.approx(0.8)
    assert audit.from_ledger(text, "b", "plain").drafting_share == pytest.approx(0.0)


def test_a_corrupt_ledger_line_is_skipped_rather_than_fatal():
    """results.jsonl is appended to by a live run. A half-written last line
    must not take down an audit of the rows that are complete."""
    text = (
        json.dumps(
            {"batch": "b", "backend": "arm", "draft": {"cycles": 4, "drafting": 2}}
        )
        + "\n{ this is not json\n"
    )
    assert audit.from_ledger(text, "b").cycles == 4
