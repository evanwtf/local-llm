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

HERE = pathlib.Path(__file__).resolve().parent


def pytest_report_header() -> list[str]:
    return [
        f"harness: {provenance.head()}",
        f"results.jsonl: {provenance.fingerprint(HERE / 'results.jsonl')}",
    ]
