"""#726: choosing a replay task's held-out tests is measured and reproducible."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import hidden_test_candidates as cand

SOURCE = """\
import pytest


def helper():
    pass


def test_top():
    pass


class TestBox:
    def test_one(self):
        pass

    def not_a_test(self):
        pass


class Helper:
    def test_ignored(self):
        pass
"""


def test_test_ids_are_function_level_and_in_file_order():
    assert cand.test_ids(SOURCE, "tests/test_a.py") == [
        "tests/test_a.py::test_top",
        "tests/test_a.py::TestBox::test_one",
    ]


JUNIT = """\
<testsuites><testsuite>
  <testcase classname="tests.test_a" name="test_top" />
  <testcase classname="tests.test_a.TestBox" name="test_one[1]" />
  <testcase classname="tests.test_a.TestBox" name="test_one[2]">
    <failure message="boom" />
  </testcase>
  <testcase classname="tests.test_a" name="test_skip"><skipped /></testcase>
  <testcase classname="tests.test_other" name="test_elsewhere" />
</testsuite></testsuites>
"""


def test_outcomes_fail_a_function_when_any_case_fails():
    got = cand.outcomes(JUNIT, ["tests/test_a.py"])
    assert got == {
        "tests/test_a.py::test_top": "passed",
        "tests/test_a.py::TestBox::test_one": "failed",
        "tests/test_a.py::test_skip": "skipped",
    }, "a file not asked about is left out"


IDS = [f"tests/test_a.py::test_{i}" for i in range(9)]


def test_pick_is_deterministic_and_takes_a_third():
    first = cand.pick(IDS, 1 / 3)
    assert first == cand.pick(list(reversed(IDS)), 1 / 3), "order does not matter"
    assert len(first) == 3
    assert first == sorted(first)


def test_pick_never_takes_every_test_or_splits_one():
    assert len(cand.pick(IDS[:2], 0.9)) == 1, "one must stay visible"
    assert cand.pick(IDS[:1], 1 / 3) == []


def test_collapse_hides_a_class_whose_every_test_was_picked():
    every = ["f::A::t1", "f::A::t2", "f::B::t1", "f::B::t2", "f::top"]
    assert cand.collapse(["f::A::t1", "f::A::t2", "f::B::t1"], every) == [
        "f::A",
        "f::B::t1",
    ]


def test_propose_takes_only_tests_that_discriminate_and_are_new():
    got = {
        "ids": ["f::new_fails", "f::new_passes", "f::old_fails", "f::broken"],
        "at": {
            "f::new_fails": "passed",
            "f::new_passes": "passed",
            "f::old_fails": "passed",
            "f::broken": "failed",
        },
        # absent after the revert means the file no longer imported
        "after": {"f::new_passes": "passed"},
        "older": {"f::old_fails"},
    }
    assert cand.propose(got, later=True, fraction=1.0) == ["f::new_fails"]
