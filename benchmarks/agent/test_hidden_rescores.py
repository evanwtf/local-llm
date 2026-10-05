"""#801: report.py reads the re-graded held-out verdicts in rescores/hidden.jsonl.

rescore_hidden.py re-ran the held-out tests on saved patches, append-only, and
keyed each record to its row by the patch's sha256. Until a reader applied
them, every re-graded row still counted as invalid.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import results

ROW: dict[str, Any] = {
    "backend": "b",
    "task": "replay-web-auth-hidden",
    "trial": 1,
    "started": "2026-09-27T00:10:40-0400",
    "solution_sha256": "aa",
    "hidden": {"counts": {"passed": 0, "failed": 0, "errors": 1, "skipped": 0}},
    "hidden_passed": False,
}


def record(**over):
    rec = {
        "row": {k: ROW[k] for k in ("backend", "task", "trial", "started")}
        | {"solution_sha256": "aa"},
        "hidden_counts": {"passed": 22, "failed": 1, "errors": 0, "skipped": 0},
        "hidden_passed": False,
        "rescored_at": "2026-09-30T10:13:49-04:00",
        "reason": "#801",
        "was": "1 error",
    }
    rec.update(over)
    return rec


def write(tmp_path, *recs):
    path = tmp_path / "hidden.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return path


def test_a_matching_record_replaces_the_hidden_verdict(tmp_path):
    rows, n = results.apply_hidden_rescores([ROW], write(tmp_path, record()))
    assert n == 1
    assert rows[0]["hidden"]["counts"]["passed"] == 22
    assert rows[0]["hidden_passed"] is False
    assert rows[0]["hidden"]["rescored"]["reason"] == "#801"
    assert ROW["hidden"]["counts"]["passed"] == 0, "the input row is not mutated"


def test_a_different_patch_is_not_applied(tmp_path):
    """The sha256 is the guard: an overwritten patch file graded another trial."""
    rec = record(row=record()["row"] | {"solution_sha256": "bb"})
    rows, n = results.apply_hidden_rescores([ROW], write(tmp_path, rec))
    assert n == 0
    assert rows[0] is ROW


def test_the_newest_record_wins(tmp_path):
    old = record()
    new = record(
        hidden_counts={"passed": 23, "failed": 0, "errors": 0, "skipped": 0},
        hidden_passed=True,
        rescored_at="2026-10-01T09:00:00-04:00",
    )
    rows, _ = results.apply_hidden_rescores([ROW], write(tmp_path, new, old))
    assert rows[0]["hidden_passed"] is True


def test_a_row_without_a_patch_hash_is_left_alone(tmp_path):
    bare = {k: v for k, v in ROW.items() if k != "solution_sha256"}
    rows, n = results.apply_hidden_rescores([bare], write(tmp_path, record()))
    assert (rows, n) == ([bare], 0)


def test_no_file_is_no_rescores(tmp_path):
    rows, n = results.apply_hidden_rescores([ROW], tmp_path / "absent.jsonl")
    assert (rows, n) == ([ROW], 0)


def test_the_committed_file_applies_to_the_mac_ledger():
    """Anchor: 6 of the M5 Max's 12 web-auth rows were recoverable (#868)."""
    repo = pathlib.Path(results.__file__).resolve().parent.parent.parent
    mac = results.load(
        repo / "hardware" / "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A" / "results.jsonl"
    )
    _rows, n = results.apply_hidden_rescores(mac)
    assert n == 6


def test_an_error_record_neither_crashes_nor_supersedes(tmp_path):
    """rescore_hidden.py once appended `{"error": ...}` when `git apply` or
    `uv sync` failed. Read as a verdict it raised KeyError on hidden_counts,
    and as the newest record it would have replaced a valid re-grade."""
    good = record()
    failed = {
        "row": record()["row"],
        "error": "git apply failed: corrupt patch",
        "rescored_at": "2026-10-01T09:00:00-04:00",
        "reason": "#801",
        "was": "1 error",
    }
    rows, n = results.apply_hidden_rescores([ROW], write(tmp_path, good, failed))
    assert n == 1
    assert rows[0]["hidden"]["counts"]["passed"] == 22
