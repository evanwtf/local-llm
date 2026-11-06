"""A downloaded or copied checkpoint is checked against the Hub's own hashes.

Weights move twice before a run: one download on the head, then a copy to the
worker over the fabric. A truncated or corrupt shard loads, or half-loads, and
then fails as a model problem hours later. These tests pin the comparison: LFS
files by sha256 and size, small files by git blob id, and every way a directory
can disagree with the revision.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import verify_hf_weights as vh


def lfs_entry(path: str, data: bytes) -> dict[str, object]:
    return {
        "type": "file",
        "path": path,
        "size": len(data),
        "oid": "0" * 40,
        "lfs": {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)},
    }


def blob_entry(path: str, data: bytes) -> dict[str, object]:
    return {
        "type": "file",
        "path": path,
        "size": len(data),
        "oid": vh.git_blob_sha1(data),
    }


def test_git_blob_sha1_matches_git_for_known_values() -> None:
    # `git hash-object` of an empty file and of "hello\n".
    assert vh.git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert vh.git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_parse_tree_keeps_files_and_prefers_the_lfs_hash() -> None:
    tree = [
        {"type": "directory", "path": "sub", "oid": "a" * 40, "size": 0},
        lfs_entry("model-00001.safetensors", b"weights"),
        blob_entry("config.json", b"{}"),
    ]
    entries = vh.parse_tree(tree)
    assert [e.path for e in entries] == ["config.json", "model-00001.safetensors"]
    lfs = entries[1]
    assert lfs.sha256 == hashlib.sha256(b"weights").hexdigest()
    assert lfs.git_oid is None
    assert lfs.size == 7
    assert entries[0].sha256 is None
    assert entries[0].git_oid == vh.git_blob_sha1(b"{}")


def test_a_matching_directory_passes(tmp_path: pathlib.Path) -> None:
    (tmp_path / "model-00001.safetensors").write_bytes(b"weights")
    (tmp_path / "config.json").write_bytes(b"{}")
    entries = vh.parse_tree(
        [
            lfs_entry("model-00001.safetensors", b"weights"),
            blob_entry("config.json", b"{}"),
        ]
    )
    report = vh.verify(tmp_path, entries)
    assert report.ok
    assert report.checked == 2
    assert report.problems == []


def test_a_corrupt_shard_of_the_right_size_fails(tmp_path: pathlib.Path) -> None:
    (tmp_path / "model-00001.safetensors").write_bytes(b"weighTs")
    entries = vh.parse_tree([lfs_entry("model-00001.safetensors", b"weights")])
    report = vh.verify(tmp_path, entries)
    assert not report.ok
    want = (
        "model-00001.safetensors: sha256 differs: expected"
        f" {hashlib.sha256(b'weights').hexdigest()},"
        f" actual {hashlib.sha256(b'weighTs').hexdigest()}"
    )
    assert report.problems == [want]


def test_a_truncated_shard_fails_on_size_without_hashing(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "model-00001.safetensors").write_bytes(b"weig")
    entries = vh.parse_tree([lfs_entry("model-00001.safetensors", b"weights")])
    report = vh.verify(tmp_path, entries)
    assert report.problems == ["model-00001.safetensors: size 4, expected 7"]


def test_a_missing_file_fails(tmp_path: pathlib.Path) -> None:
    entries = vh.parse_tree([blob_entry("config.json", b"{}")])
    report = vh.verify(tmp_path, entries)
    assert report.problems == ["config.json: missing (expected 2 bytes)"]


def test_a_changed_small_file_fails_on_its_git_blob_id(tmp_path: pathlib.Path) -> None:
    (tmp_path / "config.json").write_bytes(b"[]")
    entries = vh.parse_tree([blob_entry("config.json", b"{}")])
    report = vh.verify(tmp_path, entries)
    # `git hash-object` of "{}" and of "[]".
    want = (
        "config.json: git blob id differs: expected"
        " 9e26dfeeb6e641a33dae4961196235bdb965b21b,"
        " actual 0637a088a01e8ddab3bf3fa98dbe804cbde1a0dc"
    )
    assert report.problems == [want]


def test_skipped_paths_are_not_required(tmp_path: pathlib.Path) -> None:
    # `hf download --local-dir` does not write .gitattributes.
    entries = vh.parse_tree([blob_entry(".gitattributes", b"*.bin lfs\n")])
    report = vh.verify(tmp_path, entries, skip={".gitattributes"})
    assert report.ok
    assert report.checked == 0


def test_the_manifest_lists_lfs_files_in_sha256sum_format() -> None:
    entries = vh.parse_tree(
        [
            lfs_entry("b.safetensors", b"b"),
            lfs_entry("a.safetensors", b"a"),
            blob_entry("c.json", b"c"),
        ]
    )
    lines = vh.sha256sum_manifest(entries).splitlines()
    assert lines == [
        f"{hashlib.sha256(b'a').hexdigest()}  a.safetensors",
        f"{hashlib.sha256(b'b').hexdigest()}  b.safetensors",
    ]


# -- every page of the tree, and every failing file named (#1344) ---------------

NEXT = (
    "https://huggingface.co/api/models/o/r/tree/abc"
    "?expand=true&recursive=true&limit=50&cursor=Zm9v"
)


class FakeResponse(io.BytesIO):
    """What urlopen returns: a body plus headers, usable as a context manager."""

    def __init__(self, body: list[dict[str, Any]], link: str | None) -> None:
        super().__init__(json.dumps(body).encode())
        self.headers = {"Link": link} if link else {}


def test_the_tree_follows_every_page() -> None:
    """The Hub pages a recursive, expanded tree at 50 entries and names the
    next page in a Link header. Page 1 alone held 45 of 82 files (#1344)."""
    page1 = [
        {"type": "directory", "path": "sub", "oid": "a" * 40, "size": 0},
        lfs_entry("model-00001.safetensors", b"weights"),
    ]
    page2 = [lfs_entry("sub/model-00002.safetensors", b"more")]
    urls: list[str] = []

    def opener(url: str, timeout: float) -> FakeResponse:
        urls.append(url)
        if len(urls) == 1:
            return FakeResponse(page1, f'<{NEXT}>; rel="next"')
        return FakeResponse(page2, None)

    tree = vh.fetch_tree("o/r", "abc", opener=opener)
    assert urls == [
        "https://huggingface.co/api/models/o/r/tree/abc?recursive=1&expand=1",
        NEXT,
    ]
    paths = [e.path for e in vh.parse_tree(tree)]
    assert paths == ["model-00001.safetensors", "sub/model-00002.safetensors"]


def test_a_next_link_off_the_hub_is_refused() -> None:
    def opener(url: str, timeout: float) -> FakeResponse:
        return FakeResponse([], '<https://example.com/x>; rel="next"')

    with pytest.raises(ValueError, match="example.com"):
        vh.fetch_tree("o/r", "abc", opener=opener)


def test_next_link_parses_only_the_next_relation() -> None:
    assert vh.next_link(None) is None
    assert vh.next_link('<https://huggingface.co/a>; rel="prev"') is None
    both = (
        '<https://huggingface.co/a>; rel="prev", <https://huggingface.co/b>; rel="next"'
    )
    assert vh.next_link(both) == "https://huggingface.co/b"


def test_a_nested_file_is_checked(tmp_path: pathlib.Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.safetensors").write_bytes(b"bad!")
    entries = vh.parse_tree([lfs_entry("sub/a.safetensors", b"good")])
    report = vh.verify(tmp_path, entries)
    assert report.checked == 1
    assert report.problems[0].startswith("sub/a.safetensors: sha256 differs")


def test_main_names_every_failing_file_and_exits_1(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "a.json").write_bytes(b"{}")
    (tmp_path / "b.json").write_bytes(b"[]")
    tree = [
        blob_entry("a.json", b"{}"),
        blob_entry("b.json", b"{}"),
        blob_entry("c.json", b"{}"),
        blob_entry(".gitattributes", b"x"),
    ]
    monkeypatch.setattr(vh, "fetch_tree", lambda repo, rev: tree)
    monkeypatch.setattr(vh.logs, "configure", lambda **_: None)
    with caplog.at_level(logging.INFO, logger=vh.logger.name):
        assert vh.main(["o/r", "abc", str(tmp_path)]) == 1
    text = caplog.text
    assert "b.json: git blob id differs" in text
    assert "c.json: missing (expected 2 bytes)" in text
    assert "2 of 3 checked files failed" in text


def test_main_says_what_it_skipped_on_a_pass(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ "44 of 45 files match" was a pass with .gitattributes skipped, and read
    as a mismatch (#1344)."""
    (tmp_path / "a.json").write_bytes(b"{}")
    tree = [blob_entry("a.json", b"{}"), blob_entry(".gitattributes", b"x")]
    monkeypatch.setattr(vh, "fetch_tree", lambda repo, rev: tree)
    monkeypatch.setattr(vh.logs, "configure", lambda **_: None)
    with caplog.at_level(logging.INFO, logger=vh.logger.name):
        assert vh.main(["o/r", "abc", str(tmp_path)]) == 0
    assert "all 1 checked files match" in caplog.text
    assert "skipped 1 (.gitattributes)" in caplog.text
