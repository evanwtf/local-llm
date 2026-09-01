"""Tests for the `script-transform` task.

The prompt lives in two places -- SCRIPT-TRANSFORM.md, which is published, and
tasks.toml, which is sent. If they drift, the document describes a benchmark
nobody ran. That already happened once with a smoke prompt (#gen_prompts).

The expected values are recomputed here from the operation definitions rather
than compared to a copied table, so a typo in the spec cannot become the
oracle.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import tomllib

import run

HERE = pathlib.Path(__file__).resolve().parent
SPEC = HERE / "SCRIPT-TRANSFORM.md"


def task() -> dict:
    with (HERE / "tasks.toml").open("rb") as fh:
        cfg = tomllib.load(fh)
    return next(t for t in cfg["task"] if t["name"] == "script-transform")


def apply(text: str, ops: list[str]) -> str:
    """The specification, as code. Fixed order: reverse, sort, sha256."""
    v = text
    if "reverse" in ops:
        v = v[::-1]
    if "sort" in ops:
        v = "".join(sorted(v))
    if "sha256" in ops:
        v = hashlib.sha256(v.encode()).hexdigest()
    return v


def test_the_sent_prompt_matches_the_published_one() -> None:
    published = (
        re.search(r"## The prompt.*?```text\n(.*?)```", SPEC.read_text(), re.DOTALL)
        .group(1)
        .rstrip("\n")
    )
    assert task()["prompt"] == published


def test_every_expected_value_is_correct() -> None:
    """Recomputed, not copied. A wrong oracle marks correct work as failure."""
    for argv, want in task()["checks"]:
        text = argv[argv.index("--input") + 1]
        ops = [a.lstrip("-") for a in argv if a.startswith("--") and a != "--input"]
        assert apply(text, ops) == want, f"{argv} should give {apply(text, ops)}"


def test_each_operation_is_exercised_alone_together_and_not_at_all() -> None:
    """The point of the task is the fixed ordering rule. A check set that never
    combines flags cannot detect a script that applies them in argv order."""
    sets = [
        frozenset(a.lstrip("-") for a in argv if a.startswith("--") and a != "--input")
        for argv, _ in task()["checks"]
    ]
    assert frozenset() in sets, "no check runs with zero operations"
    assert any(len(s) == 1 for s in sets)
    assert any(len(s) == 2 for s in sets)
    assert frozenset({"reverse", "sort", "sha256"}) in sets


def test_no_check_reuses_the_input_shown_in_the_prompt() -> None:
    """A script that hardcodes the demonstrated case must fail."""
    demo = "hello"
    for argv, _ in task()["checks"]:
        assert argv[argv.index("--input") + 1] != demo


def test_no_expected_value_has_edge_whitespace() -> None:
    """The oracle strips stdout, so a check whose correct answer begins with a
    space would fail a correct implementation. `--input "hello world" --sort`
    is exactly that case and is deliberately absent."""
    for _, want in task()["checks"]:
        assert want == want.strip()


REFERENCE = """\
import argparse, hashlib
p = argparse.ArgumentParser()
p.add_argument("--input", required=True)
p.add_argument("--reverse", action="store_true")
p.add_argument("--sort", action="store_true")
p.add_argument("--sha256", action="store_true")
a = p.parse_args()
v = a.input
if a.reverse: v = v[::-1]
if a.sort:    v = "".join(sorted(v))
if a.sha256:  v = hashlib.sha256(v.encode()).hexdigest()
print(v)
"""


def test_a_correct_implementation_passes_the_oracle(tmp_path) -> None:
    (tmp_path / "transform.py").write_text(REFERENCE)
    passed, summary = run.script_checks(tmp_path, "transform.py", task()["checks"], 30)
    assert passed, summary


def test_argv_order_does_not_change_the_answer(tmp_path) -> None:
    """The fixed-order rule is the discriminating part of this task."""
    (tmp_path / "transform.py").write_text(REFERENCE)
    for flags in (
        ["--reverse", "--sort", "--sha256"],
        ["--sha256", "--sort", "--reverse"],
    ):
        passed, _ = run.script_checks(
            tmp_path,
            "transform.py",
            [
                [
                    ["--input", "Benchmarking", *flags],
                    apply("Benchmarking", [f.lstrip("-") for f in flags]),
                ]
            ],
            30,
        )
        assert passed


def test_an_implementation_that_applies_flags_in_argv_order_fails(tmp_path) -> None:
    """The control. If this passed, the task would not measure the rule it is
    built around."""
    (tmp_path / "transform.py").write_text("""\
import sys, hashlib
v = sys.argv[sys.argv.index("--input") + 1]
for a in sys.argv:
    if a == "--reverse": v = v[::-1]
    elif a == "--sort":  v = "".join(sorted(v))
    elif a == "--sha256": v = hashlib.sha256(v.encode()).hexdigest()
print(v)
""")
    passed, _ = run.script_checks(
        tmp_path,
        "transform.py",
        [
            [
                ["--input", "abc", "--sha256", "--reverse"],
                apply("abc", ["reverse", "sha256"]),
            ]
        ],
        30,
    )
    assert not passed, "argv-order implementation should fail the fixed-order rule"


def test_a_missing_script_is_reported_not_crashed(tmp_path) -> None:
    passed, summary = run.script_checks(tmp_path, "transform.py", task()["checks"], 30)
    assert not passed
    assert "never created" in summary


def test_the_oracle_still_accepts_a_bare_string_check(tmp_path) -> None:
    """script-reverse passes a single argument, not a list. Both shapes work."""
    (tmp_path / "rev.py").write_text("import sys; print(sys.argv[1][::-1])\n")
    passed, _ = run.script_checks(tmp_path, "rev.py", [["abc", "cba"]], 30)
    assert passed


def test_an_input_containing_a_space_stays_one_argument(tmp_path) -> None:
    """argv is a list, never shell-interpreted."""
    (tmp_path / "echo.py").write_text(
        "import sys; print(sys.argv[sys.argv.index('--input') + 1])\n"
    )
    passed, _ = run.script_checks(
        tmp_path, "echo.py", [[["--input", "hello world"], "hello world"]], 30
    )
    assert passed
