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
        seen["argv"] = argv
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
