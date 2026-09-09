"""The #138 stack A/B sweep loop, ported from shell (#235).

`lib/stack_arm.py` (#260) already holds the arm description, the server argv,
the pair checks and the run record. What is tested here is what was left in the
shell: building the arms from the environment, the guards that are ds4-only,
the three-way worktree verdict, and the rule that a sweep with no transcripts
is a failed sweep whatever its exit code said.
"""

from __future__ import annotations

import ast
import datetime as dt
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import stack_agent_ab as sab
import stack_agent_report
import stack_arm
from source_text import code_of


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No inherited NEW_*/OLD_* -- the defaults ARE #138 and must be testable."""
    for key in list(os.environ):
        if key.startswith(("NEW_", "OLD_")) or key == "SHIM_NO_STRIP":
            monkeypatch.delenv(key, raising=False)


# ------------------------------------------------------------- arms from env


def test_the_defaults_are_138() -> None:
    new, old = sab.arm_from_env("NEW"), sab.arm_from_env("OLD")
    assert new.backend == "qwen38fnds4kimat"
    assert old.backend == "qwen38fnds4shim"
    assert new.engine == old.engine == stack_arm.DS4
    assert new.kv != old.kv


def test_every_override_the_shell_had_still_works(monkeypatch) -> None:
    # Not conveniences: #210/#151 need MTP on against MTP off in one tree,
    # #191 needs a second engine, #225 needs two mlx-serve BINARIES so both
    # arms do not resolve to the same brew install.
    monkeypatch.setenv("NEW_ENGINE", "mlx-serve")
    monkeypatch.setenv("NEW_MLX_MODEL", "/packs/qwen38fn")
    monkeypatch.setenv("NEW_MLX_BIN", "/src/mlx-serve/zig-out/bin/mlx-serve")
    monkeypatch.setenv("NEW_MLX_PORT", "11235")
    monkeypatch.setenv("NEW_BACKEND", "qwen38fnmlxserve-git")
    monkeypatch.setenv("OLD_FLAGS", "--mtp-draft 7 --mtp-timing")
    monkeypatch.setenv("OLD_RUN_FLAGS", "--no-require-draft")

    new, old = sab.arm_from_env("NEW"), sab.arm_from_env("OLD")
    assert new.engine == "mlx-serve"
    assert new.mlx_bin.endswith("zig-out/bin/mlx-serve")
    assert new.mlx_port == 11235
    assert new.port == 11235
    assert old.flags == "--mtp-draft 7 --mtp-timing"
    assert old.run_flags == "--no-require-draft"


# ------------------------------------------------------------------- guards


def test_the_shim_guard_is_ds4_only(monkeypatch) -> None:
    # mlx-serve keeps no disk KV we manage and sits behind no shim, so a guard
    # written for ds4 would either refuse a legitimate run or pass while
    # checking nothing.
    monkeypatch.setenv("NEW_ENGINE", "mlx-serve")
    monkeypatch.setenv("NEW_MLX_MODEL", "/packs/a")
    monkeypatch.setenv("OLD_ENGINE", "mlx-serve")
    monkeypatch.setenv("OLD_MLX_MODEL", "/packs/b")
    monkeypatch.setenv("OLD_BACKEND", "other")
    why = sab.check_shim(sab.arm_from_env("NEW"), sab.arm_from_env("OLD"))
    assert "not this run's concern" in why


def test_a_ds4_arm_still_needs_the_shim(monkeypatch) -> None:
    import ports

    monkeypatch.setattr(ports, "holder", lambda port: None)
    with pytest.raises(sab.tool_shim.NotServing):
        sab.check_shim(sab.arm_from_env("NEW"), sab.arm_from_env("OLD"))


def test_an_inherited_strip_off_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("SHIM_NO_STRIP", "1")
    with pytest.raises(sab.Refusing):
        sab.check_strip()


# ------------------------------------------------- the three-way worktree verdict


def test_a_clean_worktree_passes(monkeypatch) -> None:
    monkeypatch.setattr(sab.provenance, "code_is_dirty", lambda *a, **k: False)
    clean, why = sab.worktree_state()
    assert clean is True and why == "clean"


def test_a_dirty_worktree_refuses(monkeypatch) -> None:
    monkeypatch.setattr(sab.provenance, "code_is_dirty", lambda *a, **k: True)
    clean, why = sab.worktree_state()
    assert clean is False and "uncommitted code" in why


