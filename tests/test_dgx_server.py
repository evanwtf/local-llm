"""Recorded systemd-scope lifecycle for DGX servers (#429)."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import dgx_server


def _active(**extra: str) -> dict[str, str]:
    return {"ActiveState": "active", "SubState": "running", **extra}


def test_profiles_own_canonical_ports_bind_and_metrics():
    assert dgx_server.PROFILES["vllm"].port == 8030
    assert dgx_server.PROFILES["llamacpp"].port == 8020
    assert dgx_server.PROFILES["omni"].port == 8041
    assert dgx_server.PROFILES["clip"].port == 8042
    assert dgx_server._managed_command("vllm", ["vllm", "serve", "model"])[-4:] == [
        "--host",
        "0.0.0.0",
        "--port",
        "8030",
    ]
    assert "--metrics" in dgx_server._managed_command(
        "llamacpp", ["llama-server", "-m", "x"]
    )
    assert "--metrics" not in dgx_server._managed_command(
        "ds4", ["ds4-server", "--cuda", "-m", "x"]
    )


@pytest.mark.parametrize("option", ["--host", "--port", "--bind"])
def test_wrapper_refuses_callers_that_override_network_identity(option):
    with pytest.raises(ValueError, match="wrapper-owned"):
        dgx_server._managed_command("vllm", ["vllm", "serve", "x", option, "bad"])


def test_heavy_server_requires_a_memory_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(dgx_server, "read", lambda *a, **k: None)
    monkeypatch.setattr(dgx_server, "_unit_live", lambda *a: False)
    monkeypatch.setattr(dgx_server, "_port_open", lambda *a: False)
    with pytest.raises(ValueError, match="requires --memory-max"):
        dgx_server.start("vllm", ["vllm", "serve", "x"], "x", None, tmp_path / "x.log")


def test_start_records_scope_pid_unit_port_and_model(tmp_path, monkeypatch):
    seen = {}

    class Proc:
        pid = 77

        def poll(self):
            return None

    def popen(argv, **kwargs):
        # setdefault, not assignment: since #456 a heavy launch also spawns the
        # MemAvailable watcher, so the last Popen is not the server's.
        seen.setdefault("argv", argv)
        return Proc()

    monkeypatch.setattr(dgx_server, "read", lambda *a, **k: None)
    monkeypatch.setattr(dgx_server, "_unit_live", lambda *a: False)
    monkeypatch.setattr(dgx_server, "_port_open", lambda *a: False)
    monkeypatch.setattr(dgx_server.subprocess, "Popen", popen)
    monkeypatch.setattr(
        dgx_server,
        "unit_properties",
        lambda unit: _active(ControlGroup="/scope", MemoryCurrent="42"),
    )
    monkeypatch.setattr(dgx_server, "_scope_pid", lambda props, fallback: 88)
    server = dgx_server.start(
        "llamacpp",
        ["llama-server", "-m", "weights.gguf"],
        "qwen",
        "105G",
        tmp_path / "server.log",
        state_dir=tmp_path / "state",
    )
    assert server.pid == 88
    assert server.unit == "local-llm-llamacpp.scope"
    assert server.port == 8020
    assert server.model == "qwen"
    assert "--property=MemoryMax=105G" in seen["argv"]
    assert "--property=MemorySwapMax=0" in seen["argv"]
    assert seen["argv"][-5:] == ["--host", "0.0.0.0", "--port", "8020", "--metrics"]
    stored = json.loads((tmp_path / "state" / "llamacpp.json").read_text())
    assert stored["pid"] == 88
    assert stored["unit"] == "local-llm-llamacpp.scope"


def test_start_refuses_a_live_record(tmp_path, monkeypatch):
    server = dgx_server.Server(
        "vllm", 1, "local-llm-vllm.scope", 8030, "x", "96G", [], "x", "now", "host"
    )
    monkeypatch.setattr(dgx_server, "read", lambda *a, **k: server)
    monkeypatch.setattr(dgx_server, "_unit_live", lambda *a: True)
    with pytest.raises(RuntimeError, match="already running"):
        dgx_server.start("vllm", ["vllm", "serve", "x"], "x", "96G", tmp_path / "x.log")


def test_stop_addresses_recorded_unit_and_waits_for_port_and_memory(
    tmp_path, monkeypatch
):
    state = tmp_path / "state"
    state.mkdir()
    server = dgx_server.Server(
        "vllm", 8, "local-llm-vllm.scope", 8030, "x", "96G", [], "x", "now", "host"
    )
    (state / "vllm.json").write_text(json.dumps(server.as_dict()))
    calls = []
    monkeypatch.setattr(
        dgx_server,
        "_systemctl",
        lambda *args: (
            calls.append(args) or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )
    monkeypatch.setattr(dgx_server, "_unit_live", lambda unit: False)
    monkeypatch.setattr(dgx_server, "_port_open", lambda port: False)
    monkeypatch.setattr(dgx_server, "_mem_held_gib", lambda: 3.0)
    assert dgx_server.stop("vllm", state_dir=state)
    assert calls == [("stop", "local-llm-vllm.scope")]
    assert not (state / "vllm.json").exists()


def test_status_reports_scope_port_memory_and_served_model(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    server = dgx_server.Server(
        "vllm",
        8,
        "local-llm-vllm.scope",
        8030,
        "recorded",
        "96G",
        [],
        "x",
        "now",
        "host",
    )
    (state / "vllm.json").write_text(json.dumps(server.as_dict()))
    monkeypatch.setattr(
        dgx_server, "unit_properties", lambda unit: _active(MemoryCurrent="1234")
    )
    monkeypatch.setattr(dgx_server, "_port_open", lambda port: True)
    monkeypatch.setattr(dgx_server, "_endpoint", lambda port, path: (True, "served"))
    got = dgx_server.status("vllm", state)
    assert got["pid"] == 8
    assert got["unit_state"] == "active"
    assert got["port_open"] is True
    assert got["resident_bytes"] == 1234
    assert got["served_model"] == "served"


def test_plain_http_response_is_healthy(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b"<html>clip viewer</html>"

    monkeypatch.setattr(
        dgx_server.urllib.request, "urlopen", lambda *a, **k: Response()
    )
    assert dgx_server._endpoint(8042, "/") == (True, None)


# --- the memory watcher and MAX_JOBS (#456) ----------------------------------
#
# `MemoryMax` on the scope does not bound CUDA allocations on GB10: while
# Nemotron-3-Super loaded under MemoryMax=108G, nvidia-smi showed 71,776 MiB
# on the GPU against 3,762 MiB in the scope's own counter. So the wrapper
# watches MemAvailable itself, and caps the JIT build that exhausted the pool.


def _stub_launch(monkeypatch, seen):
    class Proc:
        pid = 77

        def poll(self):
            return None

    def popen(argv, **kwargs):
        seen.setdefault("argv", argv)
        seen.setdefault("spawned", []).append(argv)
        return Proc()

    monkeypatch.setattr(dgx_server, "read", lambda *a, **k: None)
    monkeypatch.setattr(dgx_server, "_unit_live", lambda *a: False)
    monkeypatch.setattr(dgx_server, "_port_open", lambda *a: False)
    monkeypatch.setattr(dgx_server.subprocess, "Popen", popen)
    monkeypatch.setattr(dgx_server, "unit_properties", lambda unit: _active())
    monkeypatch.setattr(dgx_server, "_scope_pid", lambda props, fallback: 88)


def test_start_caps_max_jobs_by_default(tmp_path, monkeypatch):
    """An unset MAX_JOBS let ninja run ~22 nvcc jobs during vLLM's warmup, on
    top of 69.6 GiB of loaded weights -- that, not the model, exhausted the
    pool twice on 2026-09-17 (#406)."""
    seen = {}
    _stub_launch(monkeypatch, seen)
    dgx_server.start(
        "vllm",
        ["vllm", "serve", "x"],
        "x",
        "108G",
        tmp_path / "s.log",
        state_dir=tmp_path / "state",
    )
    assert f"--setenv=MAX_JOBS={dgx_server.DEFAULT_MAX_JOBS}" in seen["argv"]


def test_an_explicit_max_jobs_in_the_command_wins(tmp_path, monkeypatch):
    """The caller said something deliberate; do not override it."""
    seen = {}
    _stub_launch(monkeypatch, seen)
    dgx_server.start(
        "vllm",
        ["env", "MAX_JOBS=8", "vllm", "serve", "x"],
        "x",
        "108G",
        tmp_path / "s.log",
        state_dir=tmp_path / "state",
    )
    assert not [a for a in seen["argv"] if a.startswith("--setenv=MAX_JOBS=")]


def test_max_jobs_can_be_disabled(tmp_path, monkeypatch):
    seen = {}
    _stub_launch(monkeypatch, seen)
    dgx_server.start(
        "vllm",
        ["vllm", "serve", "x"],
        "x",
        "108G",
        tmp_path / "s.log",
        state_dir=tmp_path / "state",
        max_jobs="",
    )
    assert not [a for a in seen["argv"] if a.startswith("--setenv=MAX_JOBS=")]


def test_a_heavy_server_records_its_floor_and_spawns_a_watcher(tmp_path, monkeypatch):
    seen = {}
    _stub_launch(monkeypatch, seen)
    monkeypatch.setattr(dgx_server, "_spawn_watcher", lambda name, floor, log: 4242)
    server = dgx_server.start(
        "vllm",
        ["vllm", "serve", "x"],
        "x",
        "108G",
        tmp_path / "s.log",
        state_dir=tmp_path / "state",
    )
    assert server.mem_floor_gib == dgx_server.MEM_FLOOR_GIB
    assert server.watcher_pid == 4242
    stored = json.loads((tmp_path / "state" / "vllm.json").read_text())
    assert stored["watcher_pid"] == 4242
    assert stored["mem_floor_gib"] == dgx_server.MEM_FLOOR_GIB


def test_a_light_server_gets_no_watcher(tmp_path, monkeypatch):
    """`clip` holds no model, so a MemAvailable floor would only add a process
    that can stop it for someone else's allocation."""
    seen = {}
    _stub_launch(monkeypatch, seen)
    monkeypatch.setattr(
        dgx_server, "_spawn_watcher", lambda *a: pytest.fail("no watcher for clip")
    )
    server = dgx_server.start(
        "clip",
        ["python", "-m", "http.server"],
        "clip",
        None,
        tmp_path / "s.log",
        state_dir=tmp_path / "state",
    )
    assert server.mem_floor_gib is None
    assert server.watcher_pid is None


def test_a_zero_floor_disables_the_watcher(tmp_path, monkeypatch):
    seen = {}
    _stub_launch(monkeypatch, seen)
    monkeypatch.setattr(
        dgx_server, "_spawn_watcher", lambda *a: pytest.fail("floor 0 means no watcher")
    )
    server = dgx_server.start(
        "vllm",
        ["vllm", "serve", "x"],
        "x",
        "108G",
        tmp_path / "s.log",
        state_dir=tmp_path / "state",
        mem_floor_gib=0,
    )
    assert server.mem_floor_gib is None


def test_a_failed_watcher_spawn_does_not_fail_the_launch(tmp_path, monkeypatch):
    """The server is the point; the watcher is the net. Losing the net is a
    warning, not a reason to have no server."""
    seen = {}
    _stub_launch(monkeypatch, seen)
    monkeypatch.setattr(dgx_server, "_spawn_watcher", lambda *a: None)
    server = dgx_server.start(
        "vllm",
        ["vllm", "serve", "x"],
        "x",
        "108G",
        tmp_path / "s.log",
        state_dir=tmp_path / "state",
    )
    assert server.watcher_pid is None
    assert server.mem_floor_gib == dgx_server.MEM_FLOOR_GIB


def _record(tmp_path, **extra):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    server = dgx_server.Server(
        "vllm",
        1,
        "local-llm-vllm.scope",
        8030,
        "x",
        "108G",
        [],
        str(tmp_path / "s.log"),
        "now",
        "host",
        **extra,
    )
    (state / "vllm.json").write_text(json.dumps(server.as_dict()))
    return state


def test_watch_stops_the_scope_below_the_floor(tmp_path, monkeypatch):
    state = _record(tmp_path, mem_floor_gib=14.0)
    stopped = []
    monkeypatch.setattr(dgx_server, "_unit_live", lambda *a: True)
    monkeypatch.setattr(dgx_server, "mem_available_gib", lambda: 13.4)
    monkeypatch.setattr(dgx_server, "_systemctl", lambda *a: stopped.append(a))
    assert dgx_server.watch("vllm", floor_gib=14.0, state_dir=state) == 1
    assert stopped == [("stop", "local-llm-vllm.scope")]


def test_watch_leaves_a_server_above_the_floor_alone(tmp_path, monkeypatch):
    """It exits when the unit goes away, which is the normal ending."""
    state = _record(tmp_path)
    live = iter([True, True, False])
    monkeypatch.setattr(dgx_server, "_unit_live", lambda *a: next(live))
    monkeypatch.setattr(dgx_server, "mem_available_gib", lambda: 26.0)
    monkeypatch.setattr(
        dgx_server, "_systemctl", lambda *a: pytest.fail("must not stop a healthy unit")
    )
    monkeypatch.setattr(dgx_server.time, "sleep", lambda s: None)
    assert dgx_server.watch("vllm", floor_gib=14.0, state_dir=state) == 0


def test_an_unreadable_meminfo_never_kills_a_server(tmp_path, monkeypatch):
    """Unknown is not "full": treating a missing reading as a floor breach
    would stop a healthy server for a parse error."""
    state = _record(tmp_path)
    live = iter([True, True, False])
    monkeypatch.setattr(dgx_server, "_unit_live", lambda *a: next(live))
    monkeypatch.setattr(dgx_server, "mem_available_gib", lambda: None)
    monkeypatch.setattr(
        dgx_server, "_systemctl", lambda *a: pytest.fail("must not stop on unknown")
    )
    monkeypatch.setattr(dgx_server.time, "sleep", lambda s: None)
    assert dgx_server.watch("vllm", floor_gib=14.0, state_dir=state) == 0


def test_watch_without_a_record_says_so(tmp_path):
    assert dgx_server.watch("vllm", state_dir=tmp_path / "empty") == 2


def test_mem_available_reads_meminfo(monkeypatch, tmp_path):
    fake = tmp_path / "meminfo"
    fake.write_text("MemTotal:  127654321 kB\nMemAvailable:  29360128 kB\n")
    monkeypatch.setattr(dgx_server.pathlib, "Path", lambda p: fake)
    assert abs(dgx_server.mem_available_gib() - 28.0) < 0.01


def test_status_reports_memavailable_and_whether_the_watcher_is_alive(
    tmp_path, monkeypatch
):
    state = _record(tmp_path, watcher_pid=999999, mem_floor_gib=14.0)
    monkeypatch.setattr(dgx_server, "unit_properties", lambda unit: _active())
    monkeypatch.setattr(dgx_server, "_endpoint", lambda port, path: (True, "x"))
    monkeypatch.setattr(dgx_server, "_port_open", lambda *a: True)
    monkeypatch.setattr(dgx_server, "mem_available_gib", lambda: 26.4)
    monkeypatch.setattr(dgx_server, "_pid_live", lambda pid: pid == 999999)
    result = dgx_server.status("vllm", state_dir=state)
    assert result["mem_available_gib"] == 26.4
    assert result["watcher_live"] is True


def test_a_record_written_before_the_watcher_still_loads(tmp_path):
    """A server started by the previous version has neither field; dropping
    the record would make a live server look unrecorded and unstoppable."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "vllm.json").write_text(
        json.dumps(
            {
                "name": "vllm",
                "pid": 5,
                "unit": "local-llm-vllm.scope",
                "port": 8030,
                "model": "x",
                "memory_max": "108G",
                "command": ["vllm"],
                "log": "/tmp/x.log",
                "started": "now",
                "hostname": "host",
            }
        )
    )
    server = dgx_server.read("vllm", state)
    assert server is not None
    assert server.watcher_pid is None
    assert server.mem_floor_gib is None
