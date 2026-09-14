"""docs/external-repos.md must name every engine_tree tasks.toml uses (#358).

A machine that clones only local-llm cannot reproduce a stack: the engine
repositories live outside the checkout and their paths are assumed. The doc is
the map. It is worth having only if it is complete, so a new engine_tree added
to tasks.toml without a line in the doc must fail here rather than leave the
next machine without a path. This checks the doc against the config, never
against the local disk -- tasks.toml is shared across the fleet and names trees
this machine does not build.
"""

from __future__ import annotations

import pathlib
import tomllib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
DOC = REPO / "docs" / "external-repos.md"
CONFIG = HERE / "tasks.toml"


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text()


@pytest.fixture(scope="module")
def engine_trees() -> set[str]:
    backends = tomllib.loads(CONFIG.read_text())["backend"]
    return {b["engine_tree"] for b in backends.values() if b.get("engine_tree")}


def test_the_document_exists(doc: str) -> None:
    assert "# External repositories" in doc


def test_ds4_root_is_explained(doc: str) -> None:
    """The one env var README already names must be resolved end to end here."""
    assert "DS4_ROOT" in doc
    assert "engine_tree" in doc


def test_every_engine_tree_is_named(doc: str, engine_trees: set[str]) -> None:
    """An engine_tree in tasks.toml with no line in the doc leaves the next
    machine without a path for that backend."""
    missing = sorted(t for t in engine_trees if t not in doc)
    assert not missing, (
        f"engine_tree paths absent from docs/external-repos.md: {missing}"
    )


def test_there_is_at_least_one_tree_to_check(engine_trees: set[str]) -> None:
    """Guard the guard: if the parse returns nothing, the test above passes
    vacuously and the doc could be empty."""
    assert len(engine_trees) >= 5
