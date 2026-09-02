"""Generate the measured tables in RECOMMENDATIONS.md from results.jsonl.

A recommendation is only as good as the numbers under it, and this project has
now published three sets of figures that measured its own bugs. So the tables a
reader acts on are derived, never transcribed:

    uv run python gen_tables.py

The output is spliced into RECOMMENDATIONS.md between the BEGIN/END markers by
`splice_tables.py`, and `test_recommendations.py` fails if the file drifts.

Only post-`--dir` OpenCode rows are counted; everything before
2026-08-31T21:47:18-04:00 measured a harness bug. See
docs/archive/results-opencode-pre-dir.md.
"""

from __future__ import annotations

import collections
import pathlib
import statistics
import subprocess
from typing import Any

import provenance
import results

HERE = pathlib.Path(__file__).parent
FIX = "7356460"

# RECOMMENDATIONS.md sits at the repo root; PROMPTS.md publishes the exact text
# of every task, generated from tasks.toml. A reader meeting "mbox-scan" for
# the first time needs one line here and the prompt itself one click away.
PROMPTS = "benchmarks/agent/PROMPTS.md"

TASK_SUMMARY = {
    "mbox-strip-envelope": "implement `strip_envelope` in an mbox parser",
    "parser-mbox-quoting": "implement `unquote_mbox`, round-tripping with `requote_mbox`",
    "storage-blob-put": "implement `BlobStore.put`",
    "parser-date": "implement `_date`, an email date parser",
    "mbox-scan": "implement `scan`, which walks an mbox file",
    "script-reverse": "write `reverse.py` from nothing: read argv, print reversed",
    "script-transform": "write `transform.py`: `--input` plus three composable flags",
}


def _after_fix() -> set[str]:
    out = subprocess.run(
        ["git", "log", "--format=%h", f"{FIX}~1..HEAD"],
        capture_output=True,
        text=True,
        cwd=HERE,
        check=False,
    ).stdout.split()
    heads = {c[:7] for c in out}
    if not heads:
        raise SystemExit("cannot resolve the --dir fix commit; refusing to guess")
    return heads


def load(path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Real trials only.

    This used to filter on `is_excluded()` alone, which lets through every row
    that is not a trial at all -- `--dry-run` control checks, which carry
    `passed: None`. 127 of them reached the published tables, counted in the
    denominator and never in the numerator, so RECOMMENDATIONS showed `gemma4`
    as **12/14** when it went 12/12 and `gemma426` as **11/12** when it went
    11/11. Every one of those "failures" was a control check the harness ran on
    purpose.

    `results.trials()` is exactly this filter and says so in its docstring --
    "usable rows, minus dry runs", "do not test row['passed'] directly". Not
    calling it is the same mistake as `dirfix.py` hand-rolling `r.get(
    "excluded")`, which RESULTS.md already records having miscounted 14 rows.
    """
    return results.trials(path or results.default_path())


def valid_opencode(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after = _after_fix()
    return [
        r
        for r in rows
        if r.get("client") == "opencode"
        and str(r.get("env", {}).get("harness_head", ""))[:7] in after
    ]


def _excision(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if not str(r.get("task", "")).startswith("script-")]


def stack_table(rows: list[dict[str, Any]], labels: dict[str, str]) -> list[str]:
    """Pass rate and wall time per backend, OpenCode only."""
    by = collections.defaultdict(list)
    for r in valid_opencode(rows):
        by[r["backend"]].append(r)
    out = [
        "| stack | passed | median | worst | spread |",
        "|---|---|---|---|---|",
    ]
    rank = sorted(
        by.items(),
        key=lambda kv: statistics.median(
            [x["wall_seconds"] for x in _excision(kv[1]) if x.get("wall_seconds")]
            or [1e9]
        ),
    )
    for name, rs in rank:
        ex = [x for x in _excision(rs) if x.get("wall_seconds")]
        if not ex:
            continue
        w = [x["wall_seconds"] for x in ex]
        p = sum(1 for x in rs if x.get("passed"))
        out.append(
            f"| {labels.get(name, name)} | {p}/{len(rs)} | "
            f"{statistics.median(w):.0f}s | {max(w):.0f}s | {max(w) / min(w):.1f}x |"
        )
    return out


def engine_table(rows: list[dict[str, Any]], a: str, b: str) -> list[str]:
    """Identical weights, two engines, per task."""
    by = collections.defaultdict(dict)
    for r in valid_opencode(rows):
        if r["backend"] in (a, b) and r.get("wall_seconds"):
            by[r["task"]].setdefault(r["backend"], []).append(r["wall_seconds"])
    out = ["| task | what it asks for | llama.cpp | LM Studio |", "|---|---|---|---|"]
    for task in sorted(by):
        cell = by[task]
        if a not in cell or b not in cell:
            continue
        out.append(
            f"| [`{task}`]({PROMPTS}#{task}) | {TASK_SUMMARY.get(task, '')} | "
            f"{statistics.median(cell[a]):.0f}s | "
            f"{statistics.median(cell[b]):.0f}s |"
        )
    return out


def throughput_table(rows: list[dict[str, Any]], labels: dict[str, str]) -> list[str]:
    """Seconds per 1k output tokens -- the machine's contribution, isolated."""
    by = collections.defaultdict(list)
    for r in valid_opencode(rows):
        if r.get("wall_seconds") and r.get("output_tokens"):
            by[r["backend"]].append(r["wall_seconds"] / r["output_tokens"] * 1000)
    out = ["| stack | seconds per 1k output tokens |", "|---|---|"]
    for name, v in sorted(by.items(), key=lambda kv: statistics.median(kv[1])):
        out.append(f"| {labels.get(name, name)} | {statistics.median(v):.0f}s |")
    return out


LABELS = {
    "qwen38fnq3": "Qwen3.8-Flash-Next Q3 - llama.cpp",
    "ds4": "DeepSeek-V4-Flash - ds4",
    "ds4anthropic": "DeepSeek-V4-Flash - ds4 (Anthropic wire)",
    "qwen38fnq3lms": "Qwen3.8-Flash-Next Q3 - LM Studio",
    "qwen36coding": "Qwen3.6-27B-coding - Ollama",
    "glm53ds4": "GLM-5.3-Flash - ds4",
}


def render(rows: list[dict[str, Any]] | None = None) -> str:
    rows = load() if rows is None else rows
    out: list[str] = []
    # The data fingerprint, not the HEAD commit: these tables are a function of
    # results.jsonl, and stamping them with a commit that moves on every
    # unrelated edit would churn the document and train people to skim it.
    out += [
        f"*Generated from `results.jsonl` — "
        f"{provenance.fingerprint(results.default_path())}.*",
        "",
    ]
    out += ["#### Every stack measured under OpenCode", ""]
    out += stack_table(rows, LABELS)
    out += [
        "",
        (
            "Excision tasks only; `script-*` excluded because they are a "
            "different class. **Spread is worst / best on the same task**, "
            "and it is the column most people forget to ask for."
        ),
        "",
        "#### Same weights, two engines",
        "",
    ]
    out += engine_table(rows, "qwen38fnq3", "qwen38fnq3lms")
    out += [
        "",
        "#### How fast each stack actually serves tokens",
        "",
    ]
    out += throughput_table(rows, LABELS)
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(render(), end="")
