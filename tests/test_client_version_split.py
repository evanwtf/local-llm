"""Which client version took which rows (#137)."""

from __future__ import annotations

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import client_version_split as split


def results(tmp_path, rows):
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_rows_without_a_version_are_not_counted(tmp_path):
    """190 rows cannot be established; they must not vote on the split."""
    p = results(
        tmp_path,
        [
            {"backend": "a", "client": "opencode", "client_version": "1.0"},
            {"backend": "a", "client": "opencode"},
            {"backend": "a", "client": "opencode", "client_version": "  "},
        ],
    )
    assert split.rows_by_backend(p)["a"] == collections.Counter({"1.0": 1})


def test_excluded_rows_do_not_vote(tmp_path):
    p = results(
        tmp_path,
        [
            {"backend": "a", "client_version": "1.0", "excluded": True},
            {"backend": "a", "client_version": "2.0"},
        ],
    )
    assert set(split.rows_by_backend(p)["a"]) == {"2.0"}


def test_a_backend_spanning_versions_is_flagged():
    """Worse than a confounded comparison: the cell's own rate mixes clients
    and cannot be split apart afterwards."""
    table = {"a": collections.Counter({"1.0": 3, "2.0": 1})}
    assert split.split_backends(table) == ["a"]


def test_a_consistent_backend_is_not_flagged():
    assert split.split_backends({"a": collections.Counter({"1.0": 3})}) == []


def test_disjoint_backends_are_a_confounded_pair():
    """The #137 situation: comparing them compares the clients too."""
    table = {
        "old": collections.Counter({"1.18.25": 10}),
        "new": collections.Counter({"1.18.27": 10}),
    }
    assert split.confounded_pairs(table) == [("new", "old")]


def test_backends_sharing_a_version_are_not_confounded():
    table = {
        "a": collections.Counter({"1.18.27": 5}),
        "b": collections.Counter({"1.18.27": 5}),
    }
    assert split.confounded_pairs(table) == []


def test_a_partial_overlap_is_not_confounded():
    """Sharing any version means a comparison can be made on that subset."""
    table = {
        "a": collections.Counter({"1.18.25": 5, "1.18.27": 5}),
        "b": collections.Counter({"1.18.27": 5}),
    }
    assert split.confounded_pairs(table) == []


def test_client_filter_selects_one_client(tmp_path):
    p = results(
        tmp_path,
        [
            {"backend": "a", "client": "opencode", "client_version": "1.0"},
            {"backend": "b", "client": "claude", "client_version": "2.0"},
        ],
    )
    assert set(split.rows_by_backend(p, client="opencode")) == {"a"}
