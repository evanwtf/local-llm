"""Tests for the #67 before/after split.

The split decides which numbers this project is allowed to publish about
OpenCode, so its failure modes matter more than its happy path. It has already
failed once by classifying every row as "before" when `git log` ran in the
wrong directory and returned nothing.
"""

from __future__ import annotations

import json
import pathlib

import dirfix
import pytest
import results

from conftest import HAS_LOCAL_RESULTS, SKIP_NO_RESULTS


def row(**kw):
    base = {"client": "opencode", "backend": "ds4", "task": "mbox-scan", "passed": True}
    env = {"harness_head": kw.pop("head")} if "head" in kw else {}
    return {**base, **kw, "env": env}


# --- #393: the era is stamped on the row at write time ----------------------
#
# The #355 orphans needed an allowlist, REBASED_AFTER_FIX, because `git log`
# could no longer reach their harness_head. A row now carries its era, with
# the rule and the fix it was judged under, so a lost sha cannot move it.

ORPHANS = {"6f56a28": 5, "62d7de1": 30, "66d5e3a": 30}
GB10 = (
    pathlib.Path(__file__).resolve().parents[2]
    / "hardware"
    / ("Cortex-X925-128GB-GB10/results.jsonl")
)


def stamped(head: str, era: str = "after", **over) -> dict:
    stamp = {"rule": dirfix.RULE, "fix": dirfix.FIX, "era": era, **over}
    return {**row(head=head), "env": {"harness_head": head, "dir_era": stamp}}


def test_the_allowlist_is_retired() -> None:
    assert not hasattr(dirfix, "REBASED_AFTER_FIX")


def test_a_stamped_row_needs_no_git_history() -> None:
    """An unreachable sha with a stamp classifies by its stamp."""
    assert dirfix.era(stamped("deadbee"), set()) == "after"


def test_a_stamp_outranks_the_commit_set() -> None:
    assert dirfix.era(stamped("x", era="before"), {"x"}) == "before"


def test_a_stamp_under_another_rule_is_not_read_as_this_one() -> None:
    assert dirfix.era(stamped("deadbee", rule=2), set()) == "before"
    assert dirfix.era(stamped("deadbee", fix="0000000"), set()) == "before"
    assert dirfix.era(stamped("deadbee", era="maybe"), set()) == "before"


def test_the_write_time_stamp_of_this_tree() -> None:
    got = dirfix.stamp(pathlib.Path(__file__).resolve().parent)
    assert got == {
        "rule": 1,
        "fix": "7356460",
        "era": "after",
        "basis": "ancestry at write time",
    }


def test_no_repository_gives_no_stamp(tmp_path) -> None:
    """git cannot answer, so the row stays unstamped rather than guessed."""
    assert dirfix.stamp(tmp_path) is None


def test_the_355_orphans_are_after_from_their_own_rows() -> None:
    """The 65 GB10 rows the allowlist kept are "after" with no commit set."""
    rows = [json.loads(x) for x in GB10.read_text().splitlines() if x.strip()]
    got: dict[str, int] = {}
    for r in rows:
        head = str((r.get("env") or {}).get("harness_head", ""))[:7]
        if head in ORPHANS:
            assert dirfix.era(r, set()) == "after"
            assert r["env"]["dir_era"]["basis"].startswith("#355")
            got[head] = got.get(head, 0) + 1
    assert got == ORPHANS


def test_a_row_from_after_the_fix_is_counted_as_after() -> None:
    assert dirfix.era(row(head="28b1da6"), {"28b1da6"}) == "after"


def test_a_row_with_no_provenance_is_before() -> None:
    """Rows predating the env block have no harness_head. That is not unknown
    -- the field was added after the fix, so its absence dates the row."""
    assert dirfix.era(row(), {"28b1da6"}) == "before"


def test_a_full_length_hash_matches_the_short_one() -> None:
    assert dirfix.era(row(head="28b1da6ffffffff"), {"28b1da6"}) == "after"


