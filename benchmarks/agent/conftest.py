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
import results

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
