"""Restart-between-trials, one module for two arms (#112, #77, #235).

The two shell scripts this replaces differ by an arm and nothing else, and
they have already drifted once in a way that cost three cycles. These tests
exist mostly to pin that the two arms share one code path.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import restart_between_trials as rbt
from source_text import code_of

SHELL_A = ROOT / "scripts" / "restart_between_trials.sh"
SHELL_B = ROOT / "scripts" / "restart_between_trials_armB.sh"


# --- the drift that having two copies caused ---------------------------------


def test_both_arms_pass_a_server_log() -> None:
    """The bug that only the copy could have. Arm B's shell script started a
    server with `--mtp-timing` and never passed `--server-log`, so every
    counter the engine emitted was written to a file nothing read -- three
    cycles asserting only that the flag had been passed. Arm A had no counters
    to lose, so the defect could exist in exactly one of two files that were
    supposed to be the same."""
    for arm in rbt.ARMS.values():
        argv = rbt.run_argv(arm, pathlib.Path("/l/s.log"))
        assert "--server-log" in argv, f"arm {arm.name} drops the counters"


def test_only_the_speculative_arm_reads_a_draft_log() -> None:
    """`--draft-log-engine` on an arm with no MTP head asks run.py to parse
    counters that will never appear."""
    assert "--draft-log-engine" in rbt.run_argv(rbt.ARMS["B"], pathlib.Path("/l/s.log"))
    assert "--draft-log-engine" not in rbt.run_argv(
        rbt.ARMS["A"], pathlib.Path("/l/s.log")
    )


def test_the_arms_differ_only_in_what_an_arm_is() -> None:
    """Backend, KV directory, MTP. If a fifth thing ever differs, it is either
    a real experimental variable or a bug, and this test forces the question."""
    a, b = rbt.ARMS["A"], rbt.ARMS["B"]
    differing = {
        f.name
        for f in __import__("dataclasses").fields(a)
        if getattr(a, f.name) != getattr(b, f.name)
    }
    assert differing == {"name", "backend", "kv", "want_mtp", "run_dir", "baseline"}


def test_the_two_arms_use_different_kv_directories() -> None:
    """ds4 rejects the other configuration's checkpoints when a flag changes
    the KV format. A shared directory makes one arm re-prefill where the other
    hit cache, and the only symptom is that it looks slower -- which is the
    quantity being measured."""
    assert rbt.ARMS["A"].kv != rbt.ARMS["B"].kv


def test_the_two_arms_use_different_backends() -> None:
    """Or the rows are indistinguishable in results.jsonl."""
    assert rbt.ARMS["A"].backend != rbt.ARMS["B"].backend


def test_only_the_b_arm_carries_mtp_flags() -> None:
    assert "--mtp-model" in rbt.server_command(rbt.ARMS["B"])
    assert "--mtp-model" not in rbt.server_command(rbt.ARMS["A"])


def test_the_transcripts_of_the_two_arms_do_not_collide() -> None:
    """Each run's transcripts move aside so the next does not clobber
    `~/bench-logs/<task>-<backend>-opencode-1.jsonl`."""
    assert rbt.ARMS["A"].run_dir != rbt.ARMS["B"].run_dir


# --- the refusals ------------------------------------------------------------


def test_a_missing_shim_refuses_before_the_lock(monkeypatch, tmp_path) -> None:
    """The upstream is otherwise indistinguishable from a working ds4 server,
    and OpenCode would talk to the wrong thing. It must refuse before claiming
    the machine, not after."""
    taken: list[str] = []
    monkeypatch.setattr(rbt, "port_answers", lambda *a, **k: False)
    monkeypatch.setattr(
        rbt.preflight,
        "acquire_lock",
        lambda *a, **k: taken.append("x") or (True, "ours"),
    )
    with pytest.raises(rbt.Refusal, match="8101"):
        rbt.cycle(rbt.ARMS["A"], tmp_path, tmp_path, 4242)
    assert taken == [], "the machine was claimed by a run that then refused"


def test_the_refusal_says_how_to_start_the_shim() -> None:
    """A refusal an operator cannot act on gets overridden rather than obeyed."""
    import inspect

    assert "ds4_qwen_tool_shim.py --port 8101" in inspect.getsource(rbt.cycle)


# --- the cycle ---------------------------------------------------------------


def test_every_trial_gets_a_fresh_server(monkeypatch, tmp_path) -> None:
    """That is the experiment, not a precaution: #77 degrades monotonically
    within a session, and restarting separates server state from machine
    state."""
    events = _wire(monkeypatch, tmp_path)
    assert rbt.cycle(rbt.ARMS["A"], tmp_path, tmp_path, 4242, trials=3) == 0
    assert events.count("serve") == 3


def test_every_trial_gets_its_own_server_log(monkeypatch, tmp_path) -> None:
    logs: list[pathlib.Path] = []
    _wire(monkeypatch, tmp_path, server_logs=logs)
    rbt.cycle(rbt.ARMS["A"], tmp_path, tmp_path, 4242, trials=3)
    assert len(logs) == len(set(logs)) == 3


def test_the_lock_is_held_across_the_whole_cycle(monkeypatch, tmp_path) -> None:
    """#133: the window this lock exists for is precisely the gap BETWEEN
    runs, where ds4-server is deliberately down and a process scan truthfully
    reports "all clear" while the machine is committed for hours."""
    events = _wire(monkeypatch, tmp_path)
    rbt.cycle(rbt.ARMS["A"], tmp_path, tmp_path, 4242, trials=3)
    assert events.count("lock-acquire") == 1
    assert events.count("lock-release") == 1
    assert events.index("lock-acquire") < events.index("serve")
    assert events.index("lock-release") > _last(events, "serve")


def test_the_lock_is_released_when_a_trial_raises(monkeypatch, tmp_path) -> None:
    events = _wire(monkeypatch, tmp_path)

    def explode(*a, **k):
        raise RuntimeError("the trial fell over")

    monkeypatch.setattr(rbt.subprocess, "run", explode)
    with pytest.raises(RuntimeError):
        rbt.cycle(rbt.ARMS["A"], tmp_path, tmp_path, 4242, trials=3)
    assert "lock-release" in events


def test_a_failed_trial_makes_the_cycle_report_failure(monkeypatch, tmp_path) -> None:
    events = _wire(monkeypatch, tmp_path, rc=1)
    assert rbt.cycle(rbt.ARMS["A"], tmp_path, tmp_path, 4242, trials=3) == 1
    assert events.count("serve") == 3, "one bad trial must not abort the rest"


# --- transcripts and the audit -----------------------------------------------


def test_transcripts_move_into_a_per_trial_directory(tmp_path) -> None:
    arm = rbt.ARMS["A"]
    (tmp_path / f"mbox-{arm.backend}-opencode-1.jsonl").write_text("{}")
    (tmp_path / f"other-{arm.backend}-opencode-1.jsonl").write_text("{}")
    assert rbt.collect_transcripts(tmp_path, arm, 2) == 2
    assert (tmp_path / f"{arm.run_dir}2").is_dir()


def test_no_transcripts_is_not_a_failure(tmp_path) -> None:
    """A `--no-client-log` run produces none, which is a valid state."""
    assert rbt.collect_transcripts(tmp_path, rbt.ARMS["A"], 1) == 0


def test_one_arms_transcripts_are_not_swept_into_the_other(tmp_path) -> None:
    """The glob is per-backend, so an arm cannot collect its pair's rows."""
    (tmp_path / f"t-{rbt.ARMS['B'].backend}-opencode-1.jsonl").write_text("{}")
    assert rbt.collect_transcripts(tmp_path, rbt.ARMS["A"], 1) == 0


