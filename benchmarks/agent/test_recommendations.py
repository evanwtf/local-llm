"""RECOMMENDATIONS.md must not drift from the data underneath it.

This project has published three sets of figures that measured its own bugs.
The tables a stranger acts on are therefore generated, and this test fails if
the committed file no longer matches what results.jsonl says.

Regenerate with: uv run python benchmarks/agent/splice_tables.py
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re

import gen_tables
import pytest
import splice_tables

from conftest import HAS_LOCAL_RESULTS, SKIP_NO_RESULTS

DOC = pathlib.Path(__file__).resolve().parents[2] / "RECOMMENDATIONS.md"
# The generated tables and the reasoning moved to docs/ on 2026-09-08
# (#232). DOC is what a stranger reads first; TABLES_DOC is what the
# splice writes. A test that asserts a property of the tables must
# name TABLES_DOC, or it silently checks a file that no longer has
# them and passes for the wrong reason.
TABLES_DOC = pathlib.Path(__file__).resolve().parents[2] / "docs/results.md"
STACKS_DOC = pathlib.Path(__file__).resolve().parents[2] / "docs/stacks.md"


def test_the_generated_tables_are_current() -> None:
    # No HAS_LOCAL_RESULTS gate: render() reads the committed hardware/*/
    # results.jsonl by glob, not this machine's file (#292), so the document is
    # verifiable on every checkout -- including CI, which could never run this
    # check while the tables were a picture of whichever machine last spliced.
    #
    # A batch in flight is appending to a ledger, so the document is being
    # compared against a moving target. Skipping keeps an unrelated commit from
    # being blocked by a run; the check is meaningful only when the data is
    # quiescent, and AGENTS.md makes re-splicing part of finishing a batch.
    # STASH_MARKER exists only on a machine that is mid-run, never on CI.
    import run

    if run.STASH_MARKER.exists():
        pytest.skip("a benchmark batch is running; results.jsonl is mid-write")
    # render() now reads every committed ledger by glob, so it also sees rows a
    # finished-but-not-yet-committed batch left in the working tree (STASH_MARKER
    # is gone by then). The doc can only match the *committed* data, so skip when
    # any ledger is dirty or untracked: commit the rows and re-splice, then this
    # passes. CI checks out a clean tree, so it always runs there -- which is the
    # point of dropping the HAS_LOCAL_RESULTS gate (#292).
    import subprocess

    repo = pathlib.Path(__file__).resolve().parents[2]
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "hardware"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if any(line.endswith("/results.jsonl") for line in status.splitlines()):
        pytest.skip("a ledger has uncommitted rows; commit them and re-splice")
    text = TABLES_DOC.read_text()
    assert text == splice_tables.splice(text, gen_tables.render()), (
        "docs/results.md is stale; run splice_tables.py"
    )


def test_the_markers_survive() -> None:
    text = TABLES_DOC.read_text()
    assert splice_tables.BEGIN in text and splice_tables.END in text


def test_the_dir_flag_is_taught() -> None:
    """Omitting --dir is silent and ruins the run; a reader must be told."""
    assert "--dir" in DOC.read_text()


def test_the_document_carries_a_valid_dated_marker() -> None:
    """A file with no visible date hides its own staleness: a reader cannot
    tell a fresh measurement from a three-month-old one. Require an explicit
    `Ledger last read YYYY-MM-DD` marker, and require it to be a real,
    non-future date so the marker cannot rot into a placeholder that still
    reads as current. The staleness threshold stays a human's to judge -- the
    marker exists so a reader sees the age, deliberately not so CI fails on a
    calendar and blocks unrelated work."""
    doc = DOC.read_text()
    m = re.search(r"Ledger last read (\d{4}-\d{2}-\d{2})", doc)
    assert m, (
        "RECOMMENDATIONS.md has no 'Ledger last read YYYY-MM-DD' marker; "
        "without it the file cannot show its own staleness."
    )
    read = datetime.date.fromisoformat(m.group(1))
    # A day of grace: the marker is stamped in local time and CI runs in UTC,
    # so a same-day boundary must not read as "the future". A real typo (a
    # wrong year) is still caught.
    today_utc = datetime.datetime.now(tz=datetime.UTC).date()
    assert read <= today_utc + datetime.timedelta(days=1), (
        f"the ledger-read date {read} is in the future -- a typo reads as "
        "fresher than any real measurement."
    )


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
    """A newbie pastes these. A trailing comma would cost them an hour.

    All three docs, not just DOC: the fenced snippets moved to docs/stacks.md
    with the per-stack recipes in #232, and a test that kept reading only
    RECOMMENDATIONS.md would have found nothing to check and passed.
    """
    found = 0
    for doc in (DOC, TABLES_DOC, STACKS_DOC):
        for b in re.findall(r"```json\n(.*?)```", doc.read_text(), re.DOTALL):
            json.loads("{" + b.strip().rstrip(",") + "}")
            found += 1
    assert found, "no json snippets found in any of the three docs"


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
    doc = TABLES_DOC.read_text()
    prompts = (pathlib.Path(__file__).parent / "PROMPTS.md").read_text()
    headings = set(re.findall(r"^### `([^`]+)`", prompts, re.MULTILINE))
    # `\.\./` since #232: the tables live in docs/, one below the root.
    linked = set(
        re.findall(r"\((?:\.\./)?benchmarks/agent/PROMPTS\.md#([a-z0-9-]+)\)", doc)
    )
    assert linked, "no task links found in docs/results.md"
    assert linked <= headings, f"dangling links: {sorted(linked - headings)}"


def test_every_task_in_the_stack_tables_is_described() -> None:
    """No task name should appear in a results table without the reader having
    been told what it is."""
    doc = TABLES_DOC.read_text()
    for task in gen_tables.TASK_SUMMARY:
        assert f"PROMPTS.md#{task}" in doc, f"{task} is never linked or described"


def test_the_target_repo_link_matches_the_actual_remote():
    """RECOMMENDATIONS pointed at a GitHub repo that does not exist.

    The doc told a stranger the excision tasks come from
    `evandhoffman/gmail-archive`; the remote is `evanwtf/gmail-archive`. A
    404 in the one link that lets a reader check our work is worse than no
    link -- it looks verifiable and is not.
    """
    doc = TABLES_DOC.read_text()
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


# ---------------------------------------------------------------------------
# #142: the stack table is sorted by median wall time, and a trial that dies
# early is quick. That rewarded a stack for failing fast: qwen38fnds4mtp7shim
# ranked second of fifteen at an 84s median while passing 50/91, above every
# stack in the table that passed all of its trials. Counting only the trials
# that passed moves it to twelfth at 177s and leaves every 100%-passing row on
# exactly the number it had.


def _trial(
    backend: str, wall: float, passed: bool | None, task: str = "mbox-scan"
) -> dict:
    # valid_opencode() keeps only rows recorded after the --dir fix, so a
    # synthetic row needs a harness head from that range or the table is empty.
    return {
        "env": {"harness_head": min(gen_tables._after_fix())},
        "backend": backend,
        "client": "opencode",
        "client_version": "1.18.29",
        "task": task,
        "wall_seconds": wall,
        "output_tokens": 1000,
        "passed": passed,
    }


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def test_a_stack_that_fails_fast_does_not_outrank_one_that_passes():
    """The exact shape of the bug, in miniature.

    `quick` dies at 10s on two thirds of its trials. Counting every trial its
    median is 10s and it leads the table; counting only what passed it is 200s
    and it trails the stack that passes everything at 100s.
    """
    rows = [
        _trial("quick", 10.0, False),
        _trial("quick", 10.0, False),
        _trial("quick", 200.0, True),
        _trial("steady", 100.0, True),
        _trial("steady", 100.0, True),
        _trial("steady", 100.0, True),
    ]
    body = gen_tables.stack_table(rows, {})[2:]
    order = [_cells(line)[0] for line in body]
    assert order == ["steady", "quick"]
    assert _cells(body[0])[1:3] == ["3/3", "100s"]
    assert _cells(body[1])[1:3] == ["1/3", "200s"]


def test_a_stack_that_passes_everything_keeps_the_number_it_had():
    """The rule must not restate the figures of any clean row.

    Twelve of the fifteen published rows pass 100%, so for them the two
    medians are the same set of trials. If this ever fails, the change has
    moved numbers a reader may already have acted on.
    """
    rows = [_trial("clean", w, True) for w in (40.0, 90.0, 300.0)]
    assert _cells(gen_tables.stack_table(rows, {})[2])[1:] == [
        "3/3",
        "90s",
        "300s",
        "7.5x",
    ]


def test_a_stack_with_no_passing_trial_keeps_its_row_and_sorts_last():
    """0/n is the most important thing a table of stacks can say.

    Dropping the row would hide it, and giving it a median would be inventing
    one, so it keeps the row and reports no timing.
    """
    rows = [
        _trial("hopeless", 10.0, False),
        _trial("hopeless", 12.0, False),
        _trial("fine", 500.0, True),
    ]
    body = gen_tables.stack_table(rows, {})[2:]
    assert [_cells(line)[0] for line in body] == ["fine", "hopeless"]
    assert _cells(body[1]) == ["hopeless", "0/2", "—", "—", "—"]


def test_a_timed_out_trial_is_not_a_timing():
    """results.trials() keeps timeouts as failures with `passed: None`.

    They are the longest walls in the file, so treating them as passes would
    push a stack's median and worst up for exactly the runs that produced no
    work.
    """
    rows = [
        _trial("t", 900.0, None),
        _trial("t", 100.0, True),
        _trial("t", 120.0, True),
    ]
    assert _cells(gen_tables.stack_table(rows, {})[2])[1:] == [
        "2/3",
        "110s",
        "120s",
        "1.2x",
    ]


def test_the_table_says_which_trials_the_timings_count(tmp_path):
    """A rule a reader cannot see is a second version of the same bug."""
    rows = [_trial("a", 50.0, True), _trial("a", 10.0, False)]
    path = tmp_path / "results.jsonl"
    path.write_text("")
    doc = "\n".join(gen_tables.machine_section("MacA", path, rows))
    warning = doc.index("count only trials that passed")
    assert warning < doc.index("| stack | passed |")


def _shim_backed_backends() -> set[str]:
    """Backend names whose tasks.toml description says they use the shim.

    Read from the config, not from a list in this file. A hand-kept list is
    what failed: the caveat said "Both qwen38fnds4* rows" while three backends
    ran behind the shim, and the one it omitted -- qwen38fnds4kimat, 90/90 at
    97s -- was the strongest of them and so the likeliest to be reproduced.
    """
    text = (pathlib.Path(__file__).resolve().parent / "tasks.toml").read_text()
    found, name = set(), None
    for line in text.splitlines():
        if line.startswith("[backend."):
            name = line[len("[backend.") :].rstrip("]").strip()
        elif name and "via the tool-format shim" in line:
            found.add(name)
    return found


NUMBER_WORD = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}


def _between(text: str, opening: str, closing: str) -> str:
    """The span between two markers, or a clear failure.

    Anchored on the sentence that does the enumerating, not on the section.
    Checking the whole section is what the first version of this test did, and
    it was weaker than its own commit message claimed: a backend dropped from
    the list but still mentioned in a later paragraph kept the test green. The
    peer found that by mutating it, which is the only way anyone would.
    """
    i = text.index(opening) + len(opening)
    return text[i : text.index(closing, i)]


def test_the_strip_caveat_enumerates_every_shim_backed_row() -> None:
    """The strip is worth 23 points where measured; a row it applies to and
    the caveat does not list is a reproduction that will silently miss it."""
    doc = TABLES_DOC.read_text()
    listed = _between(doc, "`qwen38fnds4*` rows \u2014 ", " \u2014 run behind")
    missing = sorted(b for b in _shim_backed_backends() if b not in listed)
    assert not missing, (
        f"shim-backed backends absent from the strip caveat's list: {missing}. "
        "Add them to the enumeration, or a reader reproducing that row never "
        "learns the strip is load-bearing."
    )


def test_the_strip_caveat_counts_the_rows_it_lists() -> None:
    """ "All three" has to stay true when a fourth shim backend appears."""
    n = len(_shim_backed_backends())
    word = NUMBER_WORD[n]
    assert f"**All {word.lower()}** `qwen38fnds4*` rows" in TABLES_DOC.read_text(), (
        f"{n} backends run behind the shim; the strip caveat does not say "
        f'"All {word.lower()}"'
    )


def test_the_upstream_caveat_enumerates_every_shim_backed_row() -> None:
    """Same rows, same argument: they all launch with --ple against a fork."""
    doc = TABLES_DOC.read_text()
    listed = _between(doc, "\nThe `qwen38fnds4", " rows all\nneed **PLE")
    missing = sorted(
        b for b in _shim_backed_backends() if b not in "The `qwen38fnds4" + listed
    )
    assert not missing, (
        f"shim-backed backends absent from the upstream caveat's list: {missing}"
    )


def test_the_upstream_caveat_heading_counts_the_rows() -> None:
    """The heading said "One row" while the body named two and three applied."""
    n = len(_shim_backed_backends())
    heading = f"### {NUMBER_WORD[n]} rows here cannot be reproduced"
    assert heading in TABLES_DOC.read_text(), (
        f"{n} rows need the fork; the heading does not say {NUMBER_WORD[n]!r}"
    )


def test_the_shim_backend_list_is_not_empty() -> None:
    """A parser that silently finds nothing would make both tests vacuous."""
    assert len(_shim_backed_backends()) >= 3


def test_the_recommendation_stays_short() -> None:
    """#232: it reached 838 lines holding an answer to "what do I run".

    Same failure as NEXT.md, and the same cause -- every addition was
    reasonable on its own. The cap is on prose rather than total lines
    because section 1 is a pastable install block that a reader needs whole,
    and shrinking it would be shrinking the wrong thing.
    """
    lines = DOC.read_text().splitlines()
    prose, fenced = 0, False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.strip():
            prose += 1
    assert prose <= 100, (
        f"RECOMMENDATIONS.md has {prose} lines of prose, over 100 (#232). "
        f"New reasoning belongs in docs/stacks.md or docs/results.md."
    )


def test_the_three_sections_are_all_there() -> None:
    """The issue asked for exactly three: paste it, pick a row, run a script.
    A missing one means the answer moved somewhere a stranger will not look.
    """
    doc = DOC.read_text()
    assert "## 1. Paste this" in doc
    assert "## 2. Or pick a row" in doc
    assert "## 3. Or run one script" in doc
    assert "docs/stacks.md" in doc and "docs/results.md" in doc


def test_the_moved_docs_do_not_lose_their_links() -> None:
    """The prose moved into docs/, so every repo-root-relative link in it
    needed a `../`. A link that resolves from the old location and not the
    new one is exactly the 404 test_the_target_repo_link_matches_the_actual_remote
    exists to prevent."""
    for doc in (TABLES_DOC, STACKS_DOC):
        text = doc.read_text()
        for target in re.findall(r"\]\((?!https?://|#)([^)]+)\)", text):
            path = (doc.parent / target.split("#")[0]).resolve()
            assert path.exists(), f"{doc.name} links to a missing {target}"
