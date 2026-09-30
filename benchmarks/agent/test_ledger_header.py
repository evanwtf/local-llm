"""#305, step 1: the reader learns the machine-header record first.

A header line (`{"record": "machine-header", ...}`) is a record about the file,
not a trial. Before this, `normalize()` turned it into a counted, failed trial
attributed to `claude`, so the first header written would have cut one pass
from every rate over that file. The writer must not ship before this reader.
"""

from __future__ import annotations

import json
import pathlib
import sys

import provenance
import results

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import validate_ledgers

HEADER_1 = {
    "record": "machine-header",
    "v": 1,
    "at": "2026-09-30T14:00:00-0400",
    "machine": "M5-Max-128GB",
    "facts": {"macos": "27.0", "iogpu_wired_limit_mb": 114688},
}
HEADER_2 = HEADER_1 | {"at": "2026-10-02T09:00:00-0400", "facts": {"macos": "27.1"}}


def trial(n: int, passed: bool = True) -> dict:
    return {
        "schema_version": 2,
        "task": "t",
        "backend": "b",
        "client": "opencode",
        "trial": n,
        "started": f"2026-09-30T14:0{n}:00-0400",
        "finished": f"2026-09-30T14:0{n}:30-0400",
        "model": "m",
        "context_tokens": 1,
        "env": {},
        "excluded": False,
        "exclusion_reason": None,
        "passed": passed,
        "wall_seconds": 1.0,
        "pytest": "1 passed",
        "touched_tests": False,
        "source_repo_intact": True,
    }


def ledger(tmp_path, *objs) -> pathlib.Path:
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(o) + "\n" for o in objs))
    return path


def test_a_header_is_not_a_trial(tmp_path):
    path = ledger(tmp_path, HEADER_1, trial(1), trial(2))
    assert len(results.load(path)) == 2
    assert all(results.verdict(r) for r in results.trials(path))


def test_the_header_in_force_at_a_row(tmp_path):
    path = ledger(tmp_path, HEADER_1, trial(1), trial(2), HEADER_2, trial(3))
    assert results.header(path, 0) == HEADER_1
    assert results.header(path, 1) == HEADER_1
    assert results.header(path, 2) == HEADER_2
    assert results.header(path) == HEADER_2


def test_rows_before_any_header_are_unknown_not_absent(tmp_path):
    path = ledger(tmp_path, trial(1), HEADER_1, trial(2))
    assert results.header(path, 0) is None
    assert results.header(path, 1) == HEADER_1


def test_a_file_with_no_header_has_none(tmp_path):
    path = ledger(tmp_path, trial(1))
    assert results.header(path) is None
    assert results.header(tmp_path / "absent.jsonl") is None


def test_the_fingerprint_counts_trials_not_header_lines(tmp_path):
    with_header = ledger(tmp_path, HEADER_1, trial(1), trial(2))
    assert provenance.fingerprint(with_header).startswith("2 rows, ")


def test_the_validator_does_not_read_a_header_as_a_duplicate_row(tmp_path):
    path = ledger(tmp_path, HEADER_1, trial(1), HEADER_2, trial(2))
    rows, errs = validate_ledgers._parse(path)
    assert errs == []
    assert [r["trial"] for r in rows] == [1, 2]


def test_a_malformed_header_is_a_violation(tmp_path):
    bad = {"record": "machine-header", "v": 1, "facts": {"nested": {"no": 1}}}
    path = ledger(tmp_path, bad, trial(1))
    _rows, errs = validate_ledgers._parse(path)
    assert any("line 1" in e and "machine-header" in e for e in errs), errs
