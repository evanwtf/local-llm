"""#148: `committed` includes a token that cost no speculative work."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mtp_timing

MICRO = "ds4: mtp timing micro drafted={d} committed={c} draft=1.0 ms snapshot=0.1 ms verify=2.0 ms total=3.1 ms"
DECODE2 = "ds4: mtp timing decode2 drafted={d} committed={c} draft=1.0 ms snapshot=0.1 ms verify=2.0 ms total=3.1 ms"
SKIP = "ds4: mtp timing margin-skip drafted=2 committed=1 margin=0.5 threshold=3.0 draft=1.0 ms verify=2.0 ms total=3.0 ms"


def test_a_rejected_draft_accepts_nothing():
    """margin-skip prints committed=1, and that 1 is the free first token."""
    got = mtp_timing.read(SKIP)
    assert got.cycles[0].accepted == 0
    assert got.accepted == 0
    assert got.used is False, "a skipped draft must not read as a used draft head"


def test_committed_two_is_one_accepted_draft_token():
    got = mtp_timing.read(DECODE2.format(d=2, c=2))
    assert got.cycles[0].accepted == 1
    assert got.cycles[0].proposed == 1


def test_a_head_that_never_accepts_reports_zero_not_a_healthy_total():
    """The failure #148 exists to catch.

    Twenty cycles, every one declined. Summing `committed` naively gives 20,
    which looks like a working draft head. It is zero.
    """
    got = mtp_timing.read("\n".join([SKIP] * 20))
    assert len(got.cycles) == 20
    # The raw `committed=1` on each of those lines sums to 20, which is what
    # a naive reader would report. Resolving the free token at parse time is
    # what makes the true count reachable at all.
    assert got.accepted == 0, "the true count"
    assert got.used is False


def test_accept_rate_excludes_the_free_token_on_both_sides():
    """drafted=7 committed=4 is 3 accepted of 6 proposed, not 4 of 7."""
    got = mtp_timing.read(MICRO.format(d=7, c=4))
    assert got.proposed == 6
    assert got.accepted == 3
    assert got.accept_rate == 0.5


def test_accept_rate_is_none_when_nothing_was_proposed():
    """Not zero -- a rate over no proposals is undefined, and 0.0 would
    read as 'proposed and always rejected', which is a different fact."""
    got = mtp_timing.read(MICRO.format(d=1, c=1))
    assert got.proposed == 0
    assert got.accept_rate is None


def test_it_counts_spec_misses_separately():
    got = mtp_timing.read("ds4: mtp spec miss first draft=1782\n" + SKIP)
    assert got.spec_misses == 1
    assert len(got.cycles) == 1


def test_it_ignores_the_rest_of_a_server_log():
    log = "\n".join(
        [
            "ds4: loading model",
            "ds4: Metal tensor API is on",
            DECODE2.format(d=2, c=2),
            "ds4: invalid tool call returned as assistant text finish=stop",
            SKIP,
        ]
    )
    got = mtp_timing.read(log)
    assert len(got.cycles) == 2
    assert got.accepted == 1


def test_no_counters_at_all_is_distinguishable_from_zero_accepted(tmp_path, caplog):
    """An empty log means the counters were off, not that the head is dead.

    These two must never collapse: one voids the check, the other fails it.
    """
    empty = tmp_path / "empty.log"
    empty.write_text("ds4: loading model\n")
    with caplog.at_level("WARNING", logger="mtp_timing"):
        code = mtp_timing.main([str(empty), "--require-used"])
    assert code == 2, "counters-off must not share an exit code with head-unused"
    assert "DS4_MTP_TIMING" in caplog.text

    dead = tmp_path / "dead.log"
    dead.write_text(SKIP + "\n")
    assert mtp_timing.main([str(dead), "--require-used"]) == 1


def test_require_used_passes_a_working_head(tmp_path):
    good = tmp_path / "good.log"
    good.write_text(MICRO.format(d=7, c=5) + "\n")
    assert mtp_timing.main([str(good), "--require-used"]) == 0


def test_read_since_attributes_only_the_new_slice(tmp_path):
    """A trial's counters are the ones it produced, not the sweep's total."""
    log = tmp_path / "server.log"
    log.write_text(DECODE2.format(d=2, c=2) + "\n")
    first = mtp_timing.read_since(log)
    assert first.counters.accepted == 1

    with log.open("a") as handle:
        handle.write(SKIP + "\n")
    second = mtp_timing.read_since(log, first.offset)
    assert len(second.counters.cycles) == 1, "must not re-count the earlier cycle"
    assert second.counters.accepted == 0


def test_read_since_rereads_a_log_that_shrank(tmp_path, caplog):
    """A new server on the same path must not read as 'no cycles'."""
    log = tmp_path / "server.log"
    log.write_text((DECODE2.format(d=2, c=2) + "\n") * 5)
    far = mtp_timing.read_since(log).offset

    log.write_text(DECODE2.format(d=2, c=2) + "\n")  # restarted, much shorter
    with caplog.at_level("WARNING", logger="mtp_timing"):
        got = mtp_timing.read_since(log, far)
    assert got.counters.accepted == 1, "a shrunk log must be reread, not skipped"
    assert "shrank" in caplog.text


def test_read_since_on_a_missing_log_reports_nothing_and_holds_its_offset(tmp_path):
    got = mtp_timing.read_since(tmp_path / "absent.log", 17)
    assert got.counters.cycles == ()
    assert got.offset == 17


QWEN = "ds4: Qwen MTP timing drafted={d} accepted={a} target_tokens={t} cycle=98.3 ms verifier=block"


def test_the_qwen_path_reports_accepted_draft_tokens_directly(tmp_path):
    """The line qwen38fnds4mtp7shim actually produces.

    `accepted` here is already draft-only -- the free first token is the +1
    in target_tokens (`1u + plan.accepted` in ds4.c). Subtracting again, as
    the decode2 shape requires, would under-report every cycle by one.
    """
    got = mtp_timing.read(QWEN.format(d=7, a=3, t=4))
    assert got.accepted == 3
    assert got.proposed == 7
    assert got.used is True


def test_a_qwen_cycle_that_accepted_nothing():
    got = mtp_timing.read(QWEN.format(d=7, a=0, t=1))
    assert got.accepted == 0 and got.proposed == 7
    assert got.used is False


def test_both_log_shapes_can_appear_and_each_uses_its_own_rule():
    """Same binary, two code paths. One rule for both would be wrong for one."""
    got = mtp_timing.read(QWEN.format(d=7, a=3, t=4) + "\n" + MICRO.format(d=7, c=4))
    assert len(got.cycles) == 2
    assert got.accepted == 3 + 3, "qwen contributes 3, micro contributes 4-1"
    assert got.proposed == 7 + 6


def test_a_broken_target_tokens_relation_is_flagged_not_silently_counted(caplog):
    """If target_tokens stops being accepted+1 the line has changed meaning."""
    with caplog.at_level("WARNING", logger="mtp_timing"):
        got = mtp_timing.read(QWEN.format(d=7, a=3, t=9))
    assert "may have changed" in caplog.text
    assert got.accepted == 3, "still counted, but the reader was told"


BYPASS = "ds4: Qwen MTP timing drafted=0 accepted=0 target_tokens=1 cycle=21.8 ms verifier=scheduler-bypass"


def test_a_bypass_cycle_is_not_drafting():
    got = mtp_timing.read(BYPASS)
    assert got.cycles[0].bypassed is True
    assert got.drafting == 0 and got.bypassed == 1
    assert got.drafting_share == 0.0


def test_an_arm_switched_off_after_a_good_start_still_reports_used(caplog):
    """The real shape of the armB logs, and the reason `used` is too weak.

    ds4's scheduler measures MTP against plain decode and disables it when it
    loses. The head accepted plenty during warmup, so `used` is True and the
    #148 zero-acceptance gate passes -- while almost every cycle afterwards
    drafts nothing. `drafting_share` is what catches it.
    """
    text = "\n".join([QWEN.format(d=7, a=5, t=6)] * 5 + [BYPASS] * 95)
    got = mtp_timing.read(text)
    assert got.used is True, "the zero-acceptance gate would pass this"
    assert got.accepted == 25
    assert got.drafting == 5 and got.bypassed == 95
    assert got.drafting_share == 0.05


def test_drafting_share_is_none_rather_than_zero_with_no_cycles():
    assert mtp_timing.read("").drafting_share is None


def test_a_low_drafting_share_is_warned_about(tmp_path, caplog):
    log = tmp_path / "s.log"
    log.write_text("\n".join([QWEN.format(d=7, a=5, t=6)] * 2 + [BYPASS] * 98))
    with caplog.at_level("WARNING", logger="mtp_timing"):
        mtp_timing.main([str(log)])
    assert "largely NOT an MTP measurement" in caplog.text
