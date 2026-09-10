"""The Python #210 treatment gate (#235 stage 4).

`test_mtp_treatment_gate.py` covers the shell script this replaces and stays
until #235's rule is met.

#210's `Done when` is a pair: a real batch writes `draft` on every row of an
MTP backend, AND one deliberately broken arm is shown to be refused. Half 2
without half 1 is a gate nobody has run in anger; half 1 without half 2 is a
green light from a gate that may never fire. 119 MTP rows were taken with
neither.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mtp_treatment_gate as gate
from source_text import code_of

SHELL = ROOT / "vault" / "mtp_treatment_gate.sh"
KV = pathlib.Path("/tmp/kv")


# --- the broken arm, which is the whole of #210's half 2 ---------------------


def test_the_bypass_arm_differs_from_the_treated_arm_by_exactly_one_flag() -> None:
    """ "The broken arm is not a broken flag." Omitting `--mtp-model` is the
    exact failure the runbook records: the argv still reads as an MTP arm."""
    treated = gate.server_command(True, KV)
    bypass = gate.server_command(False, KV)
    assert [x for x in treated if x not in bypass] == [
        "--mtp-model",
        str(gate.DS4_MTP),
    ]


def test_the_bypass_arm_still_carries_the_draft_flags() -> None:
    """This is the point of the whole stage, and the easiest thing to lose in
    a port. `--mtp-draft 7 --mtp-timing` are accepted without complaint and
    `counters_on()` READS THE ARGV AND PASSES on this configuration. Only the
    per-trial counters catch it.

    An arm built without these would also be refused -- by the argv check --
    which would demonstrate a different and much less interesting failure."""
    bypass = gate.server_command(False, KV)
    assert "--mtp-draft" in bypass
    assert "--mtp-timing" in bypass
    assert "--mtp-model" not in bypass


def test_the_library_would_not_build_that_arm_by_accident() -> None:
    """`ds4_server.argv` drops the draft flags when no model is given, and is
    right to: a half-MTP arm built by accident is exactly #151. This gate has
    to ask for it explicitly, which is why it goes through `extra`."""
    import ds4_server

    accidental = ds4_server.argv(
        gate.DS4_MODEL,
        gate.DS4_PLE,
        KV,
        binary=pathlib.Path("/t/ds4-server"),
        mtp_model=None,
        mtp_draft=7,
        mtp_timing=True,
    )
    assert "--mtp-draft" not in accidental, "the library must refuse this by default"


def test_the_two_arms_use_different_kv_directories() -> None:
    """The MTP and non-MTP KV formats are incompatible and ds4 rejects the
    other one's checkpoints. Sharing a directory would leave rejected
    checkpoints behind and make the next real arm re-prefill, whose only
    symptom is that it looks slower."""
    assert gate.KV_TREATED != gate.KV_BYPASS


# --- the gate's own inverted logic -------------------------------------------


def test_a_bypass_arm_that_succeeds_is_a_failure(monkeypatch, tmp_path) -> None:
    """A zero exit from the broken arm means the gate did not fire. That is
    the finding, and it must not read as a pass."""
    _wire(monkeypatch, tmp_path, rc=0)
    with pytest.raises(gate.Refusal, match="not binding"):
        gate.stage_bypass(tmp_path, tmp_path / "s.log", 1)


def test_a_bypass_arm_that_is_refused_is_a_pass(monkeypatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path, rc=1)
    assert gate.stage_bypass(tmp_path, tmp_path / "s.log", 1) == 0


def test_a_refused_arm_that_wrote_rows_is_still_a_failure(
    monkeypatch, tmp_path
) -> None:
    """A refusal must raise before `write_row`. A non-zero exit that still
    left rows behind is the gate firing too late to matter -- and those rows
    would be in a scratch file, which is why the claim "the broken arm
    contaminated nothing" must not rest on reading the caller correctly."""
    _wire(monkeypatch, tmp_path, rc=1, rows=2)
    with pytest.raises(gate.Refusal, match="too late"):
        gate.stage_bypass(tmp_path, tmp_path / "s.log", 1)


