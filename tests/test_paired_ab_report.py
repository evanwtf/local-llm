"""The general paired A/B read-out (#240).

The void checks are the point. A ratio computed on rows that should never have
been pooled is worse than no ratio, because it is quotable and looks careful.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import paired_ab_report as report

ARMS = ("mtp", "plain")


def row(
    task: str,
    backend: str,
    wall: float | None,
    *,
    head: str = "aaaaaaa",
    dirty: bool = False,
    passed: bool = True,
    solution_empty: bool | None = None,
    excluded: bool = False,
    batch: str = "b",
) -> dict:
    return {
        "batch": batch,
        "task": task,
        "backend": backend,
        "wall_seconds": wall,
        "passed": passed,
        "solution_empty": solution_empty,
        "excluded": excluded,
        "env": {"harness_head": head, "harness_dirty": dirty},
    }


def ledger(tmp_path: pathlib.Path, rows: list[dict]) -> pathlib.Path:
    path = tmp_path / "results.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def balanced(n: int = 4, t: float = 90.0, c: float = 100.0, **kw) -> list[dict]:
    out = []
    for i in range(n):
        out.append(row(f"task{i}", "mtp", t, **kw))
        out.append(row(f"task{i}", "plain", c, **kw))
    return out


# --- the void checks ---------------------------------------------------------


def test_two_harness_heads_are_void():
    """Two heads are normally two harnesses. `stack_agent_report` already
    refuses this; the refusal is reproduced rather than re-invented."""
    rows = balanced(2) + [row("taskX", "mtp", 90.0, head="bbbbbbb")]
    lines, code = report.render(rows, ARMS)
    assert code == 2
    assert any("VOID" in line and "harness heads" in line for line in lines)


def test_an_arm_with_no_rows_is_void():
    rows = [row("task0", "mtp", 90.0), row("task1", "mtp", 91.0)]
    lines, code = report.render(rows, ARMS)
    assert code == 2
    assert any("VOID" in line for line in lines)


def test_no_paired_task_is_void():
    """Both arms present, but never on the same task -- there is nothing to
    compare, and a ratio of arm medians would hide that."""
    rows = [row("a", "mtp", 90.0), row("b", "plain", 100.0)]
    lines, code = report.render(rows, ARMS)
    assert code == 2
    assert any("no task has a wall-eligible trial in both arms" in x for x in lines)


# --- the check that 2026-09-08 needed and no read-out had --------------------


def test_a_dirty_split_across_the_arms_is_reported(caplog):
    """2026-09-08: reading `peer_status.py` wrote into the tree mid-run and
    `harness_dirty` flipped at exactly the treatment/control boundary -- 15
    clean treatment rows, then a dirty control arm. `harness_head` was
    identical, so the code provably did not change, and no existing read-out
    would have mentioned it."""
    rows = [row(f"t{i}", "mtp", 90.0, dirty=False) for i in range(3)]
    rows += [row(f"t{i}", "plain", 100.0, dirty=True) for i in range(3)]
    lines, code = report.render(rows, ARMS)
    assert code == 0, "an asymmetric flag is a warning, not a void"
    assert any("harness_dirty differs across the arms" in line for line in lines)


def test_no_note_when_both_arms_agree():
    lines, _ = report.render(balanced(3, dirty=True), ARMS)
    assert not any("harness_dirty differs" in line for line in lines)


# --- pairing and eligibility -------------------------------------------------


def test_a_task_missing_from_one_arm_is_dropped_and_named():
    """Silently dropping it would change which tasks the ratio covers without
    saying so."""
    rows = balanced(2) + [row("lonely", "mtp", 50.0)]
    lines, code = report.render(rows, ARMS)
    assert code == 0
    assert any("unpaired, dropped: lonely" in line for line in lines)


def test_a_turn_one_death_is_not_a_wall():
    """Its wall measures the harness giving up, not the model working."""
    assert not report.wall_eligible(row("t", "mtp", 4.0, solution_empty=True))
    assert report.wall_eligible(row("t", "mtp", 400.0, solution_empty=False))


def test_a_row_from_an_older_harness_still_counts():
    """`solution_empty` is None on rows predating the field. Dropping those
    would silently shrink an arm."""
    assert report.wall_eligible(row("t", "mtp", 90.0, solution_empty=None))


def test_a_failing_trial_still_contributes_its_wall():
    """Wrong code and timeouts are included: their walls are long and real."""
    rows = [
        row("t", "mtp", 300.0, passed=False),
        row("t", "plain", 100.0, passed=True),
    ]
    paired, _ = report.pair(rows, ARMS)
    assert paired["t"] == (300.0, 100.0)


def test_excluded_rows_never_load(tmp_path):
    path = ledger(tmp_path, balanced(2) + [row("x", "mtp", 1.0, excluded=True)])
    assert all(not r["excluded"] for r in report.load(path, "b"))
    assert len(report.load(path, "b")) == 4


def test_only_the_named_batch_loads(tmp_path):
    path = ledger(tmp_path, balanced(2) + [row("x", "mtp", 1.0, batch="other")])
    assert len(report.load(path, "b")) == 4


def test_a_corrupt_line_is_skipped_rather_than_fatal(tmp_path):
    """The ledger is appended to by a live run; a half-written last line must
    not take down a read-out of the complete rows."""
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(row("t", "mtp", 90.0)) + "\n{ broken\n")
    assert len(report.load(path, "b")) == 1


# --- the statistics ----------------------------------------------------------


def test_the_ratio_is_treatment_over_control_and_below_one_means_faster():
    """A reader who gets this backwards reports a regression as a win."""
    lines, code = report.render(balanced(4, t=90.0, c=100.0), ARMS)
    assert code == 0
    assert any("median wall ratio 0.900" in line for line in lines)
    assert any("4 of 4 favor mtp" in line for line in lines)


def test_a_slower_treatment_reads_above_one():
    lines, _ = report.render(balanced(4, t=110.0, c=100.0), ARMS)
    assert any("median wall ratio 1.100" in line for line in lines)
    assert any("0 of 4 favor mtp" in line for line in lines)


def test_the_bootstrap_is_seeded_so_a_read_out_is_reproducible():
    """Two people running this on the same ledger must quote the same
    interval, or the interval is not evidence of anything."""
    ratios = [0.8, 0.9, 1.0, 1.1, 1.2, 0.95]
    assert report.ratio_ci(ratios) == report.ratio_ci(ratios)


def test_one_run_is_never_presented_as_a_conclusion():
    """This project's minimum is three datapoints, and a read-out that reads
    like a verdict is how one run becomes a published claim."""
    lines, _ = report.render(balanced(3), ARMS)
    assert any("One run is not three" in line for line in lines)


def test_main_refuses_an_empty_batch(tmp_path):
    path = ledger(tmp_path, balanced(2))
    assert (
        report.main(
            [
                "--batch",
                "nope",
                "--treatment",
                "mtp",
                "--control",
                "plain",
                "--ledger",
                str(path),
            ]
        )
        == 2
    )


def test_main_reads_out_a_healthy_run(tmp_path):
    path = ledger(tmp_path, balanced(4))
    assert (
        report.main(
            [
                "--batch",
                "b",
                "--treatment",
                "mtp",
                "--control",
                "plain",
                "--ledger",
                str(path),
            ]
        )
        == 0
    )
