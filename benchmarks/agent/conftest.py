"""Make every test run attributable.

A pytest report pasted into an issue or a commit message is evidence, and
evidence with no version on it cannot be checked. This prints the harness
commit and the fingerprint of the data the tests read, once per session.

`-dirty` means the tree had uncommitted changes: the run exercised code that
exists nowhere but that machine, so the result is not reproducible from any
commit.
"""

from __future__ import annotations

import pathlib

import provenance
import pytest
import results
import run

HERE = pathlib.Path(__file__).resolve().parent


#: True when THIS machine's results file exists with rows in it.
#:
#: Several tests read the measured data, and after #85 that data lives at a
#: machine-derived path. On any other host -- the Linux CI runner included --
#: the path resolves somewhere empty, and those tests failed on emptiness
#: rather than on anything being wrong. That made CI red for 20 consecutive
#: runs while the local suite stayed green.
#:
#: The skip is keyed on the FILE, never on the platform: on the machine that
#: owns the rows the tests still run, because a skipping test is not a passing
#: test. A second machine with its own results file gets them too.
HAS_LOCAL_RESULTS = results.default_path().exists()

SKIP_NO_RESULTS = (
    f"no results file for this machine at {results.default_path()}; "
    "these tests read measured data and there is none here"
)


def pytest_report_header() -> list[str]:
    # `results` was referenced here without being imported. `-q` hides the
    # header, so a bare `pytest` died with INTERNALERROR while every -q run
    # looked fine.
    return [
        f"harness: {provenance.head()}",
        f"results.jsonl: {provenance.fingerprint(results.default_path())}",
    ]


@pytest.fixture(autouse=True)
def _keep_the_stash_inside_the_test(tmp_path, monkeypatch):
    """No test may reach the real stash, marker or notice.

    `stash_targets` moves a directory to `run.STASH_ROOT` and `restore_targets`
    rmtrees whatever stands in its place. A test that patches only the marker
    still writes into the operator's home: on 2026-09-04 one of them parked a
    fixture repo at ~/.local-llm-bench/stash/repo, and a second test then failed
    because that leftover was in the way.

    Worse than the mess is the timing. A live batch keeps real checkouts in
    exactly these paths for hours, so a suite run during a batch is a suite run
    aimed at the operator's repositories. Redirect all three by default; a test
    that wants its own paths overrides them as before.
    """
    monkeypatch.setattr(run, "STASH_ROOT", tmp_path / "stash", raising=False)
    monkeypatch.setattr(run, "STASH_MARKER", tmp_path / "stash.json", raising=False)
    monkeypatch.setattr(run, "STASH_NOTICE", tmp_path / "NOTICE.md", raising=False)
