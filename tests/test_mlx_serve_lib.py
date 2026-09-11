"""The mlx-serve lifecycle library (#235, replacing lib/mlx_serve.sh).

Real processes, not mocks, wherever a real one can stand in: the failures this
module exists to prevent are all failures of process handling, and a mock that
agrees with a wrong mental model proves nothing. `sleep` is the stand-in for a
server; the module never inspects what it started.

The stake is specific. mlx-serve holds ~100 GiB resident, and until 2026-09-07
preflight could not see it at all -- a leaked server read as an empty machine
with 100 GiB of headroom that did not exist.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import mlx_serve
import preflight
import unitctl
from source_text import code_of

SERVER = ["sleep", "60"]
LIB = ROOT / "scripts" / "lib" / "mlx_serve.py"


@pytest.fixture(autouse=True)
def empty_census(monkeypatch) -> None:
    """No mlx-serve on the machine, unless a test says otherwise.

    `start()` consults the real process census, so without this the suite
    would pass or fail depending on whether a server happened to be up on the
    machine running it -- and this repo's machine runs servers for a living. A
    test that wants a foreign server substitutes one explicitly.
    """
    monkeypatch.setattr(mlx_serve.preflight, "_capture", lambda _argv: "")


@pytest.fixture
def state(tmp_path) -> pathlib.Path:
    """A private unit directory, so a test never touches a real server."""
    return tmp_path / "units"


def serving(state: pathlib.Path, tmp_path: pathlib.Path, name: str = "s.log"):
    """The context manager under test, short enough to sit in a `with` tuple."""
    return mlx_serve.serving(SERVER, tmp_path / name, cwd=ROOT, state_dir=state)


def alive(pid: int) -> bool:
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    stat = out.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


# --- the lifecycle -----------------------------------------------------------


def test_serving_starts_a_server_and_stops_it(state, tmp_path) -> None:
    with mlx_serve.serving(
        SERVER, tmp_path / "s.log", cwd=ROOT, state_dir=state
    ) as unit:
        assert mlx_serve.running(state)
        pid = unit.pid
        assert alive(pid)
    assert not mlx_serve.running(state)
    assert not alive(pid), "~100 GiB stays resident if this leaks"


def test_the_server_is_stopped_when_the_block_raises(state, tmp_path) -> None:
    """#145: `stack_agent_ab.sh` restarted the server between sweeps and
    stopped none of them, so every clean finish left the last arm's server
    resident -- 97.9 GiB, four runs in a row. A `finally` cannot forget."""
    pid = None
    with pytest.raises(RuntimeError, match="boom"), serving(state, tmp_path) as u:
        pid = u.pid
        raise RuntimeError("boom")
    assert pid is not None and not alive(pid)


def test_stop_is_idempotent(state, tmp_path) -> None:
    """`start()` calls it on the normal path and `serving()` calls it again in
    its `finally`. That is only harmless because a stale or absent record is a
    no-op -- load-bearing, not incidental."""
    assert mlx_serve.stop(state_dir=state) == unitctl.STOPPED
    assert mlx_serve.stop(state_dir=state) == unitctl.STOPPED
    with serving(state, tmp_path):
        pass
    assert mlx_serve.stop(state_dir=state) == unitctl.STOPPED


def test_start_stops_a_leftover_first(state, tmp_path) -> None:
    """ "An arm starts from a clean slate" is this module's invariant, and it
    lives here rather than in the driver so a driver cannot forget it."""
    first = mlx_serve.start(SERVER, tmp_path / "a.log", cwd=ROOT, state_dir=state)
    second = mlx_serve.start(SERVER, tmp_path / "b.log", cwd=ROOT, state_dir=state)
    assert second.pid != first.pid
    assert not alive(first.pid), "the leftover held ~100 GiB"
    mlx_serve.stop(state_dir=state)


# --- stop, then prove it stopped ---------------------------------------------


def test_stop_and_prove_confirms_the_server_is_gone(state, tmp_path) -> None:
    mlx_serve.start(SERVER, tmp_path / "s.log", cwd=ROOT, state_dir=state)
    assert mlx_serve.stop_and_prove("test", state_dir=state) == unitctl.RUNNING
    assert not mlx_serve.running(state)


def test_a_server_that_will_not_stop_raises(monkeypatch, state) -> None:
    """A stop that is not verified is a stop that reports success while
    100 GiB stays resident."""
    monkeypatch.setattr(mlx_serve, "stop", lambda *a, **k: unitctl.RUNNING)
    monkeypatch.setattr(mlx_serve, "running", lambda *a: True)
    with pytest.raises(mlx_serve.WouldNotStop):
        mlx_serve.stop_and_prove("test", state_dir=state)


def test_a_teardown_failure_warns_and_does_not_mask_the_real_error(
    monkeypatch, state, tmp_path, caplog
) -> None:
    """A teardown failure must not replace the exception already propagating.
    The shell had the same rule: `|| echo "WARNING: ..."`, never a hard exit."""
    monkeypatch.setattr(
        mlx_serve,
        "stop_and_prove",
        lambda *a, **k: (_ for _ in ()).throw(mlx_serve.WouldNotStop("stuck")),
    )
    with (
        caplog.at_level("WARNING", logger=mlx_serve.logger.name),
        pytest.raises(ValueError, match="the real error"),
        serving(state, tmp_path),
    ):
        raise ValueError("the real error")
    assert "survived teardown" in caplog.text
    unitctl.stop(mlx_serve.UNIT, state_dir=state)


def test_a_teardown_failure_on_a_clean_block_does_not_raise(
    monkeypatch, state, tmp_path, caplog
) -> None:
    """Same rule with nothing propagating: still a warning, still not a raise,
    or a finished run reports failure because its cleanup was untidy."""
    monkeypatch.setattr(
        mlx_serve,
        "stop_and_prove",
        lambda *a, **k: (_ for _ in ()).throw(mlx_serve.WouldNotStop("stuck")),
    )
    with (
        caplog.at_level("WARNING", logger=mlx_serve.logger.name),
        mlx_serve.serving(SERVER, tmp_path / "s.log", cwd=ROOT, state_dir=state),
    ):
        pass
    assert "survived teardown" in caplog.text
    unitctl.stop(mlx_serve.UNIT, state_dir=state)


# --- what the port must not have brought across ------------------------------


def test_the_library_does_not_look_for_processes_by_name() -> None:
    """`pgrep -f mlx-serve` matches the shell running the pgrep -- the
    self-match trap the shell carried `--model` to dodge. Bracketing only ever
    protected against *self*-match; a third process quoting the string matches
    too. A recorded pid has neither problem."""
    code = code_of(LIB)
    assert "pgrep" not in code
    assert "pkill" not in code
    assert "MLX_SERVE_PATTERN" not in code
    assert "pgrep" in LIB.read_text(), "the docstring should still explain why"


def test_a_foreign_server_is_reported_and_not_killed() -> None:
    """A resident mlx-serve this project did not start is somebody else's
    process, and `pkill`-ing it by name is what #235 exists to remove.
    `foreign()` returns; it never signals."""
    code = code_of(LIB)
    body = code[code.index("def foreign") : code.index("def stop")]
    for signal_word in ("kill", "terminate", "signal", "SIGKILL", "SIGTERM"):
        assert signal_word not in body, f"foreign() must not {signal_word}"


def test_our_own_server_is_not_foreign(monkeypatch, state, tmp_path) -> None:
    """ "Foreign" must mean "not ours". An earlier version matched every
    mlx-serve process including our own unit, which made the word untrue
    whenever we had a server up. Raised by @deepseek reviewing #252.

    A real unit, so the pid the record holds is a pid that exists; only the
    census is substituted, because this machine has no mlx-serve running."""
    unit = mlx_serve.start(SERVER, tmp_path / "s.log", cwd=ROOT, state_dir=state)
    census = [
        preflight.Proc(pid=unit.pid, rss_gib=100.0, command="mlx-serve --model ours"),
        preflight.Proc(pid=unit.pid + 99999, rss_gib=100.0, command="mlx-serve --x"),
    ]
    monkeypatch.setattr(preflight, "parse_ps", lambda _text: census)
    found = mlx_serve.foreign(state)

    assert [p.pid for p in found] == [unit.pid + 99999]
    mlx_serve.stop(state_dir=state)


def test_start_refuses_when_a_foreign_server_is_resident(
    monkeypatch, state, tmp_path
) -> None:
    """Stopping our own unit is only half the invariant. Starting beside a
    foreign server is ~200 GiB on a 128 GiB machine, and the symptom is not a
    crash -- it is a run that swaps, and rows that are slow for a reason
    nobody records."""
    monkeypatch.setattr(
        mlx_serve,
        "foreign",
        lambda *a: [
            preflight.Proc(pid=4243, rss_gib=99.9, command="mlx-serve --model y")
        ],
    )
    with pytest.raises(mlx_serve.ForeignServer, match="4243"):
        mlx_serve.start(SERVER, tmp_path / "s.log", cwd=ROOT, state_dir=state)
    assert not mlx_serve.running(state), "nothing may start beside a foreign server"


def test_the_refusal_names_the_memory_at_stake(monkeypatch, state, tmp_path) -> None:
    """A refusal an operator cannot act on gets overridden rather than obeyed:
    it must say which process and how much it holds."""
    monkeypatch.setattr(
        mlx_serve,
        "foreign",
        lambda *a: [
            preflight.Proc(pid=4243, rss_gib=99.9, command="mlx-serve --model y")
        ],
    )
    with pytest.raises(mlx_serve.ForeignServer) as caught:
        mlx_serve.start(SERVER, tmp_path / "s.log", cwd=ROOT, state_dir=state)
    assert "99.9 GiB" in str(caught.value)


def test_the_refusal_can_be_overridden_deliberately(
    monkeypatch, state, tmp_path
) -> None:
    """A refusal with no way through gets deleted rather than satisfied. It
    must be possible, and it must be an explicit act."""
    monkeypatch.setattr(
        mlx_serve,
        "foreign",
        lambda *a: [
            preflight.Proc(pid=4243, rss_gib=99.9, command="mlx-serve --model y")
        ],
    )
    unit = mlx_serve.start(
        SERVER, tmp_path / "s.log", cwd=ROOT, allow_foreign=True, state_dir=state
    )
    assert alive(unit.pid)
    mlx_serve.stop(state_dir=state)


def test_a_clean_machine_does_not_refuse(monkeypatch, state, tmp_path) -> None:
    """The negative case. Without it the guard could refuse everything and the
    library would be unreachable until an overnight run produced nothing."""
    monkeypatch.setattr(mlx_serve, "foreign", lambda *a: [])
    unit = mlx_serve.start(SERVER, tmp_path / "s.log", cwd=ROOT, state_dir=state)
    assert alive(unit.pid)
    mlx_serve.stop(state_dir=state)


def test_the_shell_it_replaces_is_still_here() -> None:
    """#235: deleted only after a run agrees."""
    assert (ROOT / "vault" / "lib" / "mlx_serve.sh").exists()


