"""Tests for the secondary-measurement report."""
from __future__ import annotations

import quality


def _row(**kw):
    base = {"task": "t", "backend": "b", "client": "claude", "passed": True,
            "excluded": False, "control_fails_as_expected": True}
    base.update(kw)
    return base


def test_gate_deltas_are_averaged_only_over_rows_that_measured_them():
    """An absent gate is not a zero. Averaging it in would report cleanliness."""
    rows = [_row(gates_delta={"ruff": 2, "mypy": 0}), _row(gates_delta={}),
            _row(gates_delta={"ruff": 4, "mypy": 2})]
    got = quality.summarise(rows)[("t", "b", "claude")]
    assert got["ruff"] == 3.0          # (2 + 4) / 2, not / 3
    assert got["mypy"] == 1.0
    assert got["gated"] == 2


def test_a_cell_with_no_gates_at_all_reports_none_not_zero():
    got = quality.summarise([_row(gates_delta={})])[("t", "b", "claude")]
    assert got["ruff"] is None and got["gated"] == 0


def test_verbatim_counts_only_decided_rows():
    """None means unreadable, and must not count as "not recalled"."""
    rows = [_row(restored_verbatim=True), _row(restored_verbatim=False),
            _row(restored_verbatim=None)]
    got = quality.summarise(rows)[("t", "b", "claude")]
    assert got["verbatim"] == 1 and got["verbatim_of"] == 2


def test_distinct_solutions_measures_determinism():
    """Same hash twice at temperature 1.0 is worth knowing (#26)."""
    rows = [_row(solution_sha256="aa"), _row(solution_sha256="aa"),
            _row(solution_sha256="bb")]
    got = quality.summarise(rows)[("t", "b", "claude")]
    assert got["distinct"] == 2 and got["hashed"] == 3


def test_a_failing_trial_still_contributes_its_gate_delta():
    """Failed code is exactly where a quality signal is most interesting."""
    rows = [_row(passed=False, gates_delta={"ruff": 9, "mypy": 0})]
    got = quality.summarise(rows)[("t", "b", "claude")]
    assert got["ruff"] == 9.0


def test_cells_are_split_by_client_not_just_backend():
    rows = [_row(client="claude"), _row(client="codex")]
    assert len(quality.summarise(rows)) == 2