def test_the_audit_never_fails_the_cycle(monkeypatch, tmp_path) -> None:
    """#64's audit is a read-out. A cycle that completed must not report
    failure because its audit did not run."""
    (tmp_path / "ds4server-trial1.log").write_text("x")
    monkeypatch.setattr(
        rbt.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no uv"))
    )
    rbt.kv_prefix_audit(tmp_path)


def test_the_audit_is_skipped_when_there_are_no_logs(tmp_path) -> None:
    rbt.kv_prefix_audit(tmp_path)


# --- what the port must not have brought across ------------------------------


def test_the_driver_does_not_look_for_processes_by_name() -> None:
    code = code_of(ROOT / "scripts" / "restart_between_trials.py")
    assert "pgrep" not in code
    assert "pkill" not in code


def test_both_shells_it_replaces_are_still_here() -> None:
    """#235: deleted only after a run agrees -- and this replaces TWO."""
    assert SHELL_A.exists()
    assert SHELL_B.exists()


# --- helpers -----------------------------------------------------------------


def _last(items: list[str], value: str) -> int:
    return len(items) - 1 - items[::-1].index(value)


def _wire(monkeypatch, tmp_path, *, rc: int = 0, server_logs: list | None = None):
    events: list[str] = []
    monkeypatch.setattr(rbt, "port_answers", lambda *a, **k: True)
    monkeypatch.setattr(
        rbt.preflight,
        "acquire_lock",
        lambda *a, **k: (events.append("lock-acquire"), (True, "ours"))[1],
    )
    monkeypatch.setattr(
        rbt.preflight,
        "release_lock",
        lambda *a, **k: (events.append("lock-release"), (True, "released"))[1],
    )

    @contextlib.contextmanager
    def fake_serving(command, log, **kw):
        events.append("serve")
        if server_logs is not None:
            server_logs.append(log)
        yield object()

    monkeypatch.setattr(rbt.ds4_server, "serving", fake_serving)

    class Done:
        returncode = rc

    monkeypatch.setattr(rbt.subprocess, "run", lambda *a, **k: Done())
    monkeypatch.setattr(rbt, "kv_prefix_audit", lambda *a, **k: None)
    return events


def test_each_arms_run_dir_is_the_one_its_shell_wrote() -> None:
    """One experiment, one directory family.

    #261 gave one module both arms. Arm A's run_dir matched its shell exactly;
    arm B's had been tidied to `77-armB-restart-run`, and ~/bench-logs already
    holds `77-armB-run{1,2,3}` from the shell. Nothing PARSES these names, so
    it changes no number -- it splits one experiment's transcripts across two
    families with nothing recording that they are the same experiment, which
    is a cost that only shows up months later when somebody goes looking.

    Read out of the shells rather than typed here, because a name typed in a
    test is a name that agrees with whoever typed it.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    for arm, script in (
        ("A", "restart_between_trials.sh"),
        ("B", "restart_between_trials_armB.sh"),
    ):
        text = (root / "scripts" / script).read_text()
        # `mkdir -p "$BENCH_LOGS/112-run$n"` -> 112-run
        found = re.findall(r"\$BENCH_LOGS/([\w.-]+?)\$n", text)
        assert found, f"{script} does not name a run dir"
        assert set(found) == {rbt.ARMS[arm].run_dir}, (
            f"arm {arm}: the shell writes {sorted(set(found))}, "
            f"the port writes {rbt.ARMS[arm].run_dir!r}"
        )
