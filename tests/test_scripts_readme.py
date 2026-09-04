"""The scripts index must describe what is actually there (#4 housekeeping).

A generated index is worth having only if it cannot drift. This asserts it is
regenerable and current -- the same guarantee `test_sources.py` gives for the
watched-repository table.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
README = REPO / "scripts" / "README.md"


def test_every_script_is_listed():
    """A script nobody knows about is a script nobody runs. Eight were added
    in one afternoon and none was mentioned in any document."""
    text = README.read_text()
    scripts = sorted(
        p.name
        for p in (REPO / "scripts").iterdir()
        if p.suffix in (".py", ".sh") and p.name != "README.md"
    )
    missing = [s for s in scripts if f"`{s}`" not in text]
    assert not missing, f"not in scripts/README.md: {missing}"


def test_the_index_is_current():
    """Regenerating must be a no-op, or the checked-in file is stale."""
    before = README.read_text()
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "make_scripts_readme.py")],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    after = README.read_text()
    if before != after:
        README.write_text(before)
        raise AssertionError(
            "scripts/README.md is stale; run scripts/make_scripts_readme.py"
        )


def test_no_entry_is_empty():
    """An index row with no description is worse than an absent row: it says
    the script was considered and had nothing worth saying."""
    empty = [
        line
        for line in README.read_text().splitlines()
        if line.startswith("| `") and "(no description)" in line
    ]
    assert not empty, empty
