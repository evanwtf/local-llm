"""SOURCES.md's repository table must not drift from the sweep tool.

A watch list that lives in two places is a watch list that is wrong in one of
them. The tool is the source of truth because it is the thing that runs; the
document exists so a person can see what is watched and why.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from upstream_sweep import WATCHED  # noqa: E402


@pytest.fixture(scope="module")
def doc() -> str:
    return (REPO / "SOURCES.md").read_text()


def test_every_watched_repo_is_documented(doc):
    missing = sorted(r for r in WATCHED if f"github.com/{r})" not in doc)
    assert not missing, f"watched but absent from SOURCES.md: {missing}"


def test_every_watched_repo_says_why(doc):
    """A list of repos with no reasons is a second inbox, not a tool."""
    for repo, why in WATCHED.items():
        assert why.strip(), repo
        assert why in doc, repo


def test_the_engines_we_actually_run_are_watched():
    """The three live engines and the one client are not optional entries."""
    for repo in (
        "antirez/ds4",
        "ggml-org/llama.cpp",
        "ollama/ollama",
        "anomalyco/opencode",
    ):
        assert repo in WATCHED, repo


def test_the_target_repo_is_watched():
    """The excision tasks are exported from it; if it moves, the tasks change."""
    assert "evanwtf/gmail-archive" in WATCHED
