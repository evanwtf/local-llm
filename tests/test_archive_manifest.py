"""Tests for scripts/archive_manifest.py (#999).

The merge rebuilds a model archive's manifest from the copy on the archive
host plus items another copy holds. The failures it must refuse are the ones
a hand-run jq merge made easy: losing the archive's newer header, listing an
item twice, and silently taking one of two different records for one name.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import archive_manifest as am

NOW = "2026-10-10T10:45:00-04:00"


def _item(name: str, sha: str = "aa", size: int = 10) -> dict[str, object]:
    return {"name": name, "kind": "docker-image", "bytes": size, "sha256": sha}


def _manifest(items: list[dict[str, object]], **header: object) -> dict[str, object]:
    return {
        "description": "two DGX Spark nodes",
        "updated": "2026-10-05",
        **header,
        "items": items,
    }


def test_merge_keeps_the_base_header_and_adds_missing_items() -> None:
    base = _manifest([_item("a"), _item("b")], source_host="newer header")
    other = _manifest([_item("a"), _item("b"), _item("c")], source_host="older")
    got = am.merge(base, [other], [], now=NOW)
    assert got["source_host"] == "newer header"
    assert got["description"] == "two DGX Spark nodes"
    assert got["updated"] == NOW
    assert [i["name"] for i in got["items"]] == ["a", "b", "c"]


def test_merge_adds_a_single_entry_file() -> None:
    base = _manifest([_item("a")])
    delta = {"name": "x-delta", "kind": "model-delta", "bytes": 34397}
    got = am.merge(base, [], [delta], now=NOW)
    assert [i["name"] for i in got["items"]] == ["a", "x-delta"]


def test_merge_refuses_two_different_records_for_one_name() -> None:
    base = _manifest([_item("a", sha="aa")])
    other = _manifest([_item("a", sha="bb")])
    with pytest.raises(am.ConflictError, match="'a'"):
        am.merge(base, [other], [], now=NOW)


def test_an_identical_record_in_both_copies_is_listed_once() -> None:
    base = _manifest([_item("a")])
    got = am.merge(base, [_manifest([_item("a")])], [_item("a")], now=NOW)
    assert len(got["items"]) == 1


def test_a_base_that_already_lists_a_name_twice_is_refused() -> None:
    with pytest.raises(am.ConflictError, match="twice"):
        am.merge(_manifest([_item("a"), _item("a")]), [], [], now=NOW)


def test_an_entry_without_a_name_is_refused() -> None:
    with pytest.raises(am.ConflictError, match="no name"):
        am.merge(_manifest([]), [], [{"kind": "x"}], now=NOW)


def test_main_writes_the_file_and_reads_it_back(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "base.json"
    other = tmp_path / "other.json"
    entry = tmp_path / "entry.json"
    out = tmp_path / "out.json"
    base.write_text(json.dumps(_manifest([_item("a")], source_host="keep")))
    other.write_text(json.dumps(_manifest([_item("a"), _item("b")])))
    entry.write_text(json.dumps(_item("c")))
    rc = am.main(
        [
            "merge",
            str(base),
            "--from",
            str(other),
            "--entry",
            str(entry),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    got = json.loads(out.read_text())
    assert got["source_host"] == "keep"
    assert [i["name"] for i in got["items"]] == ["a", "b", "c"]


def test_main_refuses_to_overwrite_an_input(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "base.json"
    base.write_text(json.dumps(_manifest([_item("a")])))
    assert am.main(["merge", str(base), "--out", str(base)]) == 2
    assert json.loads(base.read_text())["updated"] == "2026-10-05"


def test_main_reports_a_conflict_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "base.json"
    other = tmp_path / "other.json"
    out = tmp_path / "out.json"
    base.write_text(json.dumps(_manifest([_item("a", sha="aa")])))
    other.write_text(json.dumps(_manifest([_item("a", sha="bb")])))
    assert am.main(["merge", str(base), "--from", str(other), "--out", str(out)]) == 1
    assert not out.exists()
