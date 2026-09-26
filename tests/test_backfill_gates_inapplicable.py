"""Mark Python-gate results on Swift rows as not measured (#46)."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import backfill_gates_inapplicable as bf


def _swift(**kw):
    return {"task": "swift-x", "target_repo": "~/git/monitor", **kw}


def test_a_swift_row_with_a_ruff_zero_is_marked():
    lines, counts = bf.plan([json.dumps(_swift(gates_delta={"ruff": 0}))])
    assert json.loads(lines[0])["gates_inapplicable"] is True
    assert counts["marked"] == 1


def test_the_recorded_gate_values_are_kept():
    """Evidence is annotated, never rewritten."""
    row = _swift(gates_before={"ruff": 0}, gates_delta={"ruff": 0})
    got = json.loads(bf.plan([json.dumps(row)])[0][0])
    assert got["gates_delta"] == {"ruff": 0}
    assert got["gates_before"] == {"ruff": 0}


def test_a_python_row_is_never_marked():
    row = {
        "task": "t",
        "target_repo": "~/git/gmail-archive",
        "gates_delta": {"ruff": 0},
    }
    lines, counts = bf.plan([json.dumps(row)])
    assert "gates_inapplicable" not in json.loads(lines[0])
    assert counts["marked"] == 0


def test_a_swift_row_with_no_gate_result_is_left_alone():
    """Nothing claims a clean gate, so there is nothing to correct."""
    lines, counts = bf.plan([json.dumps(_swift(gates_delta=None))])
    assert "gates_inapplicable" not in json.loads(lines[0])
    assert counts["no_gate"] == 1


def test_a_second_run_changes_nothing():
    once, _ = bf.plan([json.dumps(_swift(gates_delta={"ruff": 0}))])
    twice, counts = bf.plan(once)
    assert twice == once
    assert counts["marked"] == 0 and counts["already"] == 1


def test_an_unparseable_line_passes_through():
    lines, counts = bf.plan(["not json", ""])
    assert lines == ["not json", ""]
    assert counts["unparsed"] == 1
