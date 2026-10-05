"""#803: report.py reports the held-out (hidden) test verdict, not only the visible one.

A `--replay-hard` row carries two verdicts. `passed` is the visible suite; the
held-out run is `hidden.counts` ({passed, failed, skipped, errors}) and
`hidden_passed`. Two M5 Max read-outs (#749, #762) published visible pass rates
for a hard-set run because report.py never read the hidden half.

These tests pin absolute values, not relationships: a relationship-only test
passes under a uniformly wrong convention. The rule, agreed with the DGX lane
on #803:

- tests passed / total counts `passed` and `failed` only;
- a collection error (`errors` > 0 with nothing collected) is INVALID, never
  0/N -- #801 is a held-out file that cannot import, which measures the task;
- a whole-task hidden pass is `results.hidden_verdict()`, which carries the
  same guards as the visible verdict (a trial that edited tests fails).
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import report


def row(task, trial, *, counts=None, hidden_passed=None, touched=False, timeout=False):
    r = {
        "backend": "b",
        "task": task,
        "client": "opencode",
        "trial": trial,
        "passed": not timeout,
        "pytest": "1 passed",
        "wall_seconds": None if timeout else 100.0,
        "touched_tests": touched,
    }
    if timeout:
        r["timeout_reason"] = "wall-clock"
    r["hidden"] = {"ref": "abc", "tests": ["t"], "hidden": []}
    if counts is not None:
        r["hidden"]["counts"] = counts
    if hidden_passed is not None:
        r["hidden_passed"] = hidden_passed
    return r


def counts(passed=0, failed=0, errors=0, skipped=0):
    return {"passed": passed, "failed": failed, "skipped": skipped, "errors": errors}


def test_tests_passed_counts_passed_over_passed_plus_failed() -> None:
    got = report.hidden_summary([row("t", 1, counts=counts(passed=12, failed=5))])
    assert got is not None
    assert (got.tests_passed, got.tests_total) == (12, 17)
    assert got.invalid == 0


def test_a_collection_error_is_invalid_not_zero_of_n() -> None:
    got = report.hidden_summary([row("t", 1, counts=counts(errors=1))])
    assert got is not None
    assert (got.tests_passed, got.tests_total) == (0, 0)
    assert got.invalid == 1
    assert got.whole == 0


def test_a_timeout_has_no_hidden_result() -> None:
    got = report.hidden_summary([row("t", 1, timeout=True)])
    assert got is not None
    assert got.no_result == 1
    assert (got.tests_passed, got.tests_total, got.invalid) == (0, 0, 0)


def test_whole_task_pass_uses_hidden_verdict_and_its_guards() -> None:
    rows = [
        row("t", 1, counts=counts(passed=8), hidden_passed=True),
        # all hidden tests passed, but the trial edited tests: not a pass
        row("t", 2, counts=counts(passed=8), hidden_passed=True, touched=True),
        row("t", 3, counts=counts(passed=7, failed=1), hidden_passed=False),
    ]
    got = report.hidden_summary(rows)
    assert got is not None
    assert got.whole == 1
    assert got.rows == 3
    assert (got.tests_passed, got.tests_total) == (23, 24)


def test_rows_without_a_hidden_record_are_not_counted() -> None:
    plain = row("t", 1, counts=counts(passed=3))
    del plain["hidden"]
    assert report.hidden_summary([plain]) is None


def test_render_prints_the_hidden_table_with_absolute_counts() -> None:
    by_cell = {
        ("b", "replay-x-hidden"): [
            row("replay-x-hidden", 1, counts=counts(passed=12, failed=5)),
            row("replay-x-hidden", 2, counts=counts(errors=1)),
            row("replay-x-hidden", 3, counts=counts(passed=17), hidden_passed=True),
        ]
    }
    out = "\n".join(report.render(by_cell, ["b"]))
    assert "Hidden tests (#726)" in out
    assert "| `replay-x-hidden` | 1/3 whole · 29/34 tests · 1 invalid |" in out
    assert "**b** hidden: 1/3 whole-task passes, 29/34 tests (85.3%), 1 invalid" in out


def test_render_is_silent_when_no_row_holds_tests_out() -> None:
    plain = row("t", 1)
    del plain["hidden"]
    out = "\n".join(report.render({("b", "t"): [plain]}, ["b"]))
    assert "Hidden" not in out
