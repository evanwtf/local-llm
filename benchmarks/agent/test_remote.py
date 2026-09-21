"""Remote mode: harness on a client, model on another machine. #562"""

from __future__ import annotations

import io
import pathlib
import sys
import types

import remote
import run

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import memory_gate

NODE = """\
# HELP node_memory_MemAvailable_bytes Memory information field MemAvailable_bytes.
node_memory_MemAvailable_bytes 2.5832173568e+10
node_memory_MemFree_bytes 9.154465792e+09
node_memory_MemTotal_bytes 1.30663165952e+11
"""


def test_a_loopback_url_points_at_the_server_and_keeps_its_port():
    assert remote.rewrite_url("http://127.0.0.1:8030", "srv") == "http://srv:8030"
    assert remote.rewrite_url("http://localhost:8888/v1", "srv") == "http://srv:8888/v1"


def test_a_non_loopback_url_is_left_alone():
    assert remote.rewrite_url("https://api.example.com/v1", "srv") == (
        "https://api.example.com/v1"
    )
    assert remote.rewrite_url(None, "srv") is None


def test_every_url_a_backend_carries_is_rewritten():
    backend = {
        "base_url": "http://127.0.0.1:8030",
        "props_url": "http://127.0.0.1:8020/props",
        "model": "m",
    }
    out = remote.rewrite(backend, "srv")
    assert out["base_url"] == "http://srv:8030"
    assert out["props_url"] == "http://srv:8020/props"
    assert out["model"] == "m"
    assert backend["base_url"] == "http://127.0.0.1:8030"  # the original is untouched


def test_node_exporter_memory_is_read_in_gib():
    got = remote.parse_node_meminfo(NODE)
    assert round(got["MemAvailable"], 1) == 24.1
    assert round(got["MemTotal"], 1) == 121.7


def test_stamp_makes_the_row_the_servers_and_keeps_the_client_aside():
    env = {
        "arch": "x86_64",
        "cpu": "Intel(R) Core(TM) i3-7100 CPU @ 3.90GHz",
        "memory_gib": 15.0,
        "confinement": "bwrap",
        "opencode": "1.18.31",
        "harness_head": "abc1234",
        "vllm": None,
    }
    facts = {
        "directory": "Cortex-X925-128GB-GB10",
        "facts": {"arch": "aarch64", "memory_gib": 121.7, "gpu": "NVIDIA GB10"},
        "env": {
            "vllm": "0.29.0",
            "server_argv": "vllm serve m",
            "opencode": "9.9.9",  # the server's own client version must not win
            "harness_head": "zzz9999",
        },
    }
    out = remote.stamp(env, facts)
    assert out["arch"] == "aarch64" and out["memory_gib"] == 121.7
    assert "cpu" not in out  # the server did not report one; the client's must not stay
    assert out["client_machine"]["arch"] == "x86_64"
    assert out["client_machine"]["confinement"] == "bwrap"
    assert out["vllm"] == "0.29.0" and out["server_argv"] == "vllm serve m"
    assert out["opencode"] == "1.18.31" and out["harness_head"] == "abc1234"
    assert out["topology"] == "remote"
    assert out["server_machine"] == "Cortex-X925-128GB-GB10"


def test_rows_go_to_the_servers_ledger():
    path = remote.results_path(
        pathlib.Path("/repo"), {"directory": "Cortex-X925-128GB-GB10"}
    )
    assert path == pathlib.Path("/repo/hardware/Cortex-X925-128GB-GB10/results.jsonl")


def test_no_server_host_means_a_local_run(monkeypatch):
    monkeypatch.delenv(remote.ENV_HOST, raising=False)
    assert remote.host() is None


def test_mem_available_reads_the_server_in_remote_mode(monkeypatch):
    monkeypatch.setenv(remote.ENV_HOST, "srv")
    monkeypatch.setattr(remote, "mem_available_gib", lambda server: 42.0)
    assert run.mem_available_gib() == 42.0


def test_the_headroom_gate_ignores_the_client_cap_in_remote_mode(monkeypatch):
    """The trial's memory is on the client, so it cannot push the server down."""
    monkeypatch.setenv(remote.ENV_HOST, "srv")
    monkeypatch.setattr(run, "mem_available_gib", lambda: 20.0)
    monkeypatch.setattr(run, "CLIENT_MEM_CAP_GIB", 24.0)
    args = types.SimpleNamespace(memory_gate_gib=18, server_floor_gib=13)
    # Locally this would be 20 - 24 - 13 < 0 and refuse; remotely it is 20 - 13.
    assert run._headroom_gate(args) == 7.0


def test_memory_gate_reads_a_node_exporter(monkeypatch):
    import urllib.request

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=5: io.BytesIO(NODE.encode())
    )
    got = memory_gate._node_exporter_meminfo("http://srv:9100/metrics")
    assert got["avail_gib"] == 24.1 and got["total_gib"] == 121.7


