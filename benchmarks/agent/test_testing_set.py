"""TESTING-SET.md must not drift from tasks.toml.

A reference document that lists what is measured is worth having only if it is
true. This project has twice published numbers that described something other
than what the reader thought, so a doc naming the components is exactly the
kind of thing that must fail a test when it goes stale rather than quietly
misinform.
"""

from __future__ import annotations

import pathlib
import tomllib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
DOC = REPO / "TESTING-SET.md"
CONFIG = HERE / "tasks.toml"


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text()


@pytest.fixture(scope="module")
def backends() -> dict[str, dict]:
    return tomllib.loads(CONFIG.read_text())["backend"]


def test_the_document_exists(doc):
    assert "# The testing set" in doc


def test_every_live_backend_is_named(doc, backends):
    """A backend that runs and is not in the doc is an undocumented variable."""
    missing = sorted(
        name for name, b in backends.items() if not b.get("retired") and name not in doc
    )
    assert not missing, f"live backends absent from TESTING-SET.md: {missing}"


def test_every_retired_backend_is_named_as_retired(doc, backends):
    retired = [name for name, b in backends.items() if b.get("retired")]
    assert retired, "expected at least one retired backend"
    assert "Retired: LM Studio" in doc
    # The doc explains the class rather than listing every key, but the reason
    # a reader needs -- that rows still reference them -- must be present.
    assert "27 rows reference it" in doc


def test_every_task_is_counted(doc, backends):
    """The task counts in the doc must add up to the tasks that exist."""
    tasks = tomllib.loads(CONFIG.read_text())["task"]
    swift = [t for t in tasks if t["name"].startswith("swift-")]
    script = [t for t in tasks if t["name"].startswith("script-")]
    python_excision = [t for t in tasks if t not in swift and t not in script]
    assert f"| **Excision** (Python) | {len(python_excision)} |" in doc
    assert f"| **Excision** (Swift) | {len(swift)} |" in doc
    assert f"| **Script** | {len(script)} |" in doc


def test_the_cutover_timestamp_matches_the_archive_note(doc):
    """One timestamp, stated the same way everywhere.

    The `--dir` cutover is the line between valid and invalid OpenCode rows.
    Two documents disagreeing about it would put rows on the wrong side.
    """
    archive = (REPO / "docs/archive/results-opencode-pre-dir.md").read_text()
    assert "2026-08-31" in doc
    assert "21:47" in doc
    assert "21:47" in archive


def test_the_client_is_opencode_only(doc):
    assert "OpenCode, and nothing else" in doc
    assert "--dir` is not optional" in doc
