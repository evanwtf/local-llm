"""#148: `committed` includes a token that cost no speculative work."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

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
    assert sum(c.committed for c in got.cycles) == 20, "the naive sum"
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