def test_an_empty_commit_set_refuses_rather_than_reporting_zeroes(tmp_path) -> None:
    """The original bug: `git log` in a non-repo exits cleanly with no output,
    every row lands in "before", and the table looks plausible and is wrong."""
    with pytest.raises(SystemExit, match="no commits"):
        dirfix.fixed_commits(tmp_path)


def test_the_real_repo_resolves_the_fix() -> None:
    heads = dirfix.fixed_commits(pathlib.Path(__file__).resolve().parent)
    assert dirfix.FIX in heads


def test_script_and_excision_are_counted_apart() -> None:
    """A script task has no repo, so it cannot leak an answer or fail excision.
    Averaging the two classes hides which one a client is failing."""
    assert dirfix.task_class(row(task="script-reverse")) == "script"
    assert dirfix.task_class(row(task="mbox-scan")) == "excision"


def test_excluded_rows_never_reach_a_column() -> None:
    counts = dirfix.tally([row(head="x", excluded=True, passed=False)], {"x"})
    assert counts == {}


def test_other_clients_are_ignored() -> None:
    assert dirfix.tally([row(head="x", client="claude")], {"x"}) == {}


def test_wins_and_totals_accumulate_per_cell() -> None:
    rows = [row(head="x"), row(head="x", passed=False), row()]
    counts = dirfix.tally(rows, {"x"})
    assert counts[("ds4", "excision", "after")] == [1, 2]
    assert counts[("ds4", "excision", "before")] == [1, 1]


def test_a_cell_missing_one_era_prints_a_dash_not_a_zero() -> None:
    """'0/0' would read as a measured failure. It is an absence."""
    lines = dirfix.report({("ds4", "script", "after"): [3, 3]})
    assert lines[1].split() == ["ds4", "script", "-", "3/3"]


def test_a_client_that_never_ran_is_excluded_without_a_stored_flag() -> None:
    """agent_error rows carry excluded=False on disk; results.is_excluded knows
    better. Counting them as model failures is how a config bug becomes a
    published benchmark number -- which is what #67 is about."""
    counts = dirfix.tally([row(head="x", passed=False, agent_error=True)], {"x"})
    assert counts == {}


def test_the_archive_is_still_where_dirfix_expects_it() -> None:
    """The pre---dir rows live outside results.jsonl. If the archive moves or is
    deleted, the "before" column empties and the fix silently looks unmeasured —
    which is the same class of failure as the git-log bug above."""
    assert dirfix.ARCHIVE.exists(), f"{dirfix.ARCHIVE} is missing"
    heads = dirfix.fixed_commits(pathlib.Path(__file__).resolve().parent)
    rows = [json.loads(x) for x in dirfix.ARCHIVE.read_text().splitlines()]
    assert rows, "archive is empty"
    assert all(r.get("client") == "opencode" for r in rows)
    assert all(dirfix.era(r, heads) == "before" for r in rows), (
        "a post-fix row is in the pre-fix archive"
    )


@pytest.mark.skipif(not HAS_LOCAL_RESULTS, reason=SKIP_NO_RESULTS)
def test_no_pre_dir_opencode_rows_remain_in_results() -> None:
    """Live results must contain only trials that measured the client."""
    live = results.default_path()
    heads = dirfix.fixed_commits(live.parent)
    stragglers = [
        json.loads(x)
        for x in live.read_text().splitlines()
        if json.loads(x).get("client") == "opencode"
        and dirfix.era(json.loads(x), heads) == "before"
    ]
    assert not stragglers, f"{len(stragglers)} pre---dir rows still in results.jsonl"


def test_a_pass_that_edited_the_tests_is_not_a_win() -> None:
    """Code review, 2026-10-05: `passed` alone counted a test-editing row as a
    win. results.verdict() decides, with its guards."""
    got = dirfix.tally([row(head="x"), row(head="x", touched_tests=True)], {"x"})
    assert got[("ds4", "excision", "after")] == [1, 2]


def test_a_dry_run_is_not_a_trial() -> None:
    got = dirfix.tally([row(head="x"), row(head="x", passed=None, dry_run=True)], {"x"})
    assert got[("ds4", "excision", "after")] == [1, 1]
