"""Tests for the excision, and for reading a body back out again.

There were none. excise.py decides what the agent is asked to write and what
counts as the original, so both directions need pinning: `excise` must remove
exactly the body and keep the docstring, and `body_source` must return the same
span without touching the file.
"""
from __future__ import annotations

import pathlib

import pytest

from excise import TargetNotFound, body_source, excise

SAMPLE = '''\
def keep_me():
    return 1


def target(a, b):
    """The contract stays.

    Multi-line, so a naive slice would eat it.
    """
    total = a + b
    return total


class Holder:
    def method(self, x):
        # no docstring here
        return x * 2
'''


@pytest.fixture
def sample(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "mod.py"
    p.write_text(SAMPLE)
    return p


def test_excise_removes_the_body_and_keeps_the_docstring(sample):
    removed = excise(sample, "target")
    after = sample.read_text()
    assert "total = a + b" in removed
    assert "total = a + b" not in after
    assert "The contract stays." in after
    assert 'raise NotImplementedError("removed for benchmark")' in after
    # Everything else in the module survives untouched.
    assert "def keep_me():\n    return 1" in after
    assert "return x * 2" in after


def test_excise_reaches_a_method(sample):
    removed = excise(sample, "Holder.method")
    assert "return x * 2" in removed
    assert "return x * 2" not in sample.read_text()


def test_a_leading_comment_survives_excision(sample):
    """Pinned because it leaks, and because it is easy to "fix" wrongly.

    A comment is not an AST node, so the first statement's `lineno` points past
    it and the comment stays in the hollowed-out file as a hint to the agent.
    Harmless on the five current targets -- none of them opens with a comment --
    but check a new target before adding it, because a leading comment that
    describes the algorithm hands over the answer. `body_source` uses the same
    span, so the two still agree either way.
    """
    excise(sample, "Holder.method")
    assert "# no docstring here" in sample.read_text()


def test_body_source_returns_exactly_what_excise_would_remove(sample):
    """The two must agree, or `restored_verbatim` compares different spans."""
    before = body_source(sample, "target")
    removed = excise(sample, "target")
    assert before == removed


def test_body_source_does_not_modify_the_file(sample):
    original = sample.read_text()
    body_source(sample, "target")
    assert sample.read_text() == original


def test_body_source_agrees_with_excise_on_a_method(sample):
    before = body_source(sample, "Holder.method")
    assert before == excise(sample, "Holder.method")


def test_a_missing_symbol_is_an_error_both_ways(sample):
    with pytest.raises(TargetNotFound):
        body_source(sample, "nope")
    with pytest.raises(TargetNotFound):
        excise(sample, "Holder.nope")


def test_a_docstring_only_function_has_nothing_to_remove(tmp_path):
    p = tmp_path / "m.py"
    p.write_text('def f():\n    """Just this."""\n')
    with pytest.raises(TargetNotFound):
        excise(p, "f")