def test_the_servers_gpu_power_is_read_from_dcgm():
    text = (
        'DCGM_FI_DEV_POWER_USAGE{gpu="0",modelName="NVIDIA GB10"} 9.823000\n'
        'DCGM_FI_DEV_GPU_TEMP{gpu="0"} 38\n'
    )
    assert remote.parse_dcgm_watts(text) == 9.823
    assert remote.parse_dcgm_watts("nothing here\n") is None


def test_in_sandbox_layout_the_tripwire_watches_the_harness_clone(
    monkeypatch, tmp_path
):
    """Not the operator's checkout, which the trial never touches (#146, #562)."""
    clone = tmp_path / "sandbox" / "gmail-archive"
    (clone / ".git").mkdir(parents=True)
    monkeypatch.setattr(run, "SANDBOX_ROOT", tmp_path / "sandbox")
    operator = tmp_path / "git" / "gmail-archive"
    assert run.guarded_repo(operator, "sandbox") == clone
    assert run.guarded_repo(operator, "legacy") == operator


def test_topology_must_match_the_run():
    backends = {"local": {}, "far": {"topology": "remote"}}
    assert remote.topology_mismatch(backends, remote_mode=True) == ["local"]
    assert remote.topology_mismatch(backends, remote_mode=False) == ["far"]


def test_every_remote_backend_has_a_local_twin_with_the_same_model():
    """A remote backend is the same config reached from elsewhere (#562)."""
    import tomllib

    cfg = tomllib.loads((pathlib.Path(run.__file__).parent / "tasks.toml").read_text())
    for name, b in cfg["backend"].items():
        if b.get("topology") == "remote":
            assert b.get("model"), name
            assert b.get("base_url", "").startswith("http://127.0.0.1"), name


def test_an_unknown_the_server_contradicts_is_dropped():
    """The client cannot see the server's engine; the server just said (#562)."""
    env = {"sglang_version": "unknown", "ds4_version": "unknown"}
    facts = {"directory": "d", "env": {"sglang": "0.0.0.dev1+g708f51e44"}}
    out = remote.stamp(env, facts)
    assert "sglang_version" not in out
    assert out["sglang"] == "0.0.0.dev1+g708f51e44"
    # An engine the server said nothing about keeps its honest "unknown".
    assert out["ds4_version"] == "unknown"


def test_the_client_image_travels_with_the_clients_facts():
    """#611: without this the grouping key reads None for client_image and a
    containerised row pools with a bare-metal one from the same box."""
    env = {
        "arch": "x86_64",
        "cpu": "Intel(R) Core(TM) i3-7100 CPU @ 3.90GHz",
        "memory_gib": 15.0,
        "confinement": "bwrap",
        "client_image": "opencode=1.18.31 uv=0.12.13 python=3.14.4",
    }
    out = remote.stamp(env, {"directory": "d", "facts": {"arch": "aarch64"}})
    assert out["client_machine"]["client_image"].startswith("opencode=1.18.31")
    assert "client_image" not in out


def test_the_client_memory_cap_is_stamped_and_travels_with_the_client():
    """#477: the cap decides which tasks finish, so a row must say which one.

    `mbox-scan` peaks at 17.1 GiB on the desktop: excluded at 16, completes at
    24. Two arms taken at different caps are not one sample, and until this
    they were indistinguishable in the ledger.
    """
    env = {
        "arch": "x86_64",
        "cpu": "AMD Ryzen 9 7900X 12-Core Processor",
        "confinement": "bwrap",
        "client_mem_cap_gib": 8.0,
    }
    facts = {"directory": "d", "facts": {"arch": "aarch64"}}
    out = remote.stamp(env, facts)
    assert out["client_machine"]["client_mem_cap_gib"] == 8.0
    # It describes the CLIENT, so it must not be left at the top level, where
    # the row's own hardware identity lives.
    assert "client_mem_cap_gib" not in out


def test_two_caps_are_two_clients():
    """The whole point: the same box at two caps must not pool into one cell."""
    import results

    def row(cap):
        return {
            "env": {
                "topology": "remote",
                "client_machine": {
                    "arch": "x86_64",
                    "cpu": "AMD Ryzen 9 7900X 12-Core Processor",
                    "memory_gib": 30.5,
                    "confinement": "bwrap",
                    "client_image": None,
                    "client_mem_cap_gib": cap,
                },
            }
        }

    assert results.client_identity(row(8.0)) != results.client_identity(row(24.0))
    assert results.client_label(row(8.0)).endswith("@8g")
    assert results.client_label(row(24.0)).endswith("@24g")


def test_the_stamped_default_matches_the_cap_the_harness_enforces():
    """preflight reads the env; run owns the constant. They must not drift."""
    import preflight

    assert preflight.client_mem_cap_gib() == run.CLIENT_MEM_CAP_GIB


def test_a_disabled_cap_is_not_stamped(monkeypatch):
    """`0` disables the cap, and "no cap" is not the same fact as "24 GiB"."""
    import preflight

    monkeypatch.setenv("LOCAL_LLM_CLIENT_MEM_CAP_GIB", "0")
    assert preflight.client_mem_cap_gib() is None
    assert "client_mem_cap_gib" not in preflight.machine_facts()
