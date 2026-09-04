"""The per-run poster must never duplicate, and never post a partial run."""

from __future__ import annotations

import json
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import post_ab_run as poster


def _run(tmp_path: pathlib.Path, name: str, rows: dict[str, int]) -> pathlib.Path:
    d = tmp_path / name
    d.mkdir()
    for fname, n in rows.items():
        lines = ["ctx_tokens,prefill_tps,gen_steady_tps"]
        lines += [f"{2048 * (i + 1)},100.0,10.0" for i in range(n)]
        (d / fname).write_text("\n".join(lines) + "\n")
    return d


def test_a_run_with_a_short_csv_is_not_complete(tmp_path):
    """A file being written already has a name and a header. Reporting on it
    would silently include a partial arm."""
    d = _run(tmp_path, "r", {"a-rep1.csv": 32, "b-rep1.csv": 23})
    assert not poster.is_complete(d)


def test_a_run_with_uniform_csvs_is_complete(tmp_path):
    d = _run(tmp_path, "r", {"a-rep1.csv": 32, "b-rep1.csv": 32})
    assert poster.is_complete(d)


def test_an_empty_directory_is_not_complete(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert not poster.is_complete(d)


def test_header_only_csvs_are_not_complete(tmp_path):
    d = _run(tmp_path, "r", {"a-rep1.csv": 0, "b-rep1.csv": 0})
    assert not poster.is_complete(d)


def test_the_marker_names_the_run_so_reposting_is_a_noop():
    a = poster.MARKER.format(name="pr621-recheck-run1")
    b = poster.MARKER.format(name="pr621-recheck-run2")
    assert a != b
    assert "pr621-recheck-run1" in a


def test_already_posted_matches_only_its_own_run(monkeypatch):
    body = poster.MARKER.format(name="run1") + "\n## run1\n"
    payload = json.dumps({"comments": [{"body": body}]})
    monkeypatch.setattr(
        poster,
        "_gh",
        lambda args, **kw: types.SimpleNamespace(
            returncode=0, stdout=payload, stderr=""
        ),
    )
    assert poster.already_posted(91, "run1")
    assert not poster.already_posted(91, "run2")


def test_an_unreadable_issue_refuses_rather_than_risking_a_duplicate(monkeypatch):
    """A missing comment is recoverable by rerunning; a duplicate is not."""
    monkeypatch.setattr(
        poster,
        "_gh",
        lambda args, **kw: types.SimpleNamespace(
            returncode=1, stdout="", stderr="boom"
        ),
    )
    with pytest.raises(RuntimeError):
        poster.already_posted(91, "run1")