def test_could_not_check_is_not_clean(monkeypatch) -> None:
    # The third state, and the reason the shell decoded an exit status. Fails
    # closed: a false clean can sink ~3.5 hours of sweeps, a false refusal
    # costs ~15 minutes.
    def boom(*a, **k):
        raise OSError("git is gone")

    monkeypatch.setattr(sab.provenance, "code_is_dirty", boom)
    clean, why = sab.worktree_state()
    assert clean is False and "could not confirm" in why


def test_the_guard_sees_untracked_files(monkeypatch) -> None:
    # #227 defect 3: untracked=True is the ONE gap --require-harness-head
    # leaves, because that pin reads untracked=False. A file git has never seen
    # otherwise sails past it into a read-out void hours later.
    seen: dict[str, object] = {}

    def spy(repo, **kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(sab.provenance, "code_is_dirty", spy)
    sab.worktree_state()
    assert seen.get("untracked", True) is not False


def test_the_guard_no_longer_shells_out_to_uv() -> None:
    # A bare `uv run` can rewrite uv.lock, a TRACKED file, so the guard whose
    # job is to catch a dirty tracked path could dirty one itself -- which is
    # why the shell needed --frozen. In-process there is no lock to rewrite.
    source = code_of(ROOT / "scripts" / "stack_agent_ab.py")
    assert "--frozen" not in source


# ----------------------------------------------------------------- run.py argv


def declared_flags(path: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and str(arg.value).startswith("--"):
                found.add(str(arg.value))
    return found


def test_every_flag_this_driver_emits_is_one_run_py_declares(tmp_path) -> None:
    declared = declared_flags(ROOT / "benchmarks" / "agent" / "run.py")
    arm = sab.arm_from_env("OLD")
    emitted = {
        t
        for t in sab.run_argv(arm, "old-sweep1", tmp_path, "abc")
        if t.startswith("--")
    }
    assert emitted <= declared, f"run.py does not declare {sorted(emitted - declared)}"


def test_a_ds4_arm_names_its_draft_log_dialect(tmp_path) -> None:
    argv = sab.run_argv(sab.arm_from_env("OLD"), "old-sweep1", tmp_path, "abc")
    assert argv[argv.index("--draft-log-engine") + 1] == "ds4"


def test_an_mlx_arm_omits_the_flag_rather_than_passing_a_rejected_name(
    tmp_path, monkeypatch
) -> None:
    # run.py accepts ds4|mtplx only. Passing "mlx-serve" is an argparse error
    # that ends the sweep in one second, not a harmless unknown option.
    monkeypatch.setenv("NEW_ENGINE", "mlx-serve")
    monkeypatch.setenv("NEW_MLX_MODEL", "/packs/a")
    argv = sab.run_argv(sab.arm_from_env("NEW"), "new-sweep1", tmp_path, "abc")
    assert "--draft-log-engine" not in argv


def test_run_flags_are_split_without_reparsing_metacharacters(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OLD_RUN_FLAGS", "--no-require-draft --effort 'a b'")
    argv = sab.run_argv(sab.arm_from_env("OLD"), "old-sweep1", tmp_path, "abc")
    assert argv[-3:] == ["--no-require-draft", "--effort", "a b"]


def test_the_server_log_reaches_run_py(tmp_path) -> None:
    # #210: without it the row carries no `draft` field at all, and an MTP arm
    # that never speculated is indistinguishable from one that did.
    argv = sab.run_argv(sab.arm_from_env("OLD"), "old-sweep1", tmp_path, "abc")
    assert argv[argv.index("--server-log") + 1] == str(
        tmp_path / "server-old-sweep1.log"
    )


# --------------------------------------------------------------- the sweep


def test_every_tag_the_driver_emits_is_one_the_report_can_read() -> None:
    """A tag is an interface, not a filename.

    `stack_agent_report.Sweep` splits on the LAST `-sweep` and then looks the
    arm name up in `BACKENDS` -- so a tag it cannot split does not degrade,
    it raises KeyError halfway through a read-out. Asserting against a typed
    string would only prove I can type; this builds the tag the way the
    driver builds it and hands it to the class that consumes it.

    `ab_driver`'s own default is `r3-new`, which this splitter turns into
    `r3-new` and then fails to find. That is why stack_agent_ab passes
    tag_for at all.
    """
    arms = [stack_agent_report.BACKENDS[b] for b in stack_agent_report.BACKENDS]
    assert sorted(arms) == ["new", "old"], "the report knows exactly two arms"

    for name in arms:
        for n in (1, 2, 10):
            tag = f"{name}-sweep{n}"  # exactly stack_agent_ab's tag_for
            got = stack_agent_report.Sweep(tag, start=dt.datetime(2026, 9, 9, 8))
            assert got.arm == name, tag
            assert got.backend  # the BACKENDS lookup resolved

    # And the shape that would have shipped without tag_for.
    with pytest.raises(KeyError):
        stack_agent_report.Sweep("r3-new", start=dt.datetime(2026, 9, 9, 8))


def test_a_sweep_with_no_transcripts_fails_even_at_rc_zero(
    tmp_path, monkeypatch
) -> None:
    """run.py can exit 0 on a session that wrote nothing.

    A sweep with no evidence has to count as failed whatever its exit code
    says: `VOID: sweep new-sweep1 has 0 rows` is a read-out discovery, hours
    later, and the wrapper would otherwise have called the batch complete.
    """
    monkeypatch.setattr(sab, "worktree_state", lambda: (True, "clean"))
    monkeypatch.setattr(sab.child, "run", lambda *a, **k: 0)
    monkeypatch.setattr(sab, "collect_transcripts", lambda *a, **k: 0)
    assert sab.sweep(sab.arm_from_env("OLD"), "old-sweep1", tmp_path, "abc") == 1

    monkeypatch.setattr(sab, "collect_transcripts", lambda *a, **k: 15)
    assert sab.sweep(sab.arm_from_env("OLD"), "old-sweep2", tmp_path, "abc") == 0


def test_a_dirty_tree_refuses_the_sweep_without_spending_the_machine(
    tmp_path, monkeypatch
) -> None:
    def explode(*a, **k):
        raise AssertionError("a refused sweep must not reach run.py")

    monkeypatch.setattr(sab, "worktree_state", lambda: (False, "dirty"))
    monkeypatch.setattr(sab.child, "run", explode)
    assert sab.sweep(sab.arm_from_env("OLD"), "old-sweep1", tmp_path, "abc") == 1


def test_both_times_are_written_and_they_differ_in_meaning(
    tmp_path, monkeypatch
) -> None:
    # sweep-order.txt once carried ONE time, written at the END, while the
    # report read it as the START. Every window held the next sweep's rows: on
    # the 2026-09-05 re-run, 45 of 60 rows fit no window and the old-arm
    # control read 14/30 against a true 27/30.
    monkeypatch.setattr(sab, "worktree_state", lambda: (True, "clean"))
    monkeypatch.setattr(sab.child, "run", lambda *a, **k: 0)
    monkeypatch.setattr(sab, "collect_transcripts", lambda *a, **k: 15)
    sab.sweep(sab.arm_from_env("OLD"), "old-sweep1", tmp_path, "abc")
    line = (tmp_path / "sweep-order.txt").read_text().strip().split()
    assert len(line) == 3 and line[0] == "old-sweep1"


def test_a_leftover_from_a_killed_run_is_not_this_sweeps_evidence(
    tmp_path, monkeypatch
) -> None:
    bench = tmp_path / "bench-logs"
    bench.mkdir()
    arm = sab.arm_from_env("OLD")
    stale = bench / f"mbox-scan-{arm.backend}-opencode-1.stdout.jsonl"
    stale.write_text("{}")
    os.utime(stale, (1000, 1000))
    since = time.time()
    time.sleep(0.01)
    fresh = bench / f"parser-date-{arm.backend}-opencode-1.stdout.jsonl"
    fresh.write_text("{}")
    monkeypatch.setattr(sab, "BENCH_LOGS", bench)

    assert sab.collect_transcripts(arm, "old-sweep1", tmp_path, since) == 1
    assert (tmp_path / "old-sweep1" / fresh.name).exists()
    assert stale.exists()


def test_the_last_pgrep_is_gone() -> None:
    code = code_of(ROOT / "scripts" / "stack_agent_ab.py")
    assert "pgrep" not in code
    assert "pkill" not in code