def test_the_bypass_arm_writes_to_a_scratch_results_file(tmp_path) -> None:
    argv = gate.run_argv(
        tmp_path,
        tmp_path / "s.log",
        batch="210-bypass-demo",
        trials=1,
        require_draft=True,
        task=gate.BYPASS_TASK,
        results=tmp_path / "bypass-scratch.jsonl",
    )
    assert argv[argv.index("--results") + 1].endswith("bypass-scratch.jsonl")
    assert "--require-draft" in argv


# --- the treated arm ---------------------------------------------------------


def test_the_treated_arm_requires_a_draft() -> None:
    argv = gate.run_argv(
        pathlib.Path("/l"),
        pathlib.Path("/l/s.log"),
        batch="210-treated",
        trials=3,
        require_draft=True,
    )
    assert "--require-draft" in argv
    assert "--no-require-draft" not in argv
    assert argv[argv.index("--trials") + 1] == "3"


def test_the_silent_arm_deliberately_does_not() -> None:
    """`silent` is not a retry. It is the escape the refusal message itself
    names, and it is how #151's zero-counter case gets rows instead of an exit
    code."""
    argv = gate.run_argv(
        pathlib.Path("/l"),
        pathlib.Path("/l/s.log"),
        batch="210-silent-arm",
        trials=1,
        require_draft=False,
    )
    assert "--no-require-draft" in argv


def test_a_treated_arm_needs_the_sidecar_line_not_just_mtp_on(tmp_path) -> None:
    """`assert_graph` covers `MTP=off` both ways. The sidecar line is the
    extra check this gate needs, and it stays here rather than in the library
    because only a driver that knows there is a sidecar can ask for it."""
    log = tmp_path / "s.log"
    log.write_text("Qwen graph allocated: MTP=Q4_K verifier=block\n")
    with pytest.raises(gate.Refusal, match="sidecar"):
        gate.assert_sidecar(log)
    log.write_text("Qwen graph allocated: MTP=Q4_K\nMTP sidecar loaded from x.gguf\n")
    assert "sidecar" in gate.assert_sidecar(log)


# --- the replay stage's leak, which is the bug this port fixes ---------------


def test_the_dumping_shim_is_restored_even_when_the_payload_is_empty(
    monkeypatch, tmp_path
) -> None:
    """The shell restarted the shim with SHIM_DUMP and restored a plain one at
    the end of the replay branch -- but its empty-payload check `exit 1`s
    BEFORE the restore. That path left a payload-dumping shim on :8101 for
    every later stage, and nothing downstream would have said so."""
    events = _wire_shim(monkeypatch)
    monkeypatch.setattr(gate, "_run", lambda *a, **k: 0)
    monkeypatch.setattr(gate, "arm", _null_context)
    with pytest.raises(gate.Refusal, match="captured no payload"):
        gate.stage_replay(tmp_path, tmp_path / "s.log", 1)
    # `shim()` clears a leftover before starting, so the sequence is
    # stop, start, stop. What matters is the last one.
    assert events[-1] == "shim-stop", "a payload-dumping shim survived on :8101"
    assert events.count("shim-start") == 1
    assert events.count("shim-stop") == events.count("shim-start") + 1


