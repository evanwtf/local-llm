"""#138: a failure at the token cap is a truncation, not a wrong answer."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import eval_trace

HEAD = """# ds4-eval trace
model: /models/thing.gguf
ctx: 8192
max_tokens: 2500
questions: 3
temperature: 0
"""


def case(n, source, status, generated, expected="B", picked="B"):
    return f"""
===== CASE {n}/3 {source} =====
source: {source}
status: {status}
picked: {picked}
expected: {expected}
prompt_tokens: 100
generated_tokens: {generated}
elapsed_sec: 10.0
"""


def write(tmp_path, *cases):
    p = tmp_path / "t.trace"
    p.write_text(HEAD + "".join(cases))
    return p


def test_it_reads_the_cases_and_the_cap(tmp_path):
    got = eval_trace.read(write(tmp_path, case(1, "A/x", "PASSED", 500)))
    assert got.max_tokens == 2500
    assert got.model == "/models/thing.gguf"
    assert len(got.cases) == 1
    assert got.cases[0].generated == 500


def test_a_failure_at_the_cap_is_reported_as_truncated_not_failed(tmp_path):
    got = eval_trace.read(
        write(tmp_path, case(1, "A/x", "FAILED", 2500, expected="J", picked="H"))
    )
    assert got.cases[0].truncated is True
    assert got.truncated == 1
    assert got.failed == 0, "a truncation must not be counted as a wrong answer"


def test_a_failure_below_the_cap_is_a_real_failure(tmp_path):
    got = eval_trace.read(
        write(tmp_path, case(1, "A/x", "FAILED", 900, expected="J", picked="H"))
    )
    assert got.cases[0].truncated is False
    assert got.failed == 1 and got.truncated == 0


def test_a_pass_at_the_cap_is_not_truncated(tmp_path):
    """It answered. Hitting the cap afterwards says nothing."""
    got = eval_trace.read(write(tmp_path, case(1, "A/x", "PASSED", 2500)))
    assert got.cases[0].truncated is False
    assert got.passed == 1


def test_tokens_to_answer_counts_only_untruncated_cases(tmp_path):
    """A truncated case has no tokens-to-answer -- it never reached one."""
    got = eval_trace.read(
        write(
            tmp_path,
            case(1, "A/x", "PASSED", 300),
            case(2, "A/y", "FAILED", 2500, expected="J", picked="H"),
            case(3, "A/z", "PASSED", 700),
        )
    )
    assert got.tokens_to_answer == 1000


def test_compare_pairs_cases_by_source_id(tmp_path):
    d1 = tmp_path / "d1"
    d1.mkdir()
    d2 = tmp_path / "d2"
    d2.mkdir()
    left = eval_trace.read(write(d1, case(1, "A/x", "PASSED", 1000)))
    right = eval_trace.read(write(d2, case(1, "A/x", "PASSED", 1200)))
    pairs = eval_trace.compare(left, right)
    assert len(pairs) == 1
    assert pairs[0].ratio == 1.2


def test_compare_skips_a_case_only_one_side_ran(tmp_path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    left = eval_trace.read(write(d1, case(1, "A/x", "PASSED", 1000)))
    right = eval_trace.read(write(d2, case(1, "A/other", "PASSED", 1200)))
    assert eval_trace.compare(left, right) == []


def test_compare_excludes_a_truncated_case_from_the_token_ratio(tmp_path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    left = eval_trace.read(write(d1, case(1, "A/x", "PASSED", 1000)))
    right = eval_trace.read(
        write(d2, case(1, "A/x", "FAILED", 2500, expected="J", picked="H"))
    )
    assert eval_trace.compare(left, right) == []


def test_a_spread_wider_than_the_effect_is_flagged(tmp_path, caplog):
    """Five cases spanning 0.72-1.35 cannot establish a 3% median."""
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    left = write(d1, case(1, "A/x", "PASSED", 1000), case(2, "A/y", "PASSED", 1000))
    right = write(d2, case(1, "A/x", "PASSED", 700), case(2, "A/y", "PASSED", 1350))
    with caplog.at_level("WARNING", logger="eval_trace"):
        eval_trace.main([str(left), str(right)])
    assert "do not establish a direction" in caplog.text