def test_a_shell_that_merely_mentions_the_server_is_not_one() -> None:
    """`foreign()` matches the executable (`Proc.short`), not the whole command
    line. The shell this module replaced carried `--model` in its pgrep pattern
    precisely to dodge this, and it only ever dodged SELF-match."""
    census = [
        preflight.Proc(
            pid=1, rss_gib=0.1, command="/bin/bash -c 'mlx-serve --model x'"
        ),
        preflight.Proc(pid=2, rss_gib=99.9, command="/g/mlx-serve/mlx-serve --model x"),
    ]
    assert [p.pid for p in census if mlx_serve.PROCESS in p.short] == [2]


# ---------------------------------------------- draft source recording (#262)
#
# mlx-serve speculates by default via PLD and no row recorded it: 196 rows were
# PLD-on and silent. The guard is that a PLD run is labelled PLD, and that a
# real MTP head or drafter overrides it -- so a later reader is never told a
# speculated number was greedy.


def test_our_pack_ships_no_head_so_the_source_is_pld():
    assert (
        mlx_serve.resolve_draft_source(
            has_mtp=False, has_drafter=False, pld_enabled=True
        )
        == "PLD"
    )


def test_an_mtp_head_wins_over_everything():
    assert (
        mlx_serve.resolve_draft_source(has_mtp=True, has_drafter=True, pld_enabled=True)
        == "MTP"
    )


