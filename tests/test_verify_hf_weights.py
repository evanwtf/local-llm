"""A downloaded or copied checkpoint is checked against the Hub's own hashes.

Weights move twice before a run: one download on the head, then a copy to the
worker over the fabric. A truncated or corrupt shard loads, or half-loads, and
then fails as a model problem hours later. These tests pin the comparison: LFS
files by sha256 and size, small files by git blob id, and every way a directory
can disagree with the revision.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

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
    assert report.problems == ["model-00001.safetensors: sha256 differs"]


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
    assert report.problems == ["config.json: missing"]


def test_a_changed_small_file_fails_on_its_git_blob_id(tmp_path: pathlib.Path) -> None:
    (tmp_path / "config.json").write_bytes(b"[]")
    entries = vh.parse_tree([blob_entry("config.json", b"{}")])
    report = vh.verify(tmp_path, entries)
    assert report.problems == ["config.json: git blob id differs"]


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
