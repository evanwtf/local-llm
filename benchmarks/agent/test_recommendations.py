"""RECOMMENDATIONS.md must not drift from the data underneath it.

This project has published three sets of figures that measured its own bugs.
The tables a stranger acts on are therefore generated, and this test fails if
the committed file no longer matches what results.jsonl says.

Regenerate with: uv run python benchmarks/agent/splice_tables.py
"""

from __future__ import annotations

import json
import pathlib
import re

import gen_tables
import pytest
import splice_tables
from conftest import HAS_LOCAL_RESULTS, SKIP_NO_RESULTS

DOC = pathlib.Path(__file__).resolve().parents[2] / "RECOMMENDATIONS.md"


@pytest.mark.skipif(not HAS_LOCAL_RESULTS, reason=SKIP_NO_RESULTS)
def test_the_generated_tables_are_current() -> None:
    # A batch in flight is appending to results.jsonl, so the document is
    # being compared against a moving target. Skipping keeps an unrelated
    # commit from being blocked by a run; the check is meaningful only when
    # the data is quiescent, and AGENTS.md makes re-splicing part of finishing
    # a batch.
    import run

    if run.STASH_MARKER.exists():
        pytest.skip("a benchmark batch is running; results.jsonl is mid-write")
    text = DOC.read_text()
    assert text == splice_tables.splice(text, gen_tables.render()), (
        "RECOMMENDATIONS.md is stale; run splice_tables.py"
    )


def test_the_markers_survive() -> None:
    text = DOC.read_text()
    assert splice_tables.BEGIN in text and splice_tables.END in text


def test_the_dir_flag_is_taught() -> None:
    """Omitting --dir is silent and ruins the run; a reader must be told."""
    assert "--dir" in DOC.read_text()


@pytest.mark.skipif(not HAS_LOCAL_RESULTS, reason=SKIP_NO_RESULTS)
def test_no_pre_fix_opencode_data_is_quoted() -> None:
    """Every figure must come from valid rows. The generator filters to
    post-fix trials; this asserts the doc did not also inherit an old number."""
    rows = gen_tables.valid_opencode(gen_tables.load())
    assert rows, "no valid OpenCode rows to build recommendations from"
    assert all(r.get("client") == "opencode" for r in rows)


def test_every_recommended_stack_has_a_declared_opencode_model() -> None:
    """A stack we tell a stranger to run must resolve in the config we ship.
    An undeclared model exits in 0.6s with no error (#69)."""
    import tomllib

    import opencode_config

    ref = pathlib.Path(__file__).resolve().parents[2] / "config/opencode.json"
    declared = opencode_config.declared_models(ref)
    with (pathlib.Path(__file__).parent / "tasks.toml").open("rb") as fh:
        backends = tomllib.load(fh)["backend"]
    for name in ("qwen38fnq3", "ds4", "qwen36coding"):
        model = backends[name]["opencode_model"]
        assert model in declared, f"{name} -> {model} is not in config/opencode.json"


def test_the_json_snippets_parse() -> None:
    """A newbie pastes these. A trailing comma would cost them an hour."""
    text = DOC.read_text()
    blocks = re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    assert blocks, "no json snippets found"
    for b in blocks:
        json.loads("{" + b.strip().rstrip(",") + "}")


def test_the_full_config_snippet_parses() -> None:
    """The heredoc in the quick start is a whole file, not a fragment."""
    text = DOC.read_text()
    m = re.search(
        r"cat > ~/\.config/opencode/opencode\.json <<'JSON'\n(.*?)\nJSON",
        text,
        re.DOTALL,
    )
    assert m, "quick-start config heredoc not found"
    cfg = json.loads(m.group(1))
    assert cfg["model"] in {
        f"{p}/{k}" for p, spec in cfg["provider"].items() for k in spec["models"]
    }, "the default model is not one this config declares"


def test_every_task_link_resolves_to_a_real_prompt_heading() -> None:
    """A reader meeting `mbox-scan` needs the prompt one click away, and a
    broken anchor is worse than no link: it looks authoritative and goes
    nowhere. PROMPTS.md is generated, so its headings move when tasks change.
    """
    doc = DOC.read_text()
    prompts = (pathlib.Path(__file__).parent / "PROMPTS.md").read_text()
    headings = set(re.findall(r"^### `([^`]+)`", prompts, re.MULTILINE))
    linked = set(re.findall(r"\(benchmarks/agent/PROMPTS\.md#([a-z0-9-]+)\)", doc))
    assert linked, "no task links found in RECOMMENDATIONS.md"
    assert linked <= headings, f"dangling links: {sorted(linked - headings)}"


