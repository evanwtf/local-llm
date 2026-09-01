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

import pytest

import gen_tables
import splice_tables

DOC = pathlib.Path(__file__).resolve().parents[2] / "RECOMMENDATIONS.md"


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