def test_a_drafter_wins_over_pld_but_not_mtp():
    assert (
        mlx_serve.resolve_draft_source(
            has_mtp=False, has_drafter=True, pld_enabled=True
        )
        == "drafter"
    )


def test_no_pld_and_no_head_is_none_not_a_silent_pld():
    assert (
        mlx_serve.resolve_draft_source(
            has_mtp=False, has_drafter=False, pld_enabled=False
        )
        == "none"
    )


def test_draft_settings_default_to_pld_on_when_no_flags():
    s = mlx_serve.draft_settings(["mlx-serve", "--model", "/m", "--serve"])
    assert s == {"pld_enabled": True, "pld_draft_len": 5, "pld_key_len": 3}


def test_no_pld_flag_disables_pld():
    assert mlx_serve.draft_settings(["mlx-serve", "--no-pld"])["pld_enabled"] is False


def test_pld_tuning_is_read_from_the_argv():
    s = mlx_serve.draft_settings(
        ["mlx-serve", "--pld", "--pld-draft-len", "8", "--pld-key-len", "4"]
    )
    assert (s["pld_draft_len"], s["pld_key_len"]) == (8, 4)


def test_provenance_for_our_default_pack_reads_pld(tmp_path):
    model = tmp_path / "Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
    model.mkdir()  # ships neither mtp/ nor drafter/
    line = mlx_serve.draft_provenance(model, ["mlx-serve", "--model", str(model)])
    assert line == "draft_source=PLD pld=on pld_draft_len=5 pld_key_len=3"


