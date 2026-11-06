"""#1257: report.py says which recipe commit a backend's rows ran, and a pin.

A pinned winner is a deliberate old build. The read-out names the pin and its
reason beside the results, so a reader can tell it from a forgotten bump.
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
sys.path.insert(0, str(HERE / "benchmarks" / "agent"))

import report

PINNED = {
    "commit": "cc59c36",
    "state": "pinned",
    "upstream": "f8f20b3 (2 commits ahead of HEAD)",
    "pin_reason": "the two-Spark pick (#1160) is v1.1",
}


def row(backend, trial, recipe=None):
    r = {
        "backend": backend,
        "task": "t",
        "client": "opencode",
        "trial": trial,
        "passed": True,
        "pytest": "1 passed",
        "wall_seconds": 10.0,
        "env": {},
    }
    if recipe is not None:
        r["env"]["recipe"] = recipe
    return r


def test_recipes_counts_rows_per_commit_and_state() -> None:
    rows = [row("b", 1, PINNED), row("b", 2, PINNED), row("b", 3)]
    got = report.recipes(rows)
    assert got == [("cc59c36", "pinned", PINNED["upstream"], PINNED["pin_reason"], 2)]


def test_the_read_out_names_a_pin_and_its_reason() -> None:
    by_cell = {("b", "t"): [row("b", 1, PINNED), row("b", 2, PINNED)]}
    text = "\n".join(report.render(by_cell, ["b"]))
    assert (
        "**b** recipe: cc59c36 PINNED in 2 row(s), upstream f8f20b3 "
        "(2 commits ahead of HEAD) -- the two-Spark pick (#1160) is v1.1 (#1257)"
    ) in text


def test_a_current_recipe_is_named_without_a_reason() -> None:
    rec = {"commit": "94ae731", "state": "current", "upstream": "94ae731"}
    by_cell = {("b", "t"): [row("b", 1, rec)]}
    text = "\n".join(report.render(by_cell, ["b"]))
    assert "**b** recipe: 94ae731 current in 1 row(s)" in text
    assert "PINNED" not in text


def test_rows_without_a_recipe_say_nothing() -> None:
    # Rows before 2026-10-26 carry no env.recipe; silence, not "unknown".
    by_cell = {("b", "t"): [row("b", 1)]}
    assert "recipe:" not in "\n".join(report.render(by_cell, ["b"]))
