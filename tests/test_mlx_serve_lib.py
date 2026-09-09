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
import unitctl
from source_text import code_of

SERVER = ["sleep", "60"]
LIB = ROOT / "scripts" / "lib" / "mlx_serve.py"


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
    """`lib/ds4_server.py` already made this choice. A resident mlx-serve this
    project did not start is somebody else's process, and `pkill`-ing it by
    name is what #235 exists to remove. `foreign()` returns; it never signals."""
    code = code_of(LIB)
    body = code[code.index("def foreign") : code.index("def stop")]
    for signal_word in ("kill", "terminate", "signal", "SIGKILL", "SIGTERM"):
        assert signal_word not in body, f"foreign() must not {signal_word}"


def test_the_shell_it_replaces_is_still_here() -> None:
    """#235: deleted only after a run agrees."""
    assert (ROOT / "scripts" / "lib" / "mlx_serve.sh").exists()
