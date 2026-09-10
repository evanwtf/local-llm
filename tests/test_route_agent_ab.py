"""The #149 route A/B driver, ported from shell (#235 stage 5).

Three things here are not ordinary unit tests and are the reason the file
exists:

* the R arm is defined by a variable being **absent**, which a dict cannot say;
* the tags are parsed by `route_ab_report.py`, so they are an interface and not
  a filename;
* the shell died on a flag `run.py` had removed (#264), which nothing caught.
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
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import equiv
import metal_route
import route_ab_report
import route_agent_ab
from source_text import code_of

# The two summary lines a real gate run prints against the withhold tree,
# copied from ~/bench-logs/149-route-ab/gate-validate.log lines 60 and 104.
GATE_LOG = """\
ds4-test: Tensor equivalence candidate route=auto
ds4-test: Tensor summary route=auto cases=5 capture_fail=0 logits_fail=0 \
greedy_fail=0 top1_mismatch=0 min_top5_overlap=5/5 min_overlap=20/20 \
worst_rank_delta=0 worst_rms=0 worst_max_abs=0 worst_top20_max_abs=0
ds4-test: Tensor equivalence candidate route=tensor-optin
ds4-test: Tensor summary route=tensor-optin cases=5 capture_fail=0 \
logits_fail=2 greedy_fail=8 top1_mismatch=2 min_top5_overlap=2/5 \
min_overlap=10/20 worst_rank_delta=13 worst_rms=1.38592 worst_max_abs=7.26952 \
worst_top20_max_abs=6.62295
metal-tensor-equivalence: OK
"""


# --------------------------------------------------------------- the two arms


def test_the_withheld_arm_removes_the_variable_rather_than_not_setting_it() -> None:
    env, unset = route_agent_ab.arm_env(metal_route.WITHHELD)
    assert env == {}
    assert unset == (route_agent_ab.TENSOR_ENV,)


def test_the_tensor_arm_sets_it() -> None:
    env, unset = route_agent_ab.arm_env(metal_route.TENSOR)
    assert env == {route_agent_ab.TENSOR_ENV: "1"}
    assert unset == ()


def test_both_arms_run_the_same_command_line() -> None:
    # The experiment is one variable. A flag difference would be a second
    # thing changing, and the arms would no longer be the arms #149 registered.
    t = route_agent_ab.server_command(route_agent_ab.KV[metal_route.TENSOR])
    r = route_agent_ab.server_command(route_agent_ab.KV[metal_route.WITHHELD])
    assert [a for a in t if "server-kv" not in a] == [
        a for a in r if "server-kv" not in a
    ]


def test_the_arms_do_not_share_a_kv_directory() -> None:
    assert (
        route_agent_ab.KV[metal_route.TENSOR] != route_agent_ab.KV[metal_route.WITHHELD]
    )


# ------------------------------------------------------------------- the tags


def test_tags_are_the_shape_the_report_parses() -> None:
    arms = route_agent_ab.arms(pathlib.Path("/tmp/unused"))
    tags = [route_agent_ab.tag_for(a, 3) for a in arms]
    assert tags == ["t-sweep3", "r-sweep3"]


def test_a_recorded_window_round_trips_through_the_report(tmp_path) -> None:
    # The interface, asserted end to end: the driver writes it, the report
    # reads it. A tag the report cannot split produces rows nothing attributes.
    route_agent_ab.record_window(tmp_path, "t-sweep1", "08:56:17", "09:38:07")
    route_agent_ab.record_window(tmp_path, "r-sweep1", "09:38:15", "10:24:22")
    anchor = dt.datetime(2026, 9, 5, 8, 0, 0)
    wins = route_ab_report.read_windows(tmp_path, "t-sweep", "r-sweep", anchor)
    assert [(tag, arm) for tag, arm, _, _ in wins] == [
        ("t-sweep1", "t"),
        ("r-sweep1", "r"),
    ]


# ------------------------------------------------------------- the phase-0 gate


def test_the_signature_reads_the_optin_line_not_the_asserted_one() -> None:
    # Both are `Tensor summary` lines and the asserted one is first. Reading
    # the first would find worst_rms=0 and fail a healthy gate; reading the
    # second for the verdict would fail every run of this arm.
    assert route_agent_ab.signature(GATE_LOG) == (1.38592, 7.26952)


def test_the_recorded_gate_signature_is_inside_the_registered_bands() -> None:
    assert route_agent_ab.signature_ok(route_agent_ab.signature(GATE_LOG)) is True


def test_a_signature_outside_the_band_is_refused() -> None:
    drifted = GATE_LOG.replace("worst_rms=1.38592", "worst_rms=3.5")
    assert route_agent_ab.signature_ok(route_agent_ab.signature(drifted)) is False


def test_no_signature_is_not_a_pass() -> None:
    assert route_agent_ab.signature("no summary here") is None
    assert route_agent_ab.signature_ok(None) is False


def test_reusing_a_gate_log_needs_both_halves(tmp_path) -> None:
    log = tmp_path / route_agent_ab.GATE_LOG
    log.write_text(GATE_LOG)
    assert route_agent_ab.gate_recorded(log) is True

    # An OK verdict with no signature is the shape a truncated log has, and it
    # is exactly the case where re-running is cheap next to being wrong.
    log.write_text("metal-tensor-equivalence: OK\n")
    assert route_agent_ab.gate_recorded(log) is False

    # A signature with no verdict is the other half.
    log.write_text(GATE_LOG.replace("metal-tensor-equivalence: OK", ""))
    assert route_agent_ab.gate_recorded(log) is False


def test_a_missing_gate_log_is_not_a_pass(tmp_path) -> None:
    assert route_agent_ab.gate_recorded(tmp_path / "absent.log") is False


# ------------------------------------------------------- run.py's actual flags


def declared_flags(path: pathlib.Path) -> set[str]:
    """Every long option `path` declares through add_argument."""
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


def test_every_flag_this_driver_emits_is_one_run_py_declares() -> None:
    # #264: the shell passed --skip-tensor-gate for weeks after run.py removed
    # it with the old metal_tensor_gate_step. argparse would have exited 2 on
    # every sweep, and the `|| echo` swallowed it -- six server restarts, hours,
    # zero rows, exit 0. This class of rot is mechanical to catch.
    declared = declared_flags(ROOT / "benchmarks" / "agent" / "run.py")
    emitted = {
        token
        for token in route_agent_ab.arm_argv("t-sweep1", pathlib.Path("/tmp"), 1, "abc")
        if token.startswith("--")
    }
    assert emitted <= declared, f"run.py does not declare {sorted(emitted - declared)}"


def test_the_driver_passes_the_flag_that_replaced_the_removed_one() -> None:
    argv = route_agent_ab.arm_argv("t-sweep1", pathlib.Path("/tmp"), 1, "abc")
    assert "--allow-unverified-route" in argv
    assert "--skip-tensor-gate" not in argv


def test_the_shell_is_dead_by_the_flag_run_py_dropped() -> None:
    """#264: the shell cannot work, and this names why.

    Two cheap facts, no pgrep, no ds4-server, no wait_ready, no git. The shell
    passes `--skip-tensor-gate` to run.py; run.py's parser no longer declares
    it, so argparse exits 2 on every sweep and the `|| echo` swallows it --
    six server restarts, hours, zero rows, exit 0. This test starts failing the
    day someone re-adds the flag, which is the notice we want.
    """
    shell = (ROOT / "vault" / "route_agent_ab.sh").read_text()
    assert "--skip-tensor-gate" in shell, (
        "route_agent_ab.sh no longer passes --skip-tensor-gate -- good, but "
        "this test's premise is stale"
    )
    flags = equiv.declared_run_flags()
    assert "--skip-tensor-gate" not in flags, (
        "run.py re-declared --skip-tensor-gate; the shell may work again, and "
        "this test must be retired with the shell"
    )


def test_the_run_is_pinned_to_a_harness_commit() -> None:
    argv = route_agent_ab.arm_argv("t-sweep1", pathlib.Path("/tmp"), 1, "deadbee")
    assert argv[argv.index("--require-harness-head") + 1] == "deadbee"


def test_the_driver_does_not_take_the_machine_lock_per_arm() -> None:
    # Every arm claiming the lock would refuse against the run's own claim.
    assert "--no-lock" in route_agent_ab.arm_argv(
        "t-sweep1", pathlib.Path("/tmp"), 1, "abc"
    )


# ------------------------------------------------------------ the route assertion


def test_a_sweep_whose_log_shows_the_other_route_is_refused(tmp_path) -> None:
    log = tmp_path / "server-r-sweep1.log"
    log.write_text(
        f"{metal_route.FAST_PATH_LINE}\n{metal_route.WITHHOLD_LINE}\n"
        f"{metal_route.TENSOR_LINE}\n"
    )
    with pytest.raises(route_agent_ab.Refusing) as caught:
        route_agent_ab.assert_route(log, metal_route.WITHHELD)
    assert str(log) in str(caught.value)


def test_a_missing_server_log_is_refused_not_passed(tmp_path) -> None:
    with pytest.raises(route_agent_ab.Refusing):
        route_agent_ab.assert_route(tmp_path / "absent.log", metal_route.TENSOR)


def test_a_good_log_passes(tmp_path) -> None:
    log = tmp_path / "server-t-sweep1.log"
    log.write_text(f"{metal_route.FAST_PATH_LINE}\n{metal_route.TENSOR_LINE}\n")
    assert "passed" in route_agent_ab.assert_route(log, metal_route.TENSOR)


# --------------------------------------------------------------- housekeeping


def test_transcripts_are_claimed_before_the_next_sweep_writes(
    tmp_path, monkeypatch
) -> None:
    bench = tmp_path / "bench-logs"
    bench.mkdir()

    # A leftover from a run killed before ITS move ran. `old-sweep1` once held
    # 22 transcripts for a 15-task sweep because of exactly this.
    stale = bench / f"mbox-scan-{route_agent_ab.BACKEND}-opencode-1.stdout.jsonl"
    stale.write_text("{}")
    os.utime(stale, (1000, 1000))

    since = time.time()
    time.sleep(0.01)

    mine = bench / f"parser-date-{route_agent_ab.BACKEND}-opencode-1.stdout.jsonl"
    mine.write_text("{}")
    theirs = bench / "parser-date-qwen38fnmlxserve-opencode-1.stdout.jsonl"
    theirs.write_text("{}")
    monkeypatch.setattr(route_agent_ab, "BENCH_LOGS", bench)

    out = tmp_path / "out"
    assert route_agent_ab.collect_transcripts("t-sweep1", out, 1, since) == 1
    assert (out / "t-sweep1" / mine.name).exists()
    assert not mine.exists()
    # A leftover from a killed run is not this sweep's evidence.
    assert stale.exists()
    # Another backend's rows are not this run's to move.
    assert theirs.exists()


def test_the_refusals_name_every_missing_asset_at_once(monkeypatch) -> None:
    # One at a time turns a missing model and a missing binary into two
    # separate hours-apart refusals.
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: False)
    assert len(route_agent_ab.missing_assets()) == 5


def test_the_withhold_commit_is_identified_by_subject(monkeypatch) -> None:
    monkeypatch.setattr(
        route_agent_ab, "tree_subject", lambda tree=None: "Something else"
    )
    with pytest.raises(route_agent_ab.Refusing) as caught:
        route_agent_ab.check_tree()
    assert route_agent_ab.WITHHOLD_SUBJECT in str(caught.value)


def test_the_strip_must_be_on(monkeypatch) -> None:
    monkeypatch.setenv("SHIM_NO_STRIP", "1")
    with pytest.raises(route_agent_ab.Refusing):
        route_agent_ab.check_strip()


def test_the_shim_is_found_by_port_not_by_name(monkeypatch) -> None:
    import ports

    monkeypatch.setattr(ports, "holder", lambda port: None)
    with pytest.raises(route_agent_ab.Refusing) as caught:
        route_agent_ab.check_shim()
    assert f":{route_agent_ab.SHIM_PORT}" in str(caught.value)


def test_the_driver_that_replaces_five_pgrep_calls_uses_none() -> None:
    code = code_of(ROOT / "scripts" / "route_agent_ab.py")
    assert "pgrep" not in code
    assert "pkill" not in code
