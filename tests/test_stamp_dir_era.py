"""Stamp the --dir era onto the #355 orphan rows, once (#393)."""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import dirfix
import stamp_dir_era as sde

MAP = {"6f56a28": "2b734f2"}


def _row(head: str | None = "6f56a28", **env: object) -> str:
    e: dict[str, object] = dict(env)
    if head is not None:
        e["harness_head"] = head
    return json.dumps({"client": "opencode", "task": "t", "env": e})


def test_an_orphan_row_gets_the_stamp_and_nothing_else():
    lines, counts = sde.plan([_row(harness_dirty=False)], MAP)
    got = json.loads(lines[0])
    assert got["env"] == {
        "harness_dirty": False,
        "harness_head": "6f56a28",
        "dir_era": {
            "rule": 1,
            "fix": "7356460",
            "era": "after",
            "basis": "#355 rebased into 2b734f2",
        },
    }
    assert counts["stamped"] == 1
    assert dirfix.era(got, set()) == "after"


def test_a_reachable_row_is_left_for_git():
    line = _row("28b1da6")
    lines, counts = sde.plan([line], MAP)
    assert lines == [line]
    assert counts == {"stamped": 0, "already": 0, "other": 1, "unparsed": 0}


def test_a_row_with_no_harness_head_is_never_guessed():
    line = _row(None)
    assert sde.plan([line], MAP)[0] == [line]


def test_a_stamped_row_is_not_stamped_twice():
    line = _row(dir_era={"rule": 1, "fix": "7356460", "era": "after"})
    lines, counts = sde.plan([line], MAP)
    assert lines == [line]
    assert counts["already"] == 1


def test_records_blank_and_broken_lines_pass_through():
    header = json.dumps({"record": "machine-header", "env": {"harness_head": "x"}})
    lines = [header, "", "{not json"]
    out, counts = sde.plan(lines, MAP)
    assert out == lines
    assert counts["unparsed"] == 1


def test_the_real_map_is_proven_by_this_repository():
    assert sde.unproven(sde.REBASED, ROOT) == []


def test_a_target_before_the_fix_is_refused():
    """The parent of the fix is on main but not after it."""
    got = sde.unproven({"aaaaaaa": f"{dirfix.FIX}~1"}, ROOT)
    assert len(got) == 1
    assert "aaaaaaa" in got[0]


def test_a_target_that_does_not_exist_is_refused():
    assert sde.unproven({"aaaaaaa": "0000000"}, ROOT)


def test_a_dry_run_writes_nothing(tmp_path):
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(_row() + "\n")
    before = ledger.read_text()
    assert sde.main([str(ledger)]) == 0
    assert ledger.read_text() == before


def test_apply_writes_the_stamp(tmp_path):
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(_row() + "\n" + _row("28b1da6") + "\n")
    assert sde.main([str(ledger), "--apply"]) == 0
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert "dir_era" in rows[0]["env"]
    assert "dir_era" not in rows[1]["env"]
