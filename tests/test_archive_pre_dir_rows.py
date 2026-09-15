"""The archiver must be able to find the ledger (#67).

It could not, from 2026-08-15 -- when the ledger moved to
hardware/<machine>/results.jsonl -- until 2026-09-07. It held a literal
`benchmarks/agent/results.jsonl` resolved against the CALLER's cwd, so every
invocation raised FileNotFoundError and archived nothing. Nothing noticed,
because the invariant it enforces is checked by a test that SKIPS whenever
`results.default_path()` does not exist -- which is every CI runner, since
that path is derived from the runner's own hardware.

The cost showed up on 2026-09-07: a branch merge that took the union of two
row sets restored 90 rows the archive already held, and both the local suite
and CI were green on the branch that did it.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import archive_pre_dir_rows as apdr

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_ledger_path_is_the_one_results_py_reports():
    """Not a literal. A second copy of the path is a second thing to update."""
    import results

    assert apdr.RESULTS == results.default_path()


def test_both_paths_are_absolute():
    """They were relative to the caller's cwd, which is how this broke."""
    assert apdr.RESULTS.is_absolute() and apdr.ARCHIVE.is_absolute()


def test_the_archive_lives_in_this_repo():
    assert apdr.ARCHIVE.is_relative_to(ROOT)


def test_it_runs_from_a_directory_that_is_not_the_repo(tmp_path):
    """The regression exactly: invoked from elsewhere, it must still work.

    It is idempotent, so on a clean tree this is a no-op that must exit 0
    rather than raise FileNotFoundError -- and on a machine with no ledger at
    all (every CI runner) it must say so and exit 0, which is the second half
    of the same bug: this test was written asserting only the first half and
    turned CI red on 2026-09-07.
    """
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "archive_pre_dir_rows.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert (
        "nothing to archive" in r.stdout
        or "archived" in r.stdout
        or "no ledger at" in r.stdout
    ), r.stdout


def test_the_invariant_holds_right_now():
    """The live ledger carries no pre---dir OpenCode row.

    benchmarks/agent/test_dirfix.py asserts this too and skips when the
    ledger is absent. This one states the same thing from the scripts side;
    it also skips on a machine with no ledger, and that is the gap both share.
    """
    if not apdr.RESULTS.exists():
        import pytest

        pytest.skip("no ledger for this machine")
    after = apdr.fixed_commits(ROOT)
    stragglers = [
        x
        for x in apdr.RESULTS.read_text().splitlines(keepends=True)
        if apdr.is_pre_dir(x, after)
    ]
    assert not stragglers, f"{len(stragglers)} pre---dir rows are back in the ledger"


def test_the_archiver_has_no_classifier_of_its_own():
    """#392: a second fixed_commits() drifted from dirfix's once and archived 65
    post-fix rows. One classifier, so there is nothing to drift."""
    import dirfix

    assert apdr.fixed_commits is dirfix.fixed_commits
    assert apdr.FIX == dirfix.FIX


def test_is_pre_dir_is_dirfix_era_for_opencode_rows():
    import dirfix

    after = {"28b1da6"}
    for env in ({}, {"harness_head": "28b1da6"}, {"harness_head": "0000000"}):
        row = {"client": "opencode", "env": env}
        line = json.dumps(row)
        assert apdr.is_pre_dir(line, after) == (dirfix.era(row, after) == "before")
    assert not apdr.is_pre_dir(json.dumps({"client": "claude", "env": {}}), after)


PRE = '{"client":"opencode","env":{"harness_head":"0000000"}}\n'
POST = '{"client":"opencode","env":{"harness_head":"28b1da6"}}\n'
OTHER = '{"client":"claude","env":{}}\n'


def test_a_second_planned_pass_moves_nothing():
    """#392 idempotence, in the dry-run form: plan twice, never archive twice."""
    lines = [POST, PRE, OTHER, PRE]
    move, keep = apdr.plan(lines, {"28b1da6"})
    assert move == [PRE, PRE]
    assert keep == [POST, OTHER]
    assert apdr.plan(keep, {"28b1da6"}) == ([], keep)


def _point_at(monkeypatch, tmp_path, ledger_text):
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(ledger_text)
    archive = tmp_path / "archive.jsonl"
    archive.write_text("")
    monkeypatch.setattr(apdr, "RESULTS", ledger)
    monkeypatch.setattr(apdr, "ARCHIVE", archive)
    monkeypatch.setattr(apdr, "fixed_commits", lambda repo: {"28b1da6"})
    return ledger, archive


def test_check_reports_a_planned_move_and_writes_nothing(monkeypatch, tmp_path):
    ledger, archive = _point_at(monkeypatch, tmp_path, POST + PRE)
    assert apdr.main(["--check"]) == 1
    assert ledger.read_text() == POST + PRE
    assert archive.read_text() == ""


def test_check_passes_on_a_clean_ledger(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path, POST + OTHER)
    assert apdr.main(["--check"]) == 0


def test_the_real_run_then_check_is_clean(monkeypatch, tmp_path):
    ledger, archive = _point_at(monkeypatch, tmp_path, POST + PRE)
    assert apdr.main([]) == 0
    assert ledger.read_text() == POST
    assert archive.read_text() == PRE
    assert apdr.main(["--check"]) == 0


def test_archiver_agrees_with_dirfix_on_the_orphaned_shas():
    """#355: `git gc` collected the rebased-but-post-fix shas, so `git log`
    cannot reach them. dirfix.py unions REBASED_AFTER_FIX to keep those rows
    classified "after"; the archiver MUST union the same set, or it treats them
    as pre-dir and moves post-fix rows into the pre-dir "before" archive --
    which broke test_dirfix's archive invariant and turned main red on
    2026-09-14. The two classifiers must share one source of truth.
    """
    import dirfix

    after = apdr.fixed_commits(ROOT)
    assert set(dirfix.REBASED_AFTER_FIX).issubset(after), (
        "archiver fixed_commits dropped the #355 orphan shas dirfix keeps"
    )
    for sha in dirfix.REBASED_AFTER_FIX:
        row = f'{{"client":"opencode","env":{{"harness_head":"{sha}"}}}}'
        assert not apdr.is_pre_dir(row, after), (
            f"post-fix orphan sha {sha} would be wrongly archived as pre-dir"
        )
