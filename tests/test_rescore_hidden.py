"""The re-grade must only ever grade a row with that row's own patch.

Patch file names repeat across batches, so a later batch overwrites an
earlier one's file. Grading a row with another row's patch would attach a
verdict to work the row never did; the sha256 check is the whole defense.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import rescore_hidden as rh


def row(patch: pathlib.Path | None, sha: str | None, **extra: object) -> dict:
    return {
        "task": "replay-web-auth-hidden",
        "backend": "b",
        "trial": 1,
        "started": "2026-09-27T00:10:40-04:00",
        "hidden": {"pytest": "1 error"},
        "solution_patch": str(patch) if patch else None,
        "solution_sha256": sha,
    } | extra


def test_the_rows_own_patch_matches(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "x.patch"
    p.write_text("diff\n")
    assert rh.match(row(p, hashlib.sha256(b"diff\n").hexdigest())) == p


def test_an_overwritten_patch_does_not_match(tmp_path: pathlib.Path) -> None:
    """A later batch wrote a different diff under the same name."""
    p = tmp_path / "x.patch"
    p.write_text("a later batch's diff\n")
    assert rh.match(row(p, hashlib.sha256(b"diff\n").hexdigest())) is None


def test_a_missing_patch_or_hash_does_not_match(tmp_path: pathlib.Path) -> None:
    assert rh.match(row(tmp_path / "gone.patch", "0" * 64)) is None
    assert rh.match(row(None, "0" * 64)) is None
    p = tmp_path / "x.patch"
    p.write_text("diff\n")
    assert rh.match(row(p, None)) is None


def test_rows_for_keeps_only_the_task_with_a_hidden_verdict(
    tmp_path: pathlib.Path,
) -> None:
    ledger = tmp_path / "results.jsonl"
    rows = [
        row(None, None),
        row(None, None, task="other"),
        row(None, None, hidden=None),
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    assert len(rh.rows_for(ledger, "replay-web-auth-hidden")) == 1


def test_the_key_names_the_row_and_its_patch_hash() -> None:
    got = rh.key(row(None, "abc"))
    assert got == {
        "task": "replay-web-auth-hidden",
        "backend": "b",
        "trial": 1,
        "started": "2026-09-27T00:10:40-04:00",
        "solution_sha256": "abc",
    }
