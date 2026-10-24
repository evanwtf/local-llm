from __future__ import annotations

import pathlib
import shlex
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import vllm_two_node as v


def _run(rank: int) -> list[str]:
    return v.docker_run(
        rank=rank,
        name="supervl",
        image="vllm/vllm-openai:v0.32.0",
        model="ckpt",
        port=8888,
        vllm_args=["--max-num-seqs", "1"],
    )


def _env(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    return dict(p.split("=", 1) for p in pairs)


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@pytest.mark.fast
def test_head_serves_the_api_and_the_worker_is_headless() -> None:
    head, worker = _run(0), _run(1)
    assert _after(head, "--node-rank") == "0"
    assert _after(head, "--port") == "8888"
    assert "--headless" not in head
    assert _after(worker, "--node-rank") == "1"
    assert "--headless" in worker
    assert "--port" not in worker


@pytest.mark.fast
def test_both_nodes_get_the_same_vllm_arguments() -> None:
    def serve_args(argv: list[str]) -> list[str]:
        tail = argv[argv.index("serve") :]
        return tail[: tail.index("--node-rank")]

    assert serve_args(_run(0)) == serve_args(_run(1))
    assert serve_args(_run(0))[:4] == ["serve", "/models/ckpt", "--max-num-seqs", "1"]


@pytest.mark.fast
def test_nccl_is_pinned_to_all_four_fabric_paths() -> None:
    env = _env(_run(0))
    assert env["NCCL_SOCKET_IFNAME"].count(",") == 3
    assert env["NCCL_IB_HCA"].count(",") == 3
    assert env["NCCL_IB_GID_INDEX"] == "3"


@pytest.mark.fast
def test_each_node_advertises_its_own_fabric_address() -> None:
    assert _env(_run(0))["VLLM_HOST_IP"] == "10.0.0.1"
    assert _env(_run(1))["VLLM_HOST_IP"] == "10.0.0.2"
    assert _after(_run(1), "--master-addr") == "10.0.0.1"


@pytest.mark.fast
def test_shell_line_leaves_the_models_mount_for_the_remote_shell_to_expand() -> None:
    line = v.shell_line(_run(1))
    assert "-v ~/models:/models:ro" in line
    assert shlex.split(line)[0] == "docker"
