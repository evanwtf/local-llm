"""The generated tables name the engine build behind a row (#192).

`engine_caveat` is the read-out side of the engine identity recorded on each
row: when a table's rows span more than one engine build, the reader must be
told, the way `client_caveat` names a client split (#137).
"""

from __future__ import annotations

import json

import gen_tables


def _trial(backend: str, wall: float = 100.0, passed: bool = True) -> dict:
    # valid_opencode() keeps only rows recorded after the --dir fix, so a
    # synthetic trial needs a harness head from that range or it is filtered out.
    return {
        "env": {"harness_head": min(gen_tables._after_fix())},
        "backend": backend,
        "client": "opencode",
        "client_version": "1.18.29",
        "task": "mbox-scan",
        "wall_seconds": wall,
        "output_tokens": 1000,
        "passed": passed,
    }


def test_machine_section_heads_with_the_machine_name_and_its_fingerprint(tmp_path):
    """The heading records which machine produced the rows (#292)."""
    path = tmp_path / "results.jsonl"
    path.write_text("")
    out = "\n".join(gen_tables.machine_section("MacA", path, [_trial("qwen")]))
    assert out.startswith("### MacA\n")
    assert "`hardware/MacA/results.jsonl`" in out


def test_a_machine_with_no_opencode_trials_gets_a_note_not_an_empty_table(tmp_path):
    """A ledger of non-OpenCode rows must not render a header-only table."""
    path = tmp_path / "results.jsonl"
    path.write_text("")
    rows = [{"backend": "x", "client": "aider", "passed": True}]
    out = "\n".join(gen_tables.machine_section("Ryzen", path, rows))
    assert "No OpenCode trials" in out
    assert "| stack | passed |" not in out


def test_the_two_engine_table_is_suppressed_when_the_pair_is_absent(tmp_path):
    """The llama.cpp-vs-LM-Studio table is Mac-only; other machines skip it."""
    path = tmp_path / "results.jsonl"
    path.write_text("")
    # Neither qwen38fnq3 nor qwen38fnq3lms present -> no two-engine subsection.
    out = "\n".join(gen_tables.machine_section("Ryzen", path, [_trial("dtgemma412b")]))
    assert "Same weights, two engines" not in out
    # The other two subsections still render.
    assert "Every stack measured under OpenCode" in out
    assert "How fast each stack actually serves tokens" in out


def test_render_emits_one_section_per_ledger_in_directory_order(tmp_path, monkeypatch):
    """render() walks every committed ledger, sorted, not just this machine's."""
    a = tmp_path / "Aaa" / "results.jsonl"
    b = tmp_path / "Zzz" / "results.jsonl"
    for p in (b, a):  # create out of order; render must still sort
        p.parent.mkdir(parents=True)
        p.write_text("")
    monkeypatch.setattr(gen_tables, "ledgers", lambda: [a, b])
    monkeypatch.setattr(gen_tables.results, "trials", lambda path: [_trial("qwen")])
    text = gen_tables.render()
    assert text.index("### Aaa") < text.index("### Zzz")


def test_ledgers_globs_the_committed_hardware_directories():
    """The real fleet: at least the two machines with committed rows (#292)."""
    found = {p.parent.name for p in gen_tables.ledgers()}
    assert "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A" in found
    assert all(p.exists() for p in gen_tables.ledgers())


def _row(backend: str, engine: str, version: str) -> dict:
    return {
        "backend": backend,
        "client_version": "1.18.27",
        "servers": {backend: {"engine_name": engine, "engine_version": version}},
    }


def test_one_engine_build_means_no_caveat():
    rows = [_row("a", "ds4", "ffd85d42"), _row("b", "ds4", "ffd85d42")]
    assert gen_tables.engine_caveat(rows) == []


def test_two_engine_builds_are_named():
    rows = [_row("a", "ds4", "ffd85d42"), _row("b", "llama.cpp", "2092353c8")]
    out = gen_tables.engine_caveat(rows)
    assert len(out) == 2
    assert "not all taken under one engine build" in out[1]
    assert "ds4 ffd85d42" in out[1]
    assert "llama.cpp 2092353c8" in out[1]


def test_a_row_with_no_engine_version_is_ignored():
    rows = [_row("a", "ds4", "ffd85d42"), {"backend": "b", "servers": {}}]
    assert gen_tables.engine_caveat(rows) == []


def test_no_mlxserve_row_means_no_pld_caveat():
    """Self-retiring, like client_caveat: no mlx-serve row, no note (#262)."""
    rows = [_row("qwen38fnds4kimat", "ds4", "ffd85d42")]
    assert gen_tables.pld_caveat(rows) == []