def test_provenance_sees_an_mtp_head_on_disk(tmp_path):
    model = tmp_path / "pack"
    (model / "mtp").mkdir(parents=True)
    (model / "mtp" / "weights.safetensors").write_bytes(b"")
    line = mlx_serve.draft_provenance(model, ["mlx-serve", "--model", str(model)])
    assert line.startswith("draft_source=MTP")


# The structured form a row carries (#262). Flat keys, lower-case source, and
# no `pld` key -- that fact is engine_identity.pld_state()'s, with its own "n/a".


def test_draft_provenance_fields_is_structured_and_lower_case(tmp_path):
    model = tmp_path / "Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"
    model.mkdir()  # ships neither mtp/ nor drafter/
    fields = mlx_serve.draft_provenance_fields(
        model, ["mlx-serve", "--model", str(model)]
    )
    assert fields == {"draft_source": "pld", "pld_draft_len": 5, "pld_key_len": 3}
    assert "pld" not in fields  # that is pld_state()'s key, not this one


def test_draft_provenance_fields_sees_an_mtp_head(tmp_path):
    model = tmp_path / "pack"
    (model / "mtp").mkdir(parents=True)
    (model / "mtp" / "weights.safetensors").write_bytes(b"")
    fields = mlx_serve.draft_provenance_fields(
        model, ["mlx-serve", "--model", str(model)]
    )
    assert fields["draft_source"] == "mtp"


def test_draft_provenance_fields_read_the_pld_tuning(tmp_path):
    model = tmp_path / "pack"
    model.mkdir()
    fields = mlx_serve.draft_provenance_fields(
        model,
        [
            "mlx-serve",
            "--model",
            str(model),
            "--pld-draft-len",
            "8",
            "--pld-key-len",
            "4",
        ],
    )
    assert (fields["pld_draft_len"], fields["pld_key_len"]) == (8, 4)


def test_model_dir_of_extracts_the_model_path():
    assert mlx_serve.model_dir_of(["mlx-serve", "--model", "/x/y", "--serve"]) == (
        pathlib.Path("/x/y")
    )
    assert mlx_serve.model_dir_of(["mlx-serve", "--serve"]) is None
