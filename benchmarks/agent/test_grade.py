"""Tests for the secondary measurements taken around a trial.

These record what the oracle cannot: whether the solution is lint-clean, whether
it type-checks, and whether it is the original body reproduced from memory. None
of them may change a verdict -- the oracle stays binary and stays the authority.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import grade
import pytest
import results

ORIGINAL = "    return a + b\n"


@pytest.fixture
def worktree(tmp_path: pathlib.Path) -> pathlib.Path:
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "src" / "m.py").write_text(
        'def add(a, b):\n    """Sum."""\n    raise NotImplementedError("x")\n'
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=wt, check=True)
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"],
        cwd=wt,
        check=True,
    )
    return wt


def _solve(worktree: pathlib.Path, body: str) -> None:
    (worktree / "src" / "m.py").write_text(f'def add(a, b):\n    """Sum."""\n{body}')


# --- the recall detector -------------------------------------------------


def test_an_exact_reproduction_of_the_original_is_flagged(worktree):
    _solve(worktree, ORIGINAL)
    assert grade.restored_verbatim(worktree / "src/m.py", "add", ORIGINAL) is True


def test_whitespace_alone_does_not_make_it_a_different_solution(worktree):
    _solve(worktree, "    return  a + b\n")
    assert grade.restored_verbatim(worktree / "src/m.py", "add", ORIGINAL) is True


def test_a_genuinely_different_solution_is_not_flagged(worktree):
    _solve(worktree, "    total = a + b\n    return total\n")
    assert grade.restored_verbatim(worktree / "src/m.py", "add", ORIGINAL) is False


def test_an_unparseable_file_gives_no_answer_rather_than_raising(worktree):
    """The agent may leave the file broken. That is a failed trial, not a crash."""
    (worktree / "src" / "m.py").write_text("def add(a, b:\n")
    assert grade.restored_verbatim(worktree / "src/m.py", "add", ORIGINAL) is None


def test_a_still_stubbed_function_is_not_a_verbatim_restore(worktree):
    assert grade.restored_verbatim(worktree / "src/m.py", "add", ORIGINAL) is False


# --- the saved artifact --------------------------------------------------


def test_the_solution_patch_is_written_and_hashed(worktree, tmp_path):
    _solve(worktree, ORIGINAL)
    out = tmp_path / "solutions"
    got = grade.save_solution(out, "trial-1", worktree)
    assert got["solution_sha256"]
    patch = pathlib.Path(got["solution_patch"])
    assert patch.exists()
    assert "return a + b" in patch.read_text()


def test_an_untouched_worktree_yields_no_patch(worktree, tmp_path):
    """The agent changed nothing. Record that, do not write an empty file."""
    got = grade.save_solution(tmp_path / "solutions", "trial-1", worktree)
    assert got == {"solution_empty": True}


def test_two_identical_solutions_hash_the_same(worktree, tmp_path):
    """This is the point: it is how repeated verbatim output becomes visible."""
    _solve(worktree, ORIGINAL)
    a = grade.save_solution(tmp_path / "a", "t1", worktree)
    b = grade.save_solution(tmp_path / "b", "t2", worktree)
    assert a["solution_sha256"] == b["solution_sha256"]


def test_saving_never_raises_when_the_destination_is_unusable(worktree, tmp_path):
    _solve(worktree, ORIGINAL)
    blocker = tmp_path / "blocked"
    blocker.write_text("I am a file, not a directory")
    assert grade.save_solution(blocker, "t1", worktree) == {}


def _commit_uv_lock(worktree: pathlib.Path) -> None:
    (worktree / "uv.lock").write_text(
        'version = 1\n[options]\nexclude-newer = "0001-01-01T00:00:00Z"\n'
    )
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "l"],
        cwd=worktree,
        check=True,
    )


def _oracle_churns_uv_lock(worktree: pathlib.Path) -> None:
    # A bare `uv run` re-resolves under the machine's exclude-newer policy and
    # strips the block -- the hunk no agent wrote (#285).
    (worktree / "uv.lock").write_text("version = 1\n")


def test_uv_lock_churn_alone_reads_as_an_empty_solution(worktree, tmp_path):
    """#285: the oracle rewrites uv.lock in the tree the diff is taken from. An
    agent that wrote nothing must still read as empty, not as a one-hunk patch.
    """
    _commit_uv_lock(worktree)
    _oracle_churns_uv_lock(worktree)
    got = grade.save_solution(tmp_path / "solutions", "t1", worktree)
    assert got == {"solution_empty": True}


def test_uv_lock_churn_is_excluded_from_a_real_solution(worktree, tmp_path):
    """A real edit is still captured, minus the oracle's uv.lock hunk (#285)."""
    _commit_uv_lock(worktree)
    _solve(worktree, "    return a + b\n")
    _oracle_churns_uv_lock(worktree)
    got = grade.save_solution(tmp_path / "solutions", "t1", worktree)
    patch = pathlib.Path(got["solution_patch"]).read_text()
    assert "return a + b" in patch
    assert "uv.lock" not in patch