def test_an_mlxserve_row_earns_the_pld_caveat():
    rows = [
        _row("qwen38fnds4kimat", "ds4", "ffd85d42"),
        _row("qwen38fnmlxserve", "mlx-serve", "26.9.2"),
        _row("qwen38fnmlxserve-git", "mlx-serve", "26.9.2"),
    ]
    out = gen_tables.pld_caveat(rows)
    assert len(out) == 2
    assert "PLD-on" in out[1]
    # Names each mlx-serve backend, and no ds4 backend.
    assert "qwen38fnmlxserve" in out[1] and "qwen38fnmlxserve-git" in out[1]
    assert "qwen38fnds4kimat" not in out[1]


def test_the_pld_caveat_names_a_grafted_mtp_head_where_one_ran():
    """#479: mlx-serve grafts an MTP head onto the Bonsai 2 pack, so "the pack
    ships no MTP head" is false for those rows and must not be said of them."""
    rows = [
        _row("qwen38fnmlxserve", "mlx-serve", "26.9.2"),
        _row("bonsai2mlxserve", "mlx-serve", "6dea424"),
    ]
    note = gen_tables.pld_caveat(rows)[1]
    assert "grafts an MTP head" in note
    assert "`bonsai2mlxserve`" in note.split("grafts an MTP head")[0].rsplit(".", 1)[-1]
    assert "the pack ships no MTP head" not in note


def test_the_pld_caveat_says_nothing_of_a_graft_when_none_ran():
    rows = [_row("qwen38fnmlxserve", "mlx-serve", "26.9.2")]
    assert "graft" not in gen_tables.pld_caveat(rows)[1]


def test_replay_rows_get_their_own_table_and_leave_the_excision_table_alone(tmp_path):
    def row(task, secs, **kw):
        return {
            "task": task,
            "backend": "b",
            "client": "opencode",
            "passed": True,
            "wall_seconds": secs,
            "timestamp": "2026-09-24T10:00:00-0400",
            "env": {"harness_head": min(gen_tables._after_fix())},
            **kw,
        }

    rows = [row("mbox-scan", 40.0), row("mbox-scan", 60.0)]
    rows += [row("replay-defang", 500.0, task_kind="replay")]
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    text = "\n".join(gen_tables.machine_section("M", path, rows))
    excision, replay = text.split("#### Replay tasks")
    # the excision table's worst is the excision worst, not the replay trial
    assert "| 2/2 | 50s | 60s |" in excision
    assert "| 1/1 | 500s | 500s |" in replay


def test_harder_replay_rows_get_a_table_with_a_hidden_column(tmp_path):
    """#726: held-out verdicts sit beside the visible ones, in their own table,
    and the #714 table is not moved by them."""

    def row(task, secs, **kw):
        return {
            "task": task,
            "backend": "b",
            "client": "opencode",
            "passed": True,
            "touched_tests": False,
            "wall_seconds": secs,
            "timestamp": "2026-09-24T10:00:00-0400",
            "env": {"harness_head": min(gen_tables._after_fix())},
            "task_kind": "replay",
            **kw,
        }

    hard = {"replay": {"suite": "hard"}, "hidden": {"tests": ["tests/t.py::x"]}}
    rows = [
        row("replay-defang", 500.0),
        # visible pass, hidden fail: the case these tasks exist to catch
        row("replay-defang-hidden", 300.0, hidden_passed=False, **hard),
        row("replay-defang-hidden", 320.0, hidden_passed=True, **hard),
        # a span with nothing held out still belongs to the harder table
        row("replay-span-x", 900.0, replay={"suite": "hard"}),
    ]
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    text = "\n".join(gen_tables.machine_section("M", path, rows))
    first, harder = text.split("#### Harder replay tasks")
    assert "| 1/1 | 500s | 500s |" in first.split("#### Replay tasks")[1]
    assert "| stack | passed | hidden passed | median |" in harder
    # three trials pass visibly; two held tests out, and one of those passed
    assert "| 3/3 | 1/2 | 320s | 900s |" in harder


def test_the_hidden_cell_is_a_dash_when_nothing_was_held_out():
    assert gen_tables._hidden_cell([{"passed": True}]) == "—"


# --- a guard failure is a failure, whatever `passed` says (review) -----------


