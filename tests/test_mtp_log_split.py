"""The split that tells "MTP did nothing" from "MTP was never reached" (#39).

Every fixture is written here. The committed logs under evidence/ are read by
one test only, and it skips when they are absent.
"""

from __future__ import annotations

import logging
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mtp_log_split as mls

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "evidence" / "0039-mtp-ab-logs"

DRAFTED = (
    "ds4: Qwen MTP timing drafted=7 accepted=7 target_tokens=8 "
    "cycle=98.0 ms verifier=block"
)
BYPASSED = (
    "ds4: Qwen MTP timing drafted=0 accepted=0 target_tokens=1 cycle=21.8 ms "
    "verifier=scheduler-bypass"
)


# --- reading the offset ----------------------------------------------------


def test_the_offset_comes_from_run_pys_own_line():
    text = (
        "2026-09-07 08:43:16,264 INFO [5bfb80c@M5-Max-128GB] recording MTP draft "
        "acceptance per row from /x/server.log (ds4-mtp-timing), starting at byte 33967\n"
    )
    assert mls.offset_from_sweep_log(text) == 33967


def test_a_sweep_that_recorded_nothing_is_refused():
    """No boundary means no split. Defaulting to 0 would count the start-up
    prompts as batch traffic and turn the null into a positive."""
    with pytest.raises(ValueError, match="no 'starting at byte N' line"):
        mls.offset_from_sweep_log("2026-09-07 INFO nothing to see\n")


def test_two_different_offsets_are_refused_not_picked():
    text = "starting at byte 100\nstarting at byte 200\n"
    text = text.replace("starting", "recording MTP draft acceptance starting")
    with pytest.raises(ValueError, match="2 different offsets"):
        mls.offset_from_sweep_log(text)


def test_the_same_offset_twice_is_one_answer():
    """A retried read of the same server is not two servers."""
    line = "recording MTP draft acceptance per row from /x (ds4-mtp-timing), starting at byte 55\n"
    assert mls.offset_from_sweep_log(line * 3) == 55


# --- the split itself ------------------------------------------------------


def test_the_two_regions_are_counted_apart():
    before = (DRAFTED + "\n") * 3
    after = (BYPASSED + "\n") * 5
    got = mls.split((before + after).encode(), len(before.encode()))
    assert got["before"]["drafting"] == 3 and got["before"]["bypassed"] == 0
    assert got["after"]["drafting"] == 0 and got["after"]["bypassed"] == 5


def test_no_line_at_all_is_not_the_same_as_bypassed():
    """The whole point. A batch that bypassed reached the scheduler; a batch
    with no line never got there, and only one of those is #151's finding."""
    before = (DRAFTED + "\n") * 3
    silent = mls.split((before + "unrelated server chatter\n").encode(), len(before))
    bypassing = mls.split((before + BYPASSED + "\n").encode(), len(before))
    assert silent["after"]["mtp_timing_lines"] == 0
    assert bypassing["after"]["mtp_timing_lines"] == 1
    assert bypassing["after"]["bypassed"] == 1


def test_an_offset_past_the_end_is_refused():
    """It would hand back an empty `after` -- a manufactured null."""
    with pytest.raises(ValueError, match="past the end"):
        mls.split(b"short", 999)


def test_a_negative_offset_is_refused():
    with pytest.raises(ValueError, match="negative"):
        mls.split(b"short", -1)


def test_an_offset_at_the_exact_end_is_allowed_and_empty():
    """Legal, and the operator should see 0 rather than a refusal: a server
    that produced nothing after the boundary is a real observation."""
    got = mls.split(b"nothing\n", len(b"nothing\n"))
    assert got["after"]["mtp_timing_lines"] == 0


def test_a_log_that_is_not_utf8_is_read_anyway():
    """A truncated multi-byte character mid-log must not lose the counts."""
    body = (DRAFTED + "\n").encode() + b"\xff\xfe\n" + (BYPASSED + "\n").encode()
    got = mls.split(body, 0)
    assert got["after"]["drafting"] == 1 and got["after"]["bypassed"] == 1


# --- the CLI ---------------------------------------------------------------


def test_giving_both_inputs_is_refused(tmp_path, caplog):
    log = tmp_path / "s.log"
    log.write_text("x")
    with caplog.at_level(logging.INFO):
        rc = mls.main(
            ["--server-log", str(log), "--offset", "0", "--sweep-log", str(log)]
        )
    assert rc == 2
    assert any("exactly one" in r.message for r in caplog.records)


def test_giving_neither_input_is_refused(tmp_path, caplog):
    log = tmp_path / "s.log"
    log.write_text("x")
    with caplog.at_level(logging.INFO):
        rc = mls.main(["--server-log", str(log)])
    assert rc == 2


def test_a_missing_log_refuses_rather_than_reporting_zero(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        rc = mls.main(["--server-log", str(tmp_path / "gone.log"), "--offset", "0"])
    assert rc == 2
    assert any("refused" in r.message for r in caplog.records)


def test_the_verdict_line_needs_both_halves(tmp_path, caplog):
    """It may only fire when the control drafted AND the batch was silent."""
    log = tmp_path / "s.log"
    before = (DRAFTED + "\n") * 2
    log.write_text(before + "quiet\n")
    with caplog.at_level(logging.INFO):
        assert mls.main(["--server-log", str(log), "--offset", str(len(before))]) == 0
    assert any("the traffic refused it" in r.message for r in caplog.records)

    caplog.clear()
    log.write_text("quiet\n" + BYPASSED + "\n")
    with caplog.at_level(logging.INFO):
        assert mls.main(["--server-log", str(log), "--offset", "6"]) == 0
    assert not any("the traffic refused it" in r.message for r in caplog.records)


# --- the committed evidence ------------------------------------------------


@pytest.mark.skipif(not EVIDENCE.is_dir(), reason="evidence logs not present")
@pytest.mark.parametrize("sweep", ["new-sweep1", "new-sweep2"])
def test_the_committed_ab_logs_still_say_what_the_evidence_claims(sweep):
    """#39's headline, re-derived from the committed logs on every run."""
    offset = mls.offset_from_sweep_log(
        (EVIDENCE / f"{sweep}.log").read_text(errors="replace")
    )
    got = mls.split((EVIDENCE / f"server-{sweep}.log").read_bytes(), offset)
    assert got["before"]["drafting"] > 0, "the positive control must have drafted"
    assert got["after"]["mtp_timing_lines"] == 0, (
        "the batch must show no MTP line at all -- if this fires, the finding "
        "changed and evidence/0039-mtp-ab.json is stale"
    )


@pytest.mark.skipif(not EVIDENCE.is_dir(), reason="evidence logs not present")
@pytest.mark.parametrize("sweep", ["new-sweep1", "new-sweep2"])
def test_the_committed_batch_region_is_a_faithful_slice(sweep):
    """evidence/0039-mtp-ab.json makes its headline claim with `grep -c` on a
    sliced file, because the evidence verifier runs plain readers and will not
    run this repo's own code. A slice that drifted from the log it was cut
    from would let that claim pass while the log said something else, so cut
    it again here and compare bytes."""
    offset = mls.offset_from_sweep_log(
        (EVIDENCE / f"{sweep}.log").read_text(errors="replace")
    )
    full = (EVIDENCE / f"server-{sweep}.log").read_bytes()
    region = (EVIDENCE / f"server-{sweep}.batch-region.log").read_bytes()
    assert region == full[offset:]
