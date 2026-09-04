"""Tests for the results schema.

A results row is the only durable evidence a trial ever ran. These tests exist
because four different keys once meant "do not trust this row", and an analysis
that knew about one of them silently counted the other fifteen.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from results import (
    LEGACY_EXCLUSION_KEYS,
    REQUIRED,
    REQUIRED_WITH_VERDICT,
    SCHEMA_VERSION,
    is_excluded,
    load,
    new_row,
    normalize,
    trials,
    validate,
    verdict,
    write_row,
)

REAL_RESULTS = pathlib.Path(__file__).parent / "results.jsonl"


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
    assert (
        new_row(
            task="t",
            backend="b",
            client="codex",
            trial=1,
            model="m",
            context_tokens=1,
            effort=None,
            env={},
        )["schema_version"]
        == SCHEMA_VERSION
    )


def test_new_row_starts_unexcluded_with_an_explicit_null_reason():
    """Absent is not the same as false. Both keys are always present."""
    row = new_row(
        task="t",
        backend="b",
        client="codex",
        trial=1,
        model="m",
        context_tokens=1,
        effort=None,
        env={},
    )
    assert row["excluded"] is False
    assert row["exclusion_reason"] is None


@pytest.mark.parametrize(
    "missing",
    [
        "task",
        "backend",
        "client",
        "trial",
        "started",
        "finished",
        "env",
        "excluded",
        "exclusion_reason",
        "schema_version",
    ],
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
    path.write_text(
        json.dumps({"task": "t", "backend": "b", "error": "timeout"}) + "\n"
    )
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
    real = REAL_RESULTS
    if not real.exists():
        pytest.skip("results.jsonl not present")
    known = set(LEGACY_EXCLUSION_KEYS)
    suspicious = set()
    for row in load(real):
        for k in row:
            if (
                "exclud" in k or "confound" in k or "contaminat" in k
            ) and k not in known:
                suspicious.add(k)
    assert suspicious == set()


# --- verdicts -------------------------------------------------------------
#
# A trial that timed out writes a row with `error` and no `passed` key. The
# obvious idiom for reading verdicts -- `if "passed" in row` -- drops those
# rows instead of counting them, which silently turned a 13/16 backend into a
# 13/13 one in a published table. `verdict()` and `trials()` exist so that
# cannot happen again.


def test_verdict_is_false_when_the_trial_timed_out():
    row = good_row()
    del row["passed"]
    row["error"] = "timeout"
    assert verdict(row) is False


def test_verdict_is_false_when_there_is_no_passed_key_at_all():
    row = good_row()
    del row["passed"]
    assert verdict(row) is False


def test_verdict_is_false_when_the_agent_edited_the_tests():
    assert verdict(good_row(passed=True, touched_tests=True)) is False


def test_verdict_is_false_when_the_control_did_not_fail():
    assert verdict(good_row(passed=True, control_fails_as_expected=False)) is False


def test_verdict_is_false_when_the_agent_escaped_the_sandbox():
    assert verdict(good_row(passed=True, source_repo_intact=False)) is False


def test_verdict_is_true_for_a_clean_pass():
    assert verdict(good_row()) is True


def test_a_dry_run_has_no_verdict_to_give():
    with pytest.raises(ValueError):
        verdict(good_row(dry_run=True))


def test_trials_drops_dry_runs_and_exclusions_but_keeps_timeouts(tmp_path):
    path = tmp_path / "r.jsonl"
    timed_out = good_row(trial=2)
    del timed_out["passed"]
    timed_out["error"] = "timeout"
    rows = [
        good_row(trial=1),
        timed_out,
        good_row(trial=3, dry_run=True),
        good_row(trial=4, excluded=True, exclusion_reason="ran during a download"),
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    got = trials(path)
    assert [r["trial"] for r in got] == [1, 2]
    assert [verdict(r) for r in got] == [True, False]


def test_trials_agrees_with_summarize_on_the_real_results_file():
    """The two readers of results.jsonl must not disagree about the denominator."""
    if not REAL_RESULTS.exists():
        pytest.skip("results.jsonl not present")
    import summarize

    mine = trials(REAL_RESULTS)
    theirs, _discarded, _retired, _cheats = summarize.load(REAL_RESULTS)
    assert len(mine) == len(theirs)
    assert sum(verdict(r) for r in mine) == sum(bool(r.get("passed")) for r in theirs)


def test_the_real_results_file_counts_its_timeouts_as_failures():
    if not REAL_RESULTS.exists():
        pytest.skip("results.jsonl not present")
    rows = trials(REAL_RESULTS)
    timed_out = [r for r in rows if "passed" not in r]
    assert timed_out, "expected the known qwen38fnq2 timeouts to still be present"
    assert all(verdict(r) is False for r in timed_out)


def test_a_crashed_client_is_excluded_automatically() -> None:
    """agent_error means no model attempt was made -- never a task failure.

    Counted as failures three times on 2026-08-31: 16 opus5 rows made the
    hosted reference read 28/44 (64%) against a real 28/29, and an OpenCode row
    that died in 0.7s with "UnknownError: Unexpected server error" would have
    joined a genuine 0/9 as a tenth failure. The field existed; nothing used it.
    """
    row = normalize({"task": "t", "agent_error": True, "passed": False})
    assert row["excluded"] is True
    assert "never ran" in row["exclusion_reason"]


def test_a_client_that_errored_but_still_passed_is_kept() -> None:
    """If the oracle passed, the trial produced a real result."""
    row = normalize({"task": "t", "agent_error": True, "passed": True})
    assert row["excluded"] is False


def test_a_genuine_failure_is_not_excluded() -> None:
    """The guard must not swallow real failures -- that is the whole record."""
    row = normalize({"task": "t", "agent_error": False, "passed": False})
    assert row["excluded"] is False


def test_a_timeout_is_still_a_real_outcome() -> None:
    """`error` is deliberately not an exclusion: the trial genuinely failed."""
    row = normalize({"task": "t", "error": "timeout", "passed": False})
    assert row["excluded"] is False


def test_the_row_names_the_version_of_the_client_that_ran():
    """#131: the client version must be readable without a join.

    `env` carries a version for every client installed, so a reader had to
    know to look up `env[row["client"]]`. #104's finding -- OpenCode
    1.18.26 -> 1.18.27 roughly doubling median turns -- cannot be applied to a
    single row that way.
    """
    row = new_row(
        task="t",
        backend="b",
        client="opencode",
        trial=1,
        model="m",
        context_tokens=8192,
        effort=None,
        env={"opencode": "1.18.27", "codex": "codex-cli 0.152.0"},
    )
    assert row["client_version"] == "1.18.27"


def test_the_version_is_stored_exactly_as_the_tool_printed_it():
    """Normalising would invent a format and lose what the tool said."""
    row = new_row(
        task="t",
        backend="b",
        client="codex",
        trial=1,
        model="m",
        context_tokens=8192,
        effort=None,
        env={"opencode": "1.18.27", "codex": "codex-cli 0.152.0"},
    )
    assert row["client_version"] == "codex-cli 0.152.0"


def test_an_unestablished_client_version_is_none_not_a_guess():
    """Absent means "not established", never "same as now"."""
    row = new_row(
        task="t",
        backend="b",
        client="aider",
        trial=1,
        model="m",
        context_tokens=8192,
        effort=None,
        env={"opencode": "1.18.27"},
    )
    assert row["client_version"] is None


def test_a_blank_version_string_is_none_rather_than_empty():
    row = new_row(
        task="t",
        backend="b",
        client="opencode",
        trial=1,
        model="m",
        context_tokens=8192,
        effort=None,
        env={"opencode": "   "},
    )
    assert row["client_version"] is None


def test_client_version_is_not_required_so_existing_rows_still_validate():
    """979 rows predate this field and `validate` runs on read (#131)."""
    assert "client_version" not in REQUIRED
    assert "client_version" not in REQUIRED_WITH_VERDICT


def test_the_row_records_where_it_sat_in_the_running_order():
    """#130: a row that does not say where it sat cannot be checked for
    positional bias afterwards, and none of the existing rows can be."""
    row = new_row(
        task="t",
        backend="b",
        client="opencode",
        trial=2,
        model="m",
        context_tokens=8192,
        effort=None,
        env={},
        run_position=2,
        run_arms=3,
    )
    assert row["run_position"] == 2
    assert row["run_arms"] == 3


def test_an_unrecorded_running_order_is_none_not_first():
    """Every row written before #130 has no order. Defaulting to 1 would
    claim they all ran first, which is exactly the bias being looked for."""
    row = new_row(
        task="t",
        backend="b",
        client="opencode",
        trial=1,
        model="m",
        context_tokens=8192,
        effort=None,
        env={},
    )
    assert row["run_position"] is None
    assert row["run_arms"] is None


def test_run_position_is_not_required_so_existing_rows_still_validate():
    assert "run_position" not in REQUIRED
    assert "run_arms" not in REQUIRED
