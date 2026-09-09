"""The prefix audit must not flatter a stalled cache (#64)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import kv_prefix_audit as audit

LINE = "0809 10:50:36 ds4-server: live kv cache miss live={l} prompt={p} common={c} reason={r}"


def log(rows, reason="token-mismatch"):
    return "\n".join(LINE.format(l=l, p=p, c=c, r=reason) for l, p, c in rows)


def test_parses_the_four_fields():
    got = audit.parse(log([(432, 332, 257)]))
    assert got == [
        audit.Miss(live=432, prompt=332, common=257, reason="token-mismatch")
    ]


def test_ignores_lines_that_are_not_misses():
    assert audit.parse("ds4-server: loaded model\nnothing here\n") == []


def test_wasted_tokens_is_prompt_minus_common():
    assert (
        audit.wasted_tokens(audit.parse(log([(0, 1000, 400), (0, 2000, 400)]))) == 2200
    )


def test_a_common_above_prompt_costs_nothing_rather_than_negative():
    """One odd line must not make a log look better than it is."""
    assert audit.wasted_tokens(audit.parse(log([(0, 100, 500)]))) == 0


def test_a_pinned_prefix_is_a_stall_even_when_prompt_falls():
    """The case real data exposed: common pinned, prompt drifting DOWN, and
    ~830 tokens re-prefilled every turn. Requiring a rising prompt hid it."""
    misses = audit.parse(
        log([(11415, 11375, 10534), (11427, 11360, 10534), (11413, 11359, 10534)])
    )
    runs = audit.stalled_runs(misses)
    assert len(runs) == 1
    assert len(runs[0]) == 3


def test_a_climbing_prefix_is_not_a_stall():
    """A cold cache warming up must not be reported as a defect."""
    misses = audit.parse(log([(0, 1000, 200), (0, 2000, 900), (0, 3000, 1900)]))
    assert audit.stalled_runs(misses) == []


def test_two_misses_sharing_a_common_are_not_a_pattern():
    misses = audit.parse(log([(0, 1000, 400), (0, 1100, 400)]))
    assert audit.stalled_runs(misses) == []


def test_the_issue_64_trace_is_detected():
    """The three lines quoted on #64, verbatim."""
    misses = audit.parse(
        log([(47958, 25468, 20398), (66171, 65794, 20398), (66238, 67069, 20398)])
    )
    runs = audit.stalled_runs(misses)
    assert len(runs) == 1
    assert runs[0][0].common == 20398


def test_summarise_names_the_cost_of_each_stall(tmp_path):
    p = tmp_path / "s.log"
    p.write_text(log([(0, 5000, 1000), (0, 5000, 1000), (0, 5000, 1000)]))
    text = audit.summarise(p, audit.parse(p.read_text()), 360.0)
    assert "STALL" in text
    assert "12,000 tokens re-prefilled" in text


def test_an_empty_log_says_so(tmp_path):
    p = tmp_path / "empty.log"
    p.write_text("")
    assert "no cache-miss lines" in audit.summarise(p, [], 360.0)
