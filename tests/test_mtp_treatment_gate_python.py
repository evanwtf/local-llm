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
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mtp_treatment_gate as gate
from source_text import code_of

SHELL = ROOT / "scripts" / "mtp_treatment_gate.sh"
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