def test_two_empty_solutions_do_not_collide_on_the_lock_hunk(worktree, tmp_path):
    """#285: solution_sha256 collided across unrelated trials whose only content
    was the identical lock hunk. With it excluded, neither has a hash at all, so
    there is nothing to collide.
    """
    _commit_uv_lock(worktree)
    _oracle_churns_uv_lock(worktree)
    a = grade.save_solution(tmp_path / "a", "t1", worktree)
    b = grade.save_solution(tmp_path / "b", "t2", worktree)
    assert "solution_sha256" not in a
    assert "solution_sha256" not in b
    assert a == b == {"solution_empty": True}


def test_a_nested_uv_lock_is_also_excluded(worktree, tmp_path):
    """The oracle can run in a subpackage, so the churn can be nested (#285)."""
    (worktree / "pkg").mkdir()
    (worktree / "pkg" / "uv.lock").write_text('version = 1\n[options]\nx = "y"\n')
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "n"],
        cwd=worktree,
        check=True,
    )
    (worktree / "pkg" / "uv.lock").write_text("version = 1\n")
    got = grade.save_solution(tmp_path / "solutions", "t1", worktree)
    assert got == {"solution_empty": True}


# --- the gates -----------------------------------------------------------


def test_gates_report_counts_not_verdicts(worktree):
    """A gate returns numbers. Deciding what they mean is not its job."""
    got = grade.gates(worktree, timeout=60)
    assert set(got) <= {"ruff", "mypy"}
    assert all(isinstance(v, int) for v in got.values())


def test_a_missing_toolchain_is_an_absent_measurement_not_a_zero(worktree):
    """A zero here would read as "clean" and quietly become a published claim."""
    got = grade.gates(worktree, timeout=60, tools=["definitely-not-a-tool"])
    assert got == {}


def test_gates_cannot_change_a_verdict():
    """The oracle is the authority. Gates ride alongside it and never into it."""
    row = {"passed": True, "excluded": False}
    clean = dict(row, gates_after={"ruff": 0, "mypy": 0}, restored_verbatim=False)
    filthy = dict(row, gates_after={"ruff": 99, "mypy": 99}, restored_verbatim=True)
    assert results.verdict(clean) is True
    assert results.verdict(filthy) is True


def test_a_gate_delta_is_computed_against_the_control_not_against_zero(worktree):
    """gmail-archive carries 18 pre-existing mypy errors. Absolutes are useless."""
    before = {"ruff": 0, "mypy": 18}
    after = {"ruff": 2, "mypy": 18}
    assert grade.delta(before, after) == {"ruff": 2, "mypy": 0}


def test_a_delta_is_absent_when_either_side_is_missing(worktree):
    assert grade.delta({}, {"ruff": 2}) == {}
    assert grade.delta({"ruff": 0}, {}) == {}
    assert grade.delta({"ruff": 0}, {"mypy": 3}) == {}


def test_every_gate_key_survives_a_round_trip_through_json(worktree, tmp_path):
    """Rows are written as JSON lines; a set or a Path would blow up at write."""
    _solve(worktree, ORIGINAL)
    row = {}
    row.update(grade.save_solution(tmp_path / "s", "t1", worktree))
    row["gates_before"] = grade.gates(worktree, timeout=60)
    row["restored_verbatim"] = grade.restored_verbatim(
        worktree / "src/m.py", "add", ORIGINAL
    )
    assert json.loads(json.dumps(row)) == row


def test_verbatim_uses_the_right_parser_for_the_language(tmp_path):
    """A Swift file handed to the Python parser raises, and `restored_verbatim`
    would report None -- an unreadable file -- rather than a real comparison.
    Silently losing the recall signal on a whole repository."""
    p = tmp_path / "X.swift"
    p.write_text("enum X {\n    static func f() -> Int {\n        return 1\n    }\n}\n")
    assert grade.restored_verbatim(p, "X.f", "\n        return 1\n    ") is True
