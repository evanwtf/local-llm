"""The cross-machine ledger validator, scripts/validate_ledgers.py (#394).

CI runs on a Linux box that owns neither the M5 nor the DGX ledger, so the
runner-gated invariant tests never check the committed ledgers there. This
validator does, regardless of runner. These tests drive `validate_ledger`
directly with synthetic ledgers under a REAL registered machine directory
(so `machines.by_directory` resolves) and one fixed `after` set, checking each
invariant fires and a clean ledger passes.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import validate_ledgers as vl

# A registered DGX directory (arch aarch64) so by_directory resolves in tmp.
DGX_DIR = "Cortex-X925-128GB-GB10"
AFTER = {"abc1234"}  # the "post-fix" commit set for is_pre_dir


def _row(**kw):
    row = {
        "task": "t",
        "backend": "b",
        "client": "opencode",
        "trial": 1,
        "started": "2026-09-14T00:00:00",
        "env": {"harness_head": "abc1234", "arch": "aarch64"},
    }
    row.update(kw)
    return row


def _write(tmp_path, rows, directory=DGX_DIR):
    d = tmp_path / directory
    d.mkdir()
    p = d / "results.jsonl"
    p.write_text(
        "\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n"
    )
    return p


def test_clean_ledger_holds(tmp_path):
    p = _write(
        tmp_path,
        [_row(started="2026-09-14T00:00:01"), _row(started="2026-09-14T00:00:02")],
    )
    assert vl.validate_ledger(p, AFTER, set()) == []


def test_unregistered_directory_is_flagged(tmp_path):
    p = _write(tmp_path, [_row()], directory="Not-A-Machine-Dir")
    assert any("unregistered" in x for x in vl.validate_ledger(p, AFTER, set()))


def test_malformed_json_is_a_violation_not_a_crash(tmp_path):
    p = tmp_path / DGX_DIR
    p.mkdir()
    f = p / "results.jsonl"
    f.write_text(json.dumps(_row()) + "\n{ this is not json\n")
    assert any("malformed JSON" in x for x in vl.validate_ledger(f, AFTER, set()))


def test_duplicate_rows_are_flagged(tmp_path):
    r = _row()
    p = _write(tmp_path, [r, dict(r)])  # identical key
    assert any("duplicate" in x for x in vl.validate_ledger(p, AFTER, set()))


def test_pre_dir_straggler_is_flagged(tmp_path):
    # OpenCode row whose head is NOT in the post-fix set: it belongs in the
    # archive, not the active ledger -- the 2026-09-14 red-main shape.
    straggler = _row(env={"harness_head": "0000000", "arch": "aarch64"})
    p = _write(tmp_path, [straggler])
    assert any(
        "belongs in the archive" in x for x in vl.validate_ledger(p, AFTER, set())
    )


def test_non_opencode_before_row_is_not_a_straggler(tmp_path):
    # is_pre_dir only fires for OpenCode; a codex row with an old head is fine.
    r = _row(client="codex", env={"harness_head": "0000000", "arch": "aarch64"})
    p = _write(tmp_path, [r])
    assert not any(
        "belongs in the archive" in x for x in vl.validate_ledger(p, AFTER, set())
    )


def test_archive_overlap_is_flagged(tmp_path):
    r = _row()
    p = _write(tmp_path, [r])
    archive_keys = {vl._key(r)}
    assert any("overlap" in x for x in vl.validate_ledger(p, AFTER, archive_keys))


def test_arch_mismatch_is_cross_machine_contamination(tmp_path):
    # An arm64 (Mac) row in the DGX (aarch64) ledger.
    r = _row(env={"harness_head": "abc1234", "arch": "arm64"})
    p = _write(tmp_path, [r])
    assert any("cross-machine" in x for x in vl.validate_ledger(p, AFTER, set()))


def test_missing_arch_is_not_punished(tmp_path):
    # Pre-provenance rows carry no arch; silence must not be a violation.
    r = _row(env={"harness_head": "abc1234"})
    p = _write(tmp_path, [r])
    assert not any("cross-machine" in x for x in vl.validate_ledger(p, AFTER, set()))


def test_schema_valid_false_is_flagged(tmp_path):
    r = _row(schema_valid=False)
    p = _write(tmp_path, [r])
    assert any("schema_valid=false" in x for x in vl.validate_ledger(p, AFTER, set()))


def test_the_real_committed_ledgers_hold():
    """The live invariant: every committed hardware/*/results.jsonl passes. This
    is the check CI was blind to -- here it runs regardless of the runner."""
    assert vl.main([]) == 0
