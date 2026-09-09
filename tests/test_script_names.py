"""A script must not be named so that pytest mistakes it for a test.

`testpaths = ["."]` in pyproject.toml, so collection walks the whole tree and
pytest's default `python_files` includes `*_test.py`. A measurement script
called `disk_kv_mechanism_test.py` was therefore imported by the suite, its
`test()` entry point was read as a test function, and the run ended

    ERROR scripts/disk_kv_mechanism_test.py::test - fixture 'trials' not found

with 2400 tests passing around it. The script was fine; its name was not. It
is `scripts/disk_kv_mechanism.py` now.

This is cheap to get wrong again, because `*_test.sh` is a perfectly good name
for a shell script and the port carries the name across.
"""

from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Where real tests live. Everywhere else, the suffix is a collection accident.
TEST_DIRS = ("tests/", "benchmarks/agent/", "sandbox/")


def test_no_script_is_named_like_a_test() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "*_test.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    offenders = [f for f in tracked if not f.startswith(TEST_DIRS)]
    assert not offenders, (
        f"{offenders} match pytest's `*_test.py` pattern and testpaths is the "
        "whole repo, so the suite will import them and read their functions "
        "as tests. Drop the suffix."
    )