def test_the_replay_shim_carries_the_dump_variable(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(gate.unitctl, "stop", lambda *a, **k: "stopped")
    monkeypatch.setattr(
        gate.unitctl,
        "start",
        lambda name, cmd, **kw: seen.update(kw) or _FakeUnit(),
    )
    monkeypatch.setattr(gate.unitctl, "read", lambda *a, **k: None)
    # Nothing on the port: a port that answers while the unit record is empty
    # is a foreign shim, which is a different test.
    monkeypatch.setattr(gate, "port_answers", lambda *a, **k: False)
    monkeypatch.setattr(gate, "_wait_for_port", lambda *a, **k: True)
    with gate.shim(tmp_path / "s.log", dump=tmp_path / "p.json"):
        pass
    assert seen["env"] == {"SHIM_DUMP": str(tmp_path / "p.json")}


def test_a_plain_shim_carries_no_dump_variable(monkeypatch, tmp_path) -> None:
    """Every stage but replay must leave SHIM_DUMP alone."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(gate.unitctl, "stop", lambda *a, **k: "stopped")
    monkeypatch.setattr(
        gate.unitctl, "start", lambda name, cmd, **kw: seen.update(kw) or _FakeUnit()
    )
    monkeypatch.setattr(gate.unitctl, "read", lambda *a, **k: None)
    monkeypatch.setattr(gate, "port_answers", lambda *a, **k: False)
    monkeypatch.setattr(gate, "_wait_for_port", lambda *a, **k: True)
    with gate.shim(tmp_path / "s.log"):
        pass
    assert seen["env"] is None


def test_a_shim_that_never_answers_is_stopped_not_leaked(monkeypatch, tmp_path) -> None:
    """The readiness check lives INSIDE the try, so its refusal still runs the
    stop. A raise above the try leaves the shim running -- and on the replay
    stage that is a payload-dumping shim, which is the exact bug this context
    manager exists to fix, one failure mode over. Raised by @deepseek
    reviewing #256."""
    events = _wire_shim(monkeypatch)
    monkeypatch.setattr(gate, "_wait_for_port", lambda *a, **k: False)
    with (
        pytest.raises(gate.Refusal, match="did not answer"),
        gate.shim(tmp_path / "s.log", dump=tmp_path / "p.json"),
    ):
        pass
    assert events[-1] == "shim-stop", "a shim that never answered was leaked"
    assert events.count("shim-start") == 1


def test_the_foreign_refusal_names_the_pid(monkeypatch, tmp_path) -> None:
    """Matching the ds4_server refusal: an operator who is told "something" is
    on the port has to go find it themselves, and a refusal they cannot act on
    gets overridden rather than obeyed."""
    monkeypatch.setattr(gate, "port_answers", lambda *a, **k: True)
    monkeypatch.setattr(gate.unitctl, "read", lambda *a, **k: None)
    monkeypatch.setattr(gate.unitctl, "state", lambda *a: gate.unitctl.STOPPED)
    monkeypatch.setattr(gate, "port_holder", lambda _p: 5150)
    with pytest.raises(gate.Refusal, match="pid 5150"), gate.shim(tmp_path / "s.log"):
        pass


def test_an_unidentifiable_holder_still_refuses(monkeypatch, tmp_path) -> None:
    """`lsof` can fail or be absent. Not knowing whose process it is, is not a
    reason to start beside it."""
    monkeypatch.setattr(gate, "port_answers", lambda *a, **k: True)
    monkeypatch.setattr(gate.unitctl, "read", lambda *a, **k: None)
    monkeypatch.setattr(gate.unitctl, "state", lambda *a: gate.unitctl.STOPPED)
    monkeypatch.setattr(gate, "port_holder", lambda _p: None)
    with (
        pytest.raises(gate.Refusal, match="unidentified"),
        gate.shim(tmp_path / "s.log"),
    ):
        pass


def test_the_holder_is_found_by_port_not_by_name() -> None:
    """Every process-identification bug in this repo came from matching a
    name: `pgrep -f` matched the quoting shell, the commit guard matched seven
    waiter shells, `foreign()` matched a command line instead of a binary. The
    port is the resource actually in conflict."""
    code = code_of(ROOT / "scripts" / "mtp_treatment_gate.py")
    body = code[code.index("def port_holder") : code.index("def server_command")]
    assert "lsof" in body and "-iTCP" in body
    assert "qwen_tool_shim" not in body, "identify by port, not by name"


def test_the_first_probe_failure_is_not_masked_by_a_later_success(
    monkeypatch, tmp_path
) -> None:
    """Two pad sizes run in one stage. A failure at pad=0 followed by a pass
    at pad=11000 must still report failure."""
    monkeypatch.setattr(gate, "arm", _null_context)
    codes = iter([3, 0])
    monkeypatch.setattr(gate, "_run", lambda *a, **k: next(codes))
    assert gate.stage_probe(tmp_path, tmp_path / "s.log", 1) == 3


def test_a_clean_probe_reports_success(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(gate, "arm", _null_context)
    monkeypatch.setattr(gate, "_run", lambda *a, **k: 0)
    assert gate.stage_probe(tmp_path, tmp_path / "s.log", 1) == 0


def test_a_foreign_shim_is_refused_not_killed(monkeypatch, tmp_path) -> None:
    """The shell ran `pkill -f qwen_tool_shim` and replaced whatever it found.
    We do not signal a process this project did not start (#252, #253), and we
    cannot restore a shim we did not start either."""
    monkeypatch.setattr(gate, "port_answers", lambda *a, **k: True)
    monkeypatch.setattr(gate.unitctl, "read", lambda *a, **k: None)
    monkeypatch.setattr(gate.unitctl, "state", lambda *a: gate.unitctl.STOPPED)
    with (
        pytest.raises(gate.Refusal, match="did not start"),
        gate.shim(tmp_path / "s.log"),
    ):
        pass


# --- the CLI -----------------------------------------------------------------


def test_every_documented_stage_is_dispatchable() -> None:
    for name in gate.STAGES:
        assert callable(gate.stage(name))


def test_an_unknown_stage_exits_two() -> None:
    with pytest.raises(SystemExit) as caught:
        gate.main(["nonsense"])
    assert caught.value.code == 2


def test_the_counter_variable_is_exported(monkeypatch) -> None:
    """#210 step 1: `counters_requested` reads this process's environment and
    `counters_on` reads the server's argv, so without this a row says
    `requested: false, on: true` -- and #210's own body reads a false there as
    "accepted: 0 means not measured". On this run it means measured and zero,
    which is the opposite."""
    monkeypatch.delenv("DS4_MTP_TIMING", raising=False)
    monkeypatch.setattr(
        gate, "run_lock", lambda *a, **k: (_ for _ in ()).throw(gate.Refusal("stop"))
    )
    gate.main(["treated"])
    assert gate.os.environ["DS4_MTP_TIMING"] == "1"


# --- what the port must not have brought across ------------------------------


def test_the_driver_does_not_look_for_processes_by_name() -> None:
    code = code_of(ROOT / "scripts" / "mtp_treatment_gate.py")
    assert "pgrep" not in code
    assert "pkill" not in code


def test_the_shell_it_replaces_is_still_here() -> None:
    assert SHELL.exists(), "see #235 -- do not delete until a run agrees"


# --- helpers -----------------------------------------------------------------


class _FakeUnit:
    pid = 4242


@contextlib.contextmanager
def _null_context(*args, **kwargs):
    yield


def _wire_shim(monkeypatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(gate.unitctl, "read", lambda *a, **k: None)
    monkeypatch.setattr(gate, "port_answers", lambda *a, **k: False)
    monkeypatch.setattr(gate, "_wait_for_port", lambda *a, **k: True)
    monkeypatch.setattr(
        gate.unitctl,
        "start",
        lambda *a, **k: events.append("shim-start") or _FakeUnit(),
    )
    monkeypatch.setattr(
        gate.unitctl, "stop", lambda *a, **k: events.append("shim-stop") or "stopped"
    )
    return events


def _wire(monkeypatch, tmp_path: pathlib.Path, *, rc: int, rows: int = 0) -> None:
    """No server, no client: only the gate's own decision is under test."""
    monkeypatch.setattr(gate, "arm", _null_context)

    def fake_run(argv, out):
        scratch = tmp_path / "bypass-scratch.jsonl"
        scratch.write_text("".join('{"row": 1}\n' for _ in range(rows)))
        return rc

    monkeypatch.setattr(gate, "_run", fake_run)


# ------------------------------------------------- the #235 retirement differential
#
# The port's claim is that, under identical inputs, it hands the measurement
# child (`run.py`) the same argv and the same environment the shell did. The
# shell's stage and the port's `stage_*` both invoke
# `uv run python benchmarks/agent/run.py`; a fake `uv` on PATH records the
# child's argv+env. The real `.sh` and the port's stage run against the same
# fake, and the recordings must agree on argv (order-free) and on env modulo
# the controlled base.
#
# The three run.py stages are covered: `treated` and `silent` (both exit 0 on
# both sides) and `bypass` (the gate fires on both sides -- the shell exits 1,
# the port raises Refusal -- but the run.py recording is captured before the
# refusal). The `probe`/`probe-shim`/`replay` stages run diagnostic scripts
# (`mtp_engagement.py`, `mtp_replay_probe.py`), not the measurement child, so
# they are out of scope here.

import equiv
import wait_ready

_SHELL_ARTIFACTS = frozenset({"PWD", "OLDPWD", "SHLVL", "_"})


def _meaningful_env(env: dict[str, str]) -> dict[str, str]:
    """The env minus the controlled base and the interpreter artifacts."""
    return {
        k: v
        for k, v in env.items()
        if k not in equiv.CONTROLLED_ENV_KEYS and k not in _SHELL_ARTIFACTS
    }


def _shell_run_invs(
    tmp_path: pathlib.Path, out: pathlib.Path, shim_dir: pathlib.Path, stage: str
) -> subprocess.CompletedProcess:
    """Run the real `.sh` for one stage against the fakes."""
    tree = tmp_path / "home" / "git" / "ds4-metal"
    equiv.write_fake_ds4_server(tree, out, ROOT)
    equiv.write_fake_pgrep(shim_dir)
    equiv.write_fake_pkill(shim_dir)
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path / "home"),
            "EQUIV_OUT": str(out),
            "EQUIV_ARM": "shell",
            "LOGDIR": str(tmp_path / "logs"),
            "TRIALS": "1",
        }
    )
    return subprocess.run(
        ["bash", str(SHELL), stage],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _port_run_invs(
    tmp_path: pathlib.Path,
    out: pathlib.Path,
    shim_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    stage_fn,
    server_log: pathlib.Path,
) -> int:
    """Run one port stage against the same fake; return its exit code.

    The port's orchestration is real except where it would touch the machine:
    the lock and the shim are no-ops, and the home-derived constants move into
    tmp so the server argv matches the shell's. `serving` is NOT stubbed: it
    spawns the fake ds4-server, which records its argv+env and writes the graph
    line to the log. Only the readiness poll is stubbed -- it waits for the
    fake's record instead of polling a real port. `_run` still spawns
    `uv run python run.py` through the fake `uv` on PATH.
    """
    home = tmp_path / "home"
    monkeypatch.setattr(gate, "run_lock", _null_context)
    monkeypatch.setattr(gate, "shim", _null_context)
    monkeypatch.setattr(
        wait_ready, "ready", lambda *a, **k: equiv.wait_for_program(out, "ds4-server")
    )
    # The shell resolves these against $HOME; the port computed them at import
    # from the real home. Point them at the tmp home so the server argv agrees.
    monkeypatch.setattr(gate, "DS4_TREE", home / "git" / "ds4-metal")
    monkeypatch.setattr(
        gate,
        "DS4_MODEL",
        home
        / "models"
        / "qwen3.8-flash-next-ds4-q4"
        / "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf",
    )
    monkeypatch.setattr(
        gate,
        "DS4_PLE",
        home
        / "models"
        / "qwen3.8-flash-next-ds4-q4"
        / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf",
    )
    monkeypatch.setattr(
        gate,
        "DS4_MTP",
        home
        / "models"
        / "qwen3.8-flash-next-ds4-q4"
        / "qwen3.8-flash-next-q4-mtp.gguf",
    )
    monkeypatch.setattr(gate, "KV_TREATED", home / ".ds4" / "server-kv-mtp")
    monkeypatch.setattr(gate, "KV_BYPASS", home / ".ds4" / "server-kv-210-bypass")
    # The port's process env must carry the same driver vars the shell's did,
    # so run.py sees the same base environment on both sides. DS4_MTP_TIMING
    # is exported by the shell and set by the port's `main`; a stage called
    # directly does not set it, so the differential does.
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EQUIV_OUT", str(out))
    monkeypatch.setenv("EQUIV_ARM", "port")
    monkeypatch.setenv("LOGDIR", str(tmp_path / "logs"))
    monkeypatch.setenv("TRIALS", "1")
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    return stage_fn(tmp_path / "logs", server_log, 1)


def _assert_agree(stage: str, shell: equiv.Invocation, port: equiv.Invocation) -> None:
    assert set(equiv.canonical(shell.argv, frozenset())) == set(
        equiv.canonical(port.argv, frozenset())
    ), f"{stage}: shell argv {shell.argv} vs port argv {port.argv}"
    assert _meaningful_env(shell.env) == _meaningful_env(port.env), (
        f"{stage}: shell env {_meaningful_env(shell.env)} vs "
        f"port env {_meaningful_env(port.env)}"
    )


def test_the_shell_and_the_port_hand_run_py_the_same_command(
    tmp_path, monkeypatch
) -> None:
    """The measurement child's argv and env agree between the two drivers.

    `treated` and `silent` exit 0 on both sides. `bypass` fires the gate on
    both sides (shell exit 1, port Refusal) but records run.py first, so its
    argv is still compared.
    """
    for stage, stage_fn, expect_refusal in (
        ("treated", gate.stage_treated, False),
        ("silent", gate.stage_silent, False),
        ("bypass", gate.stage_bypass, True),
    ):
        out = tmp_path / f"rec-{stage}.jsonl"
        shim_dir = tmp_path / f"shim-{stage}"
        shim_dir.mkdir()
        equiv.write_uv_fake_running_real(shim_dir / "uv", out, ROOT)

        shell_got = _shell_run_invs(tmp_path, out, shim_dir, stage)
        server_log = tmp_path / "logs" / f"ds4server-{stage}.log"
        if expect_refusal:
            assert shell_got.returncode != 0, (
                f"shell {stage} should be refused:\n{shell_got.stdout}\n{shell_got.stderr}"
            )
            with pytest.raises(gate.Refusal):
                _port_run_invs(
                    tmp_path, out, shim_dir, monkeypatch, stage_fn, server_log
                )
        else:
            assert shell_got.returncode == 0, (
                f"shell {stage} failed:\n{shell_got.stdout}\n{shell_got.stderr}"
            )
            port_rc = _port_run_invs(
                tmp_path, out, shim_dir, monkeypatch, stage_fn, server_log
            )
            assert port_rc == 0, f"port {stage} failed rc={port_rc}"

        invs = equiv.by_program(equiv.load(out), "run.py")
        shell = [i for i in invs if i.arm == "shell"]
        port = [i for i in invs if i.arm == "port"]
        assert len(shell) == 1, f"shell {stage} recorded {len(shell)} run.py calls"
        assert len(port) == 1, f"port {stage} recorded {len(port)} run.py calls"
        _assert_agree(stage, shell[0], port[0])

        # The treatment lives on the SERVER command line, not run.py's: the
        # treated/silent arms carry --mtp-model, the bypass arm must not. A
        # differential that compared only run.py would be green while the two
        # drivers started different servers.
        servers = equiv.by_program(equiv.load(out), "ds4-server")
        srv_shell = [i for i in servers if i.arm == "shell"]
        srv_port = [i for i in servers if i.arm == "port"]
        assert len(srv_shell) == 1, (
            f"shell {stage} recorded {len(srv_shell)} ds4-server calls"
        )
        assert len(srv_port) == 1, (
            f"port {stage} recorded {len(srv_port)} ds4-server calls"
        )
        _assert_agree(stage, srv_shell[0], srv_port[0])
        srv_pairs = equiv.canonical(srv_shell[0].argv, frozenset())
        has_mtp_model = any(p[0] == "--mtp-model" for p in srv_pairs)
        if stage == "bypass":
            assert not has_mtp_model, (
                f"bypass arm carried --mtp-model: {srv_shell[0].argv}"
            )
        else:
            assert has_mtp_model, f"{stage} arm lost --mtp-model: {srv_shell[0].argv}"
