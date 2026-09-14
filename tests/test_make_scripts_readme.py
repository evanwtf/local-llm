"""The scripts index must stay complete, honest, and in sync (#368).

`scripts/README.md` is the map an agent reads before writing analysis code, so
that it reuses a committed, tested script instead of a heredoc that drifts
between sessions. A map only helps if it cannot go stale, so these tests fail
when the checked-in README no longer matches the generator, and when the
platform sets name a file that no longer exists.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_scripts_readme as m


def test_readme_is_in_sync() -> None:
    """The checked-in README matches the generator, or CI catches the drift."""
    on_disk = (ROOT / "scripts" / "README.md").read_text()
    assert on_disk == m.render(), (
        "scripts/README.md is stale -- run `uv run python "
        "scripts/make_scripts_readme.py` and commit the result"
    )


def test_platform_sets_name_real_files() -> None:
    """No stale entry: every MAC/NVIDIA name still names a script."""
    names = {p.name for p in m.script_paths()}
    stale = (m.MAC | m.NVIDIA) - names
    assert not stale, f"platform sets name files that do not exist: {sorted(stale)}"


def test_platforms_are_disjoint() -> None:
    """A script runs on one machine or the other, never declared as both."""
    assert not (m.MAC & m.NVIDIA)


def test_platform_for_returns_valid_values() -> None:
    """Every script resolves to one of the three known platforms."""
    for path in m.script_paths():
        assert m.platform_for(path.name) in {"mac", "nvidia", "any"}


def test_every_script_has_a_description() -> None:
    """A script with no docstring line is invisible in the index -- refuse it."""
    missing = [p.name for p in m.script_paths() if not m.first_doc_line(p)]
    assert not missing, f"scripts with no docstring/# summary: {missing}"
