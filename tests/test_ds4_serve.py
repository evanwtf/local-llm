"""The route must be proved, and two servers must not coexist."""

from __future__ import annotations

import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import ds4_serve

FAST = "ds4: Metal 4 tensor API enabled for Tensor kernels"
VANILLA = (
    "ds4: Metal 4 tensor API available but not enabled (numerics); "
    "set DS4_METAL_ENABLE_TENSOR=1 to override"
)


def test_each_route_confirms_only_itself():
    assert ds4_serve.confirm_route(FAST, "fast")
    assert ds4_serve.confirm_route(VANILLA, "vanilla")
    assert not ds4_serve.confirm_route(FAST, "vanilla")
    assert not ds4_serve.confirm_route(VANILLA, "fast")


def test_a_log_with_no_route_line_confirms_nothing():
    """Silence is not consent. A server that never said which route it took
    must not be handed over as either."""
    assert not ds4_serve.confirm_route("ds4: warming mapped tensor pages\n", "fast")
    assert not ds4_serve.confirm_route("", "vanilla")


def test_the_vanilla_line_mentions_the_fast_override_and_still_is_not_fast():
    """The vanilla message names DS4_METAL_ENABLE_TENSOR, so a naive
    substring check for the env var, or for 'tensor API', would read it as
    the fast route."""
    assert "DS4_METAL_ENABLE_TENSOR" in VANILLA
    assert not ds4_serve.confirm_route(VANILLA, "fast")


def test_a_log_claiming_both_routes_confirms_neither():
    assert not ds4_serve.confirm_route(FAST + "\n" + VANILLA, "fast")
    assert not ds4_serve.confirm_route(FAST + "\n" + VANILLA, "vanilla")


def test_the_modes_take_separate_kv_directories():
    """ds4's disk cache is cross-quant=accept, so a shared directory would let
    one route resume the other's checkpoint."""
    assert ds4_serve.kv_dir("fast") != ds4_serve.kv_dir("vanilla")


def test_a_free_port_has_no_holder():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert ds4_serve.port_holder(free) is None


def test_an_occupied_port_reports_a_holder():
    """Both modes share a port so the OS enforces one server at a time."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert ds4_serve.port_holder(port) is not None


def test_it_refuses_when_the_binary_is_missing(tmp_path, caplog):
    with caplog.at_level("ERROR", logger="ds4_serve"):
        code = ds4_serve.main(["fast", "--tree", str(tmp_path)])
    assert code == 1
    assert "Withhold the automatic Metal 4 tensor enable" in caplog.text


def test_the_command_carries_the_mode_s_own_kv_dir(tmp_path):
    import argparse

    args = argparse.Namespace(
        model=tmp_path / "m.gguf", ple=tmp_path / "p.gguf", ctx=99, kv=None, port=8000
    )
    cmd = ds4_serve.build_command("vanilla", args, [])
    assert str(ds4_serve.kv_dir("vanilla")) in cmd
    assert "--port" in cmd and "8000" in cmd
