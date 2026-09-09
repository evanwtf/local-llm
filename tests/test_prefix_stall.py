"""#64: the live-KV prefix stall read out of a corpus of ds4-server logs."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import prefix_stall as ps

#: The line #64 was filed from, and three neighbours from the same trial.
REAL_LINES = """\
0905 19:16:06 ds4-server: live kv cache miss live=54 prompt=98 common=45 vision=match reason=token-mismatch
0905 19:17:53 ds4-server: live kv cache miss live=19953 prompt=11928 common=10901 vision=match reason=token-mismatch
0905 19:20:11 ds4-server: live kv cache miss live=47958 prompt=25468 common=20398 vision=match reason=token-mismatch
0905 19:25:02 ds4-server: live kv cache miss live=66171 prompt=65794 common=20398 vision=match reason=token-mismatch
"""


def test_every_field_is_parsed():
    misses = ps.parse_misses(REAL_LINES)
    assert len(misses) == 4
    assert misses[2] == ps.Miss(
        live=47958,
        prompt=25468,
        common=20398,
        vision="match",
        reason="token-mismatch",
    )


def test_a_line_without_vision_or_reason_still_parses():
    """Older builds print neither. A stricter pattern would silently drop
    every miss from those logs and report a clean cache."""
    misses = ps.parse_misses("live kv cache miss live=10 prompt=20 common=5\n")
    assert misses == [ps.Miss(live=10, prompt=20, common=5, vision=None, reason=None)]


def test_other_lines_carrying_tokens_are_not_counted():
    """The disk-store lines share the log and the word `tokens`. A loose
    pattern pulls them in and inflates every count in the report."""
    text = (
        "ds4-server: kv cache stored tokens=2048 trimmed=597 reason=cold\n"
        "ds4-server: kv cache hit text tokens=10240 quant=4 key=token-text\n"
    )
    assert ps.parse_misses(text) == []


def test_the_plateau_is_the_high_water_mark_not_the_last_value():
    """`common` can fall back on a late miss. The plateau is the best prefix
    ever reused, so a dip must not be read as the cache improving."""
    misses = ps.parse_misses(REAL_LINES)
    s = ps.summarise(pathlib.Path("x.log"), misses)
    assert s.best_common == 20398


def test_a_turn_far_past_the_plateau_counts_as_stalled():
    """prompt 65794 against a best prefix of 20398 is 45k re-prefilled."""
    s = ps.summarise(pathlib.Path("x.log"), ps.parse_misses(REAL_LINES))
    assert s.stalled_turns == 2  # the 25468 and 65794 turns
    assert s.stalled


def test_a_growing_cache_is_not_stalled():
    """`common` tracking `prompt` upward is a working cache, and must not be
    reported as a stall however many misses it takes to get there."""
    text = "".join(
        f"live kv cache miss live={n} prompt={n} common={n - 100} "
        f"vision=match reason=token-mismatch\n"
        for n in (1000, 5000, 20000, 60000)
    )
    s = ps.summarise(pathlib.Path("x.log"), ps.parse_misses(text))
    assert s.best_common == 59900
    assert s.stalled_turns == 0
    assert not s.stalled


def test_reprefilled_tokens_sum_the_gap_not_the_prompt():
    s = ps.summarise(pathlib.Path("x.log"), ps.parse_misses(REAL_LINES))
    expected = (98 - 45) + (11928 - 10901) + (25468 - 20398) + (65794 - 20398)
    assert s.reprefilled_tokens == expected


def test_a_negative_gap_does_not_subtract():
    """`common` can exceed `prompt` when the live context is longer than the
    request. That is not a refund of prefill time."""
    text = "live kv cache miss live=100 prompt=50 common=80 reason=token-mismatch\n"
    s = ps.summarise(pathlib.Path("x.log"), ps.parse_misses(text))
    assert s.reprefilled_tokens == 0


def test_an_empty_log_summarises_to_zero_rather_than_raising():
    s = ps.summarise(pathlib.Path("x.log"), [])
    assert s.misses == 0
    assert s.best_common == 0
    assert not s.stalled


def test_main_refuses_when_no_log_has_a_miss(tmp_path, caplog):
    quiet = tmp_path / "quiet.log"
    quiet.write_text("ds4-server: nothing to see here\n")
    assert ps.main([str(quiet)]) == 1


def test_main_reports_and_a_missing_file_is_a_warning_not_a_crash(tmp_path, caplog):
    log = tmp_path / "server.log"
    log.write_text(REAL_LINES)
    with caplog.at_level("INFO"):
        assert ps.main([str(log), str(tmp_path / "absent.log")]) == 0
    text = caplog.text
    assert "no such file" in text
    assert "1 logs, 1 with at least one stalled turn" in text


def test_the_time_line_appears_only_with_a_rate(tmp_path, caplog):
    """A duration with no rate beside it is a number with no units."""
    log = tmp_path / "server.log"
    log.write_text(REAL_LINES)
    with caplog.at_level("INFO"):
        ps.main([str(log)])
    assert "minutes of prefill" not in caplog.text
    caplog.clear()
    with caplog.at_level("INFO"):
        ps.main([str(log), "--prefill-tps", "360"])
    assert "minutes of prefill" in caplog.text
