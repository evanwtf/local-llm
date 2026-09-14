"""Reading the three Ternary-Bonsai arms (#269).

Every case here is a number that already reached a commit message looking
fine. The arms differ in two things at once -- weights and engine -- so the
failures this file pins are all the same shape: a real difference, attributed
to the wrong cause.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import bonsai_quant_report as bqr


def row(
    backend: str,
    task: str,
    passed: bool,
    *,
    engine: str | None = None,
    version: str | None = None,
    ollama: str | None = None,
    touched_tests: bool = False,
) -> dict:
    env: dict = {}
    if engine:
        env["servers"] = {backend: {"engine_name": engine, "engine_version": version}}
    if ollama:
        env["ollama"] = f"ollama version is {ollama}"
    return {
        "backend": backend,
        "task": task,
        "passed": passed,
        "touched_tests": touched_tests,
        "wall_seconds": 10.0,
        "env": env,
    }


def test_a_pass_that_edited_the_oracle_is_a_failure() -> None:
    """The 14-vs-13 defect, with the literal rows that caused it.

    `storage-blob-put` trial 5 of the Ollama 0.33.3 arm carries
    `passed: True` and `touched_tests: True`. Reading `row["passed"]` scored
    that arm 14/26 and put 54% in fd664c2; `results.verdict()` scores it 13/26,
    which is 50%. The engine contrast moved from p = 0.30 to p = 0.19 on this
    one row.
    """
    rows = [
        row("dtbonsai27b", "storage-blob-put", True, ollama="0.33.3"),
        row(
            "dtbonsai27b",
            "storage-blob-put",
            True,
            ollama="0.33.3",
            touched_tests=True,
        ),
    ]
    assert bqr.tally(rows) == (1, 2), "an edited oracle is not a pass"


def test_two_ollama_builds_under_one_backend_name_are_two_arms() -> None:
    """Between 2026-09-03 and 2026-09-09 the same weights, under the same
    backend name, went from 42% to 54% -- and the only thing that changed was
    Ollama 0.33.2 -> 0.33.3, which changed which sampler the model got (#84).

    Keying an arm on the backend alone reports that version bump as a property
    of the model.
    """
    rows = [
        row("dtbonsai27b", "mbox-scan", False, ollama="0.33.2"),
        row("dtbonsai27b", "mbox-scan", True, ollama="0.33.3"),
    ]
    assert set(bqr.arms(rows)) == {
        ("dtbonsai27b", "ollama 0.33.2"),
        ("dtbonsai27b", "ollama 0.33.3"),
    }


def test_the_engine_build_comes_from_the_row_not_the_backend_name() -> None:
    """llama.cpp rows carry the build in the server block; Ollama rows carry
    no server block at all. Both must resolve, or an arm silently becomes
    'unrecorded' and pools with anything else that is."""
    llamacpp = row(
        "dtternarybonsai27b", "mbox-scan", True, engine="llama.cpp", version="d8f26ee"
    )
    ollama = row("dtbonsai27b", "mbox-scan", True, ollama="0.33.3")
    assert bqr.engine_of(llamacpp) == "llama.cpp d8f26ee"
    assert bqr.engine_of(ollama) == "ollama 0.33.3"


def test_a_task_only_one_arm_ran_is_dropped_from_the_contrast() -> None:
    """`mbox-strip-envelope` reached the PQ2_0 arm on 2026-09-12 and no other
    arm on this tier has ever run it. Counted in, it inflates that arm's pooled
    rate against arms that never saw the task."""
    a = [row("dtbonsai27bllamacpp", "mbox-scan", False, engine="llama.cpp")]
    b = [
        row("dtternarybonsai27b", "mbox-scan", False, engine="llama.cpp"),
        row("dtternarybonsai27b", "mbox-strip-envelope", True, engine="llama.cpp"),
    ]
    shared, dropped = bqr.shared_tasks(a, b)
    assert shared == ["mbox-scan"]
    assert dropped == ["mbox-strip-envelope"]
    (_, _), (b_pass, b_n), _ = bqr.compare(a, b)
    assert (b_pass, b_n) == (0, 1), "the unshared task must not reach the 2x2"


def test_the_quant_contrast_reproduces_the_committed_figures() -> None:
    """26/38 against 15/20 is what the ledger held on 2026-09-09, and
    p = 0.7639 is what it supports. The point of the number is that it is not
    significant: the lead claimed Q2 beats Q1, and at this n the data cannot
    say so either way."""
    q1 = [
        row("dtbonsai27bllamacpp", "mbox-scan", i < 26, engine="llama.cpp")
        for i in range(38)
    ]
    q2 = [
        row("dtternarybonsai27b", "mbox-scan", i < 15, engine="llama.cpp")
        for i in range(20)
    ]
    (a, b, p) = bqr.compare(q1, q2)
    assert a == (26, 38)
    assert b == (15, 20)
    assert round(p, 4) == 0.7639
