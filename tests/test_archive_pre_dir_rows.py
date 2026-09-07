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
    rather than raise FileNotFoundError.
    """
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "archive_pre_dir_rows.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "nothing to archive" in r.stdout or "archived" in r.stdout


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