def test_a_pass_that_edited_the_tests_is_not_counted_as_a_pass():
    """`passed` is the raw oracle. `results.verdict()` adds the guards.

    A trial that edited the tests reads `passed: true` and has no verdict
    worth the name. The table counted it 1/1 and timed it as a pass.
    """
    rigged = _trial("qwen") | {"touched_tests": True}
    out = "\n".join(gen_tables.stack_table([rigged], {}))
    assert "| qwen | 0/1 | — | — | — | — | — |" in out


def test_every_guard_fails_the_timing_as_well_as_the_count():
    rows = [
        _trial("qwen", wall=50.0),
        _trial("qwen", wall=10.0) | {"source_repo_intact": False},
        _trial("qwen", wall=20.0) | {"control_fails_as_expected": False},
    ]
    out = "\n".join(gen_tables.stack_table(rows, {}))
    assert "| qwen | 1/3 | 50s | 50s | \u2014 | \u2014 | 1.0x |" in out


# --- three spread measures, each named (review finding 9; owner, 2026-10-05) --

#: Three tasks, two passes each: within-task ratios 2.0, 1.5 and 4.0. The
#: slowest pass of any task over the fastest of any task is 1500 / 10.
THREE_TASKS = [
    ("mbox-scan", 10.0),
    ("mbox-scan", 20.0),
    ("parser-date", 1000.0),
    ("parser-date", 1500.0),
    ("storage-blob-put", 100.0),
    ("storage-blob-put", 400.0),
]


def _tasks(pairs):
    return [_trial("qwen", wall=w) | {"task": t} for t, w in pairs]


def test_worst_task_spread_is_the_largest_within_task_ratio():
    """How bad one task can get: slowest / fastest pass of the same task,
    the largest over the tasks with two passes."""
    assert gen_tables.spread_worst(_tasks(THREE_TASKS)) == 4.0


def test_typical_task_spread_is_the_median_within_task_ratio():
    """How consistent the stack usually is: the median of the same ratios."""
    assert gen_tables.spread_typical(_tasks(THREE_TASKS)) == 2.0
    two = [("mbox-scan", 10.0), ("mbox-scan", 20.0)]
    two += [("parser-date", 100.0), ("parser-date", 400.0)]
    assert gen_tables.spread_typical(_tasks(two)) == 3.0


def test_range_is_slowest_pass_over_fastest_pass_across_tasks():
    """The figure the old `spread` column printed. It is mostly task mix: a
    10 s task beside a 1,500 s task reads 150x with little trial variation."""
    assert gen_tables.task_range(_tasks(THREE_TASKS)) == 150.0


def test_both_spreads_are_a_dash_without_two_passes_on_one_task():
    one_each = _tasks([("mbox-scan", 10.0), ("parser-date", 1000.0)])
    assert gen_tables.spread_worst(one_each) is None
    assert gen_tables.spread_typical(one_each) is None
    out = "\n".join(gen_tables.stack_table(one_each, {}))
    assert "| qwen | 2/2 | 505s | 1000s | — | — | 100.0x |" in out


def test_the_range_is_a_dash_without_a_pass():
    assert gen_tables.task_range([_trial("qwen", passed=False)]) is None


def test_failures_never_enter_a_spread():
    rows = _tasks(THREE_TASKS) + [
        _trial("qwen", wall=5000.0, passed=False) | {"task": "mbox-scan"}
    ]
    assert gen_tables.spread_worst(rows) == 4.0
    assert gen_tables.task_range(rows) == 150.0


def test_the_table_prints_all_three_in_named_columns():
    out = gen_tables.stack_table(_tasks(THREE_TASKS), {})
    assert out[0] == (
        "| stack | passed | median | worst | spread (worst task) "
        "| spread (typical task) | range (all tasks) |"
    )
    assert "| qwen | 6/6 | 250s | 1500s | 4.0x | 2.0x | 150.0x |" in out


def test_every_stack_table_is_followed_by_the_three_definitions(tmp_path):
    """Each generated table explains its own columns; the old caption, "Spread
    is worst / best on the same task", described none of them correctly."""
    path = tmp_path / "results.jsonl"
    path.write_text("")
    rows = _tasks(THREE_TASKS)
    rows += [r | {"task": "replay-defang", "task_kind": "replay"} for r in rows[:2]]
    rows += [
        r | {"task": "replay-web-auth-hidden", "replay": {"suite": "hard"}}
        for r in rows[:2]
    ]
    text = "\n".join(gen_tables.machine_section("Ryzen", path, rows))
    assert text.count("**spread (worst task)**") == 3
    assert text.count("**spread (typical task)**") == 3
    assert text.count("**range (all tasks)**") == 3
    assert "Spread is worst / best on the same task" not in text
