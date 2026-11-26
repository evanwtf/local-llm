from __future__ import annotations

import pathlib
import shlex
import subprocess
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


def test_extra_env_reaches_both_nodes() -> None:
    for rank in (0, 1):
        argv = v.docker_run(
            rank=rank,
            name="glm",
            image="vllm/vllm-openai:v0.34.0",
            model="ckpt",
            port=8888,
            vllm_args=[],
            extra_env={"VLLM_USE_DEEP_GEMM": "0"},
        )
        assert _env(argv)["VLLM_USE_DEEP_GEMM"] == "0"
        # The variable goes to docker, before the image name.
        assert argv.index("VLLM_USE_DEEP_GEMM=0") < argv.index("--entrypoint")


@pytest.mark.parametrize(
    "key",
    [
        "NCCL_IB_HCA",
        "NCCL_NET",
        "NCCL_IB_TC",
        "UCX_NET_DEVICES",
        "GLOO_DEVICE_TRANSPORT",
        "MASTER_ADDR",
        "TP_SOCKET_IFNAME",
        "NVSHMEM_x",
        "VLLM_HOST_IP",
        "HF_HUB_OFFLINE",
    ],
)
def test_extra_env_cannot_change_the_fabric_setup(key: str) -> None:
    with pytest.raises(ValueError, match="fabric setup"):
        v.docker_run(
            rank=0,
            name="glm",
            image="i",
            model="ckpt",
            port=8888,
            vllm_args=[],
            extra_env={key: "x"},
        )


@pytest.mark.parametrize("key", ["1ABC", "A-B", "A B", "A=B", ""])
def test_extra_env_refuses_a_bad_key_name(key: str) -> None:
    with pytest.raises(ValueError, match="not a valid variable name"):
        v.docker_run(
            rank=1,
            name="glm",
            image="i",
            model="ckpt",
            port=8888,
            vllm_args=[],
            extra_env={key: "x"},
        )


@pytest.mark.parametrize("bad", ["NOEQUALS", "=value"])
def test_parse_env_refuses_a_malformed_pair(bad: str) -> None:
    with pytest.raises(ValueError, match="KEY=VALUE"):
        v.parse_env([bad])


def test_parse_env_keeps_an_equals_sign_in_the_value() -> None:
    assert v.parse_env(["A=b=c", "EMPTY="]) == {"A": "b=c", "EMPTY": ""}


def test_start_gives_both_ranks_the_same_extra_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(v.subprocess, "run", fake_run)
    argv = ["start", "--model-dir", "~/models/ckpt", "--image", "i", "--name", "n"]
    argv += ["--env", "VLLM_USE_DEEP_GEMM=0", "--", "--max-num-seqs", "4"]
    assert v.main(argv) == 0

    ssh, head = calls
    assert ssh[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert head[:2] == ["bash", "-c"]
    worker_argv, head_argv = shlex.split(ssh[-1]), shlex.split(head[-1])
    for node in (worker_argv, head_argv):
        assert _env(node)["VLLM_USE_DEEP_GEMM"] == "0"
        assert node[node.index("serve") + 2 : node.index("serve") + 4] == [
            "--max-num-seqs",
            "4",
        ]
    assert _after(worker_argv, "--node-rank") == "1"
    assert _after(head_argv, "--node-rank") == "0"
