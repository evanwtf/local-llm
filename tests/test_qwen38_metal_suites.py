"""Tests for scripts/qwen38_metal_suites.py (#170)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from qwen38_metal_suites import (
    binary_for,
    parse_result,
    suite_targets,
    warnings_in,
)

MAKEFILE = """
tests/test_qwen38_gdn: tests/test_qwen38_gdn.o ds4_metal.o
\t$(CC) -o $@ $^

.PHONY: test-qwen38-gdn
test-qwen38-gdn: tests/test_qwen38_gdn
\t./tests/test_qwen38_gdn

.PHONY: test-qwen38-ple-hash
test-qwen38-ple-hash: tests/test_qwen38_ple_hash
\t./tests/test_qwen38_ple_hash

.PHONY: test-glm53-kda
test-glm53-kda: $(GLM53_KDA_TEST)
\t./$(GLM53_KDA_TEST)
"""


def test_targets_come_from_the_tree_not_a_hard_coded_list() -> None:
    assert suite_targets(MAKEFILE) == ["test-qwen38-gdn", "test-qwen38-ple-hash"]


def test_a_target_declared_twice_is_listed_once() -> None:
    # `.PHONY:` and the rule itself both name the target.
    assert suite_targets(MAKEFILE + "\ntest-qwen38-gdn: tests/test_qwen38_gdn\n") == [
        "test-qwen38-gdn",
        "test-qwen38-ple-hash",
    ]


def test_unrelated_suites_are_not_picked_up() -> None:
    assert "test-glm53-kda" not in suite_targets(MAKEFILE)


def test_binary_name_converts_dashes_to_underscores() -> None:
    assert binary_for("test-qwen38-ple-hash") == "tests/test_qwen38_ple_hash"
    assert binary_for("test-qwen38-gdn") == "tests/test_qwen38_gdn"


def test_the_pass_line_is_what_makes_a_suite_pass() -> None:
    r = parse_result(
        "test-qwen38-gdn", "ds4: Metal device\nQwen3.8 GDN GPU tests: PASS\n", 0
    )
    assert r.passed
    assert r.pass_line == "Qwen3.8 GDN GPU tests: PASS"


def test_the_ple_store_spelling_also_counts() -> None:
    # This suite ends `test_qwen38_ple_store: ok`, not `tests: PASS`.
    r = parse_result(
        "test-qwen38-ple-store", "  gather ok\ntest_qwen38_ple_store: ok\n", 0
    )
    assert r.passed
    assert r.pass_line == "test_qwen38_ple_store: ok"


def test_exit_zero_without_a_pass_line_is_not_a_pass() -> None:
    # A Metal device that refuses to come up can complain and still exit 0.
    # Trusting the status alone would report a suite that asserted nothing.
    r = parse_result("test-qwen38-qsa", "ds4: Metal device unavailable\n", 0)
    assert not r.passed
    assert r.pass_line is None


def test_a_pass_line_does_not_rescue_a_nonzero_exit() -> None:
    r = parse_result(
        "test-qwen38-qsa", "Qwen3.8 QSA GPU tests: PASS\nlater: abort\n", 134
    )
    assert not r.passed


def test_the_word_pass_in_chatter_is_not_a_pass_line() -> None:
    r = parse_result("test-qwen38-qsa", "running PASS-through warmup\n", 0)
    assert not r.passed


def test_build_warnings_are_reported_separately_from_failure() -> None:
    log = (
        "cc -O3 -c -o a.o a.c\n"
        "tests/test_qwen38_qsa.c:148:28: warning: use of infinity via a macro\n"
        "  148 |   double best = -INFINITY;\n"
    )
    assert warnings_in(log) == [
        "tests/test_qwen38_qsa.c:148:28: warning: use of infinity via a macro"
    ]


def test_a_clean_build_reports_no_warnings() -> None:
    assert warnings_in("cc -O3 -c -o a.o a.c\ncc -o test a.o\n") == []
