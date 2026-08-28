"""Tests for the results schema.

A results row is the only durable evidence a trial ever ran. These tests exist
because four different keys once meant "do not trust this row", and an analysis
that knew about one of them silently counted the other fifteen.
"""
from __future__ import annotations

import json

import pytest

from results import (
    LEGACY_EXCLUSION_KEYS,
    SCHEMA_VERSION,
    is_excluded,
    load,
    new_row,
    validate,
    write_row,
)


def good_row(**over):
    row = new_row(
        task="mbox-scan",
        backend="ds4anthropic",
        client="codex",
        trial=1,
        model="deepseek-v4-flash",
        context_tokens=100000,
        effort=None,
        env={"machine": "Apple M5 Max"},
    )
    row.update(
        finished="2026-08-28T00:10:00",
        passed=True,
        wall_seconds=147.1,
        pytest="16 passed in 0.10s",
        touched_tests=False,
        source_repo_intact=True,
        control_fails_as_expected=True,
        removed_lines=33,
        client_log="/Users/x/bench-logs/a.jsonl",
    )
    row.update(over)
    return row


# --- the schema itself ---------------------------------------------------


def test_a_complete_row_validates_clean():
    assert validate(good_row()) == []


def test_new_row_stamps_the_schema_version():
    assert new_row(
        task="t", backend="b", client="codex", trial=1,
        model="m", context_tokens=1, effort=None, env={},
    )["schema_version"] == SCHEMA_VERSION


def test_new_row_starts_unexcluded_with_an_explicit_null_reason():
    """Absent is not the same as false. Both keys are always present."""
    row = new_row(
        task="t", backend="b", client="codex", trial=1,
        model="m", context_tokens=1, effort=None, env={},
    )
    assert row["excluded"] is False
    assert row["exclusion_reason"] is None


@pytest.mark.parametrize(
    "missing",
    ["task", "backend", "client", "trial", "started", "finished", "env",
     "excluded", "exclusion_reason", "schema_version"],
)
def test_a_missing_required_field_is_a_violation(missing):
    row = good_row()
    del row[missing]
    assert any(missing in v for v in validate(row))


def test_a_verdict_row_must_carry_its_verdict():
    """passed/wall_seconds are required once a trial actually produced one."""
    row = good_row()
    del row["wall_seconds"]
    assert any("wall_seconds" in v for v in validate(row))


def test_wrong_types_are_violations():
    assert validate(good_row(trial="1")) != []
    assert validate(good_row(passed="yes")) != []
    assert validate(good_row(excluded="no")) != []


def test_the_legacy_exclusion_keys_are_banned_going_forward():
    """The whole point. A v2 row may never reintroduce them."""
    for key in LEGACY_EXCLUSION_KEYS:
        if key == "excluded":
            continue
        assert any(key in v for v in validate(good_row(**{key: "because"}))), key


# --- writing -------------------------------------------------------------


def test_a_bad_row_is_still_written_but_flagged(tmp_path):
    """A 30-minute trial is never thrown away; the defect is made loud."""
    path = tmp_path / "r.jsonl"
    row = good_row()
    del row["wall_seconds"]
    write_row(row, path)
    got = json.loads(path.read_text())
    assert got["schema_valid"] is False
    assert any("wall_seconds" in e for e in got["schema_errors"])


def test_a_good_row_is_marked_valid(tmp_path):
    path = tmp_path / "r.jsonl"
    write_row(good_row(), path)
    got = json.loads(path.read_text())
    assert got["schema_valid"] is True
    assert got["schema_errors"] == []


def test_writing_appends_and_never_overwrites(tmp_path):
    path = tmp_path / "r.jsonl"
    write_row(good_row(task="a"), path)
    write_row(good_row(task="b"), path)
    assert [json.loads(x)["task"] for x in path.read_text().splitlines()] == ["a", "b"]


# --- reading legacy rows -------------------------------------------------


@pytest.mark.parametrize(
    "legacy",
    [
        {"excluded": True},
        {"exclude_reason": "ran at context_tokens=65536"},
        {"excluded_reason": "contaminated duplicate"},
        {"contaminated": "operator probed the server mid-trial"},
        {"confound": "OpenCode reached ds4-server over /v1/chat/completions"},
    ],
)
def test_every_legacy_exclusion_key_is_honoured_on_read(tmp_path, legacy):
    """The regression test for the bug that started this."""
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"task": "t", "backend": "b", **legacy}) + "\n")
    assert is_excluded(load(path)[0]) is True


def test_a_timeout_is_an_outcome_not_an_exclusion(tmp_path):
    """`error: timeout` means the run failed, not that the row is untrustworthy."""
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"task": "t", "backend": "b", "error": "timeout"}) + "\n")
    assert is_excluded(load(path)[0]) is False


def test_legacy_rows_default_to_the_claude_client(tmp_path):
    """`client` was added later; rows without it predate the other clients."""
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"task": "t", "backend": "b"}) + "\n")
    assert load(path)[0]["client"] == "claude"


def test_legacy_rows_are_version_1(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"task": "t", "backend": "b"}) + "\n")
    assert load(path)[0]["schema_version"] == 1


def test_load_does_not_rewrite_the_file(tmp_path):
    """Normalisation happens in memory. The file on disk is evidence."""
    path = tmp_path / "r.jsonl"
    raw = json.dumps({"task": "t", "backend": "b", "confound": "x"}) + "\n"
    path.write_text(raw)
    load(path)
    assert path.read_text() == raw


def test_the_real_results_file_has_no_unknown_exclusion_keys():
    """Guards against a sixth variant appearing without anyone noticing."""
    import pathlib
    real = pathlib.Path(__file__).parent / "results.jsonl"
    if not real.exists():
        pytest.skip("results.jsonl not present")
    known = set(LEGACY_EXCLUSION_KEYS)
    suspicious = set()
    for row in load(real):
        for k in row:
            if ("exclud" in k or "confound" in k or "contaminat" in k) and k not in known:
                suspicious.add(k)
    assert suspicious == set()
