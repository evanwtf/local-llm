"""The generated tables name the engine build behind a row (#192).

`engine_caveat` is the read-out side of the engine identity recorded on each
row: when a table's rows span more than one engine build, the reader must be
told, the way `client_caveat` names a client split (#137).
"""

from __future__ import annotations

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