def test_every_task_in_the_stack_tables_is_described() -> None:
    """No task name should appear in a results table without the reader having
    been told what it is."""
    doc = DOC.read_text()
    for task in gen_tables.TASK_SUMMARY:
        assert f"PROMPTS.md#{task}" in doc, f"{task} is never linked or described"


def test_the_target_repo_link_matches_the_actual_remote():
    """RECOMMENDATIONS pointed at a GitHub repo that does not exist.

    The doc told a stranger the excision tasks come from
    `evandhoffman/gmail-archive`; the remote is `evanwtf/gmail-archive`. A
    404 in the one link that lets a reader check our work is worse than no
    link -- it looks verifiable and is not.
    """
    doc = (
        pathlib.Path(__file__).resolve().parent.parent.parent / "RECOMMENDATIONS.md"
    ).read_text()
    assert "evanwtf/gmail-archive" in doc
    assert "evandhoffman/gmail-archive" not in doc


@pytest.mark.skipif(not HAS_LOCAL_RESULTS, reason=SKIP_NO_RESULTS)
def test_generated_tables_count_only_real_trials():
    """A --dry-run control check is not a failed trial.

    gen_tables.load() filtered on is_excluded() alone, which lets through every
    row that is not a trial: dry runs carry `passed: None`, so they landed in
    the denominator and never the numerator. 127 of them reached the published
    tables, showing gemma4 as 12/14 when it went 12/12.

    results.trials() is exactly this filter. Hand-rolling a narrower one is the
    same mistake dirfix.py made with r.get("excluded"), which RESULTS.md
    records having miscounted 14 rows.
    """
    import gen_tables

    rows = gen_tables.load()
    assert rows, "expected some trials"
    assert not [r for r in rows if r.get("dry_run")]

    # Timeouts DO belong here -- results.trials() keeps them deliberately
    # ("failures, not absences") and they carry `passed: None`, which is why
    # its docstring says not to test row["passed"] directly. The 13 such rows
    # are all timeouts, so their presence is correct and a dry run's is not.
    stray = [r for r in rows if r.get("passed") is None and r.get("error") != "timeout"]
    assert not stray, f"non-timeout rows with no verdict: {len(stray)}"


# ---------------------------------------------------------------------------
# #137: every OpenCode comparison across the ds4 backends spans a client
# boundary, and the published tables do not say so.


def _row(backend: str, version: str, wall: float = 100.0) -> dict:
    return {
        "backend": backend,
        "client": "opencode",
        "client_version": version,
        "task": "mbox-scan",
        "wall_seconds": wall,
        "output_tokens": 1000,
        "passed": True,
    }


def test_the_caveat_names_the_versions_and_which_backends_carry_them():
    import gen_tables

    got = "\n".join(
        gen_tables.client_caveat([_row("a", "1.18.25"), _row("b", "1.18.27")])
    )
    assert "1.18.25" in got and "1.18.27" in got
    assert "a" in got and "b" in got
    assert "#137" in got


def test_there_is_no_caveat_when_one_client_version_measured_everything():
    """The caveat must disappear on its own when the confound does."""
    import gen_tables

    assert gen_tables.client_caveat([_row("a", "1.18.25"), _row("b", "1.18.25")]) == []


def test_a_backend_whose_own_rows_span_versions_is_named_as_spanning():
    """A backend measured under both is a different problem from a split one."""
    import gen_tables

    got = "\n".join(
        gen_tables.client_caveat(
            [_row("a", "1.18.25"), _row("a", "1.18.27"), _row("b", "1.18.25")]
        )
    )
    assert "a" in got and "1.18.25, 1.18.27" in got


def test_rows_with_no_recorded_client_version_are_named_not_ignored():
    import gen_tables

    row = _row("c", "1.18.25")
    del row["client_version"]
    got = "\n".join(gen_tables.client_caveat([_row("a", "1.18.25"), row]))
    assert "unrecorded" in got


def test_the_published_tables_carry_the_caveat_while_the_split_stands():
    """The real data, not a fixture: this is what a reader actually sees."""
    import gen_tables

    text = gen_tables.render()
    versions = {
        r.get("client_version") for r in gen_tables.valid_opencode(gen_tables.load())
    }
    if len(versions) < 2:
        pytest.skip("one client version measured everything; nothing to caveat")
    assert text.count("#137") >= 2, "both generated tables need the caveat"
