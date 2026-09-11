"""Per-trial prefill-failure counting (#266).

The 500 the client retries is invisible at read-out today. These pin the count
and, most importantly, the per-trial windowing: a trial must be credited only
with the failures inside its own byte slice, and "no server log" must read as
unknown, never as zero.
"""

from __future__ import annotations

import prefill_failures

FAIL = 'metal Qwen prefill failed at position 16729", "type":"invalid_request_error"'


def test_count_is_zero_on_a_clean_log() -> None:
    assert prefill_failures.count("GET /v1/models 200\nprefill ok\n") == 0


def test_count_matches_each_occurrence() -> None:
    text = f"{FAIL}\nsome other line\nmetal Qwen prefill failed at position 4951\n"
    assert prefill_failures.count(text) == 2


def test_count_requires_a_position_number() -> None:
    """'prefill failed' without a position is not this 500; do not count it."""
    assert prefill_failures.count("prefill failed for an unrelated reason\n") == 0


def test_read_since_counts_only_the_new_bytes(tmp_path) -> None:
    log = tmp_path / "server.log"
    log.write_text(f"{FAIL}\n")
    first = prefill_failures.read_since(log)
    assert first.failures == 1
    # Nothing appended: the next read from that offset sees zero.
    assert prefill_failures.read_since(log, first.offset).failures == 0
    # Append one more failure; only it is counted from the carried offset.
    with log.open("a") as fh:
        fh.write(f"{FAIL}\n")
    assert prefill_failures.read_since(log, first.offset).failures == 1


def test_read_since_missing_file_is_zero_not_an_error(tmp_path) -> None:
    reading = prefill_failures.read_since(tmp_path / "nope.log", 500)
    assert reading.failures == 0 and reading.offset == 500


def test_read_since_survives_an_open_failure_after_stat(tmp_path) -> None:
    """A log rotated away between stat and open must not take the run down.

    A directory passes stat() but raises on open(); the read falls back to zero
    at the carried offset rather than propagating (#266 Codex review).
    """
    reading = prefill_failures.read_since(tmp_path, 42)
    assert reading.failures == 0 and reading.offset == 42


def test_read_since_rereads_a_shrunk_log_from_the_start(tmp_path) -> None:
    """A new server on the same path must not leave the offset past the end."""
    log = tmp_path / "server.log"
    log.write_text(f"{FAIL}\n{FAIL}\n")
    big_offset = log.stat().st_size + 10_000
    assert prefill_failures.read_since(log, big_offset).failures == 2


def test_probe_with_no_path_samples_none() -> None:
    """No server log means unknown, never zero."""
    assert prefill_failures.Probe(None).sample() is None


def test_probe_starts_at_the_end_so_startup_chatter_is_not_trial_one(tmp_path) -> None:
    log = tmp_path / "server.log"
    log.write_text(f"startup chatter\n{FAIL}\n")  # a failure before the probe exists
    probe = prefill_failures.Probe(log)  # constructed after the gate ran
    # Trial 1 provokes one failure of its own.
    with log.open("a") as fh:
        fh.write(f"{FAIL}\n")
    assert probe.sample() == 1  # the pre-probe failure is not credited to it


def test_probe_samples_each_trials_own_window(tmp_path) -> None:
    log = tmp_path / "server.log"
    log.write_text("")
    probe = prefill_failures.Probe(log)
    with log.open("a") as fh:
        fh.write(f"{FAIL}\n{FAIL}\n")
    assert probe.sample() == 2  # trial 1
    with log.open("a") as fh:
        fh.write(f"{FAIL}\n")
    assert probe.sample() == 1  # trial 2, not the running total
    assert probe.sample() == 0  # trial 3 ran clean
