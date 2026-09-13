"""#266: report.py surfaces the prefill-failure retries an MTP arm carried.

The harness stamps `prefill_failures` on each row (`prefill_failures.Probe`).
An MTP arm re-prefills on an HTTP 500 the control arm never sees; the retry
usually succeeds, so the row reads passed and the extra re-prefill hides in a
log. These tests pin that the count reaches the read-out: the per-backend
excision summary and the two-backend comparison both state it, and a row with
no count is treated as unknown, not a measured zero.
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import report


def row(backend, task, trial, *, prefill_failures=None):
    r = {
        "backend": backend,
        "task": task,
        "client": "opencode",
        "trial": trial,
        "passed": True,
        "pytest": "1 passed",
        "wall_seconds": 10.0,
    }
    if prefill_failures is not None:
        r["prefill_failures"] = prefill_failures
    return r


# --- retries() ----------------------------------------------------------


def test_retries_sums_measured_counts() -> None:
    rows = [
        row("b", "t", 1, prefill_failures=2),
        row("b", "t", 2, prefill_failures=1),
        row("b", "t", 3, prefill_failures=0),
    ]
    total, measured = report.retries(rows)
    assert total == 3
    assert measured == 3


def test_retries_ignores_rows_without_a_count() -> None:
    """A row with no server log recorded (field absent) is unknown, not zero."""
    rows = [
        row("b", "t", 1, prefill_failures=2),
        row("b", "t", 2),  # no prefill_failures key
    ]
    total, measured = report.retries(rows)
    assert total == 2
    assert measured == 1


def test_retries_measured_zero_is_not_unknown() -> None:
    """A control arm that sampled 0 is a measured fact: measured counts it."""
    rows = [row("b", "t", i, prefill_failures=0) for i in range(1, 4)]
    total, measured = report.retries(rows)
    assert total == 0
    assert measured == 3


def test_retries_empty_cell() -> None:
    assert report.retries([]) == (0, 0)


# --- render() surfacing -------------------------------------------------


def test_excision_summary_states_the_retry_count() -> None:
    by_cell = {
        ("mtp", "t"): [row("mtp", "t", i, prefill_failures=1) for i in range(1, 4)],
    }
    lines = report.render(by_cell, ["mtp"])
    text = "\n".join(lines)
    assert "prefill-failure retries: 3 across 3/3 trials (#266)" in text


def test_excision_is_silent_when_nothing_measured() -> None:
    """No row carried a count -> no line, rather than a misleading `0`."""
    by_cell = {("mtp", "t"): [row("mtp", "t", i) for i in range(1, 4)]}
    lines = report.render(by_cell, ["mtp"])
    assert not any("prefill-failure retries" in ln for ln in lines)


def test_two_backend_comparison_reports_the_confound() -> None:
    """The MTP-vs-control A/B states each arm's count side by side (#266)."""
    by_cell = {
        ("mtp", "t"): [row("mtp", "t", i, prefill_failures=2) for i in range(1, 4)],
        ("plain", "t"): [row("plain", "t", i, prefill_failures=0) for i in range(1, 4)],
    }
    lines = report.render(by_cell, ["mtp", "plain"])
    text = "\n".join(lines)
    assert "Prefill-failure retries (#266 confound):" in text
    assert "**mtp**: 6 retries (3 trial(s) measured)" in text
    assert "**plain**: 0 retries (3 trial(s) measured)" in text


def test_two_backend_comparison_silent_without_any_count() -> None:
    by_cell = {
        ("mtp", "t"): [row("mtp", "t", i) for i in range(1, 4)],
        ("plain", "t"): [row("plain", "t", i) for i in range(1, 4)],
    }
    lines = report.render(by_cell, ["mtp", "plain"])
    assert not any("#266 confound" in ln for ln in lines)
