from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import inkling2_two_node as k

#: The default line of the template at the pinned engine revision.
PINNED_LINE = (
    "{%- set eff = reasoning_effort if reasoning_effort is defined and "
    "reasoning_effort is not none else 0.9 -%}"
)


def _run(rank: int, extra: list[str] | None = None) -> list[str]:
    return k.docker_run(rank=rank, name="i2", home="/home/u", serve_args=extra or [])


def _env(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    return dict(p.split("=", 1) for p in pairs)


def _torchrun(argv: list[str]) -> list[str]:
    shell = argv[-1]
    return shlex.split(shell.split("exec ", 1)[1])


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_the_template_default_becomes_low() -> None:
    got = k.low_effort_template(f"a\n{PINNED_LINE}\nb")
    assert 'reasoning_effort is not none else "low" -%}' in got
    assert "else 0.9" not in got


def test_a_template_without_the_pinned_default_is_refused() -> None:
    with pytest.raises(SystemExit, match="exactly once"):
        k.low_effort_template("{%- set eff = 0.5 -%}")
    with pytest.raises(SystemExit, match="exactly once"):
        k.low_effort_template(PINNED_LINE + PINNED_LINE)


def test_both_ranks_serve_the_low_template_and_the_same_arguments() -> None:
    head, worker = _torchrun(_run(0)), _torchrun(_run(1))
    assert _after(head, "--chat-template") == f"/work/{k.LOW_TEMPLATE}"
    strip = {"--node_rank"}
    assert [a for i, a in enumerate(head) if head[i - 1] not in strip] == [
        a for i, a in enumerate(worker) if worker[i - 1] not in strip
    ]
    assert _after(head, "--node_rank") == "0"
    assert _after(worker, "--node_rank") == "1"
    assert _after(worker, "--master_addr") == "10.0.0.1"


def test_extra_arguments_come_after_the_defaults_so_they_win() -> None:
    args = _torchrun(_run(0, ["--max-model-len", "262144"]))
    lens = [args[i + 1] for i, a in enumerate(args) if a == "--max-model-len"]
    assert lens == ["524288", "262144"]


def test_nccl_names_all_four_fabric_paths() -> None:
    env = _env(_run(1))
    assert set(env["NCCL_SOCKET_IFNAME"].split(",")) == {
        "enp1s0f1np1",
        "enP2p1s0f1np1",
        "enp1s0f0np0",
        "enP2p1s0f0np0",
    }
    assert set(env["NCCL_IB_HCA"].split(",")) == {
        "rocep1s0f1",
        "roceP2p1s0f1",
        "rocep1s0f0",
        "roceP2p1s0f0",
    }
    assert env["GLOO_SOCKET_IFNAME"] == env["NCCL_SOCKET_IFNAME"]
    assert env["HF_HUB_OFFLINE"] == "1"


def test_the_container_gets_the_rdma_device_and_limits() -> None:
    argv = _run(0)
    assert _after(argv, "--device") == "/dev/infiniband"
    assert "memlock=-1" in argv and "stack=67108864" in argv
    assert f"/home/u/models/{k.MODEL}:/model:ro" in argv
    assert f"/home/u/{k.ENGINE_TREE}:/work/engine:ro" in argv


def test_each_rank_is_a_killmode_process_unit_that_appends_its_log() -> None:
    argv = k.systemd_unit(rank=1, name="i2", home="/home/u", serve_args=[])
    assert argv[:3] == ["systemd-run", "--user", "--unit=i2-rank1"]
    assert "--property=KillMode=process" in argv
    assert "--property=StandardOutput=append:/home/u/bench-logs/i2-rank1.log" in argv
    # The unit's main process is the foreground docker client, not a shell.
    rest = argv[
        argv.index("--property=StandardError=append:/home/u/bench-logs/i2-rank1.log")
        + 1 :
    ]
    assert rest[:2] == ["docker", "run"]
    assert "-d" not in rest


def _good(home: str = "/home/u") -> str:
    """A probe answer from a node that may start."""
    return "\n".join(
        [
            f"home={home}",
            f"engine_head={k.ENGINE_SHA}",
            "engine_dirty=0",
            "extension=45a574874ee6",
            f"sums={k.SUMS_ENTRIES}",
            "incomplete=0",
            f"mem_kib={120 * 2**20}",
            "unit_active=inactive",
            "gpu_container=dcgm-exporter",
            "gpu_container=dcgm",
        ]
    )


def _why(*extra: str, drop: str = "") -> list[str]:
    lines = [ln for ln in _good().splitlines() if not ln.startswith(drop or "\0")]
    return k.refusals(k.parse_facts("\n".join([*lines, *extra])), "i2", 0)


def test_a_ready_node_has_no_refusal_and_the_dcgm_monitors_are_allowed() -> None:
    assert _why() == []


@pytest.mark.parametrize(
    ("extra", "drop", "says"),
    [
        (("engine_head=1234567",), "engine_head", "not " + k.ENGINE_SHA),
        (("engine_dirty=1",), "engine_dirty", "uncommitted"),
        (("extension=",), "extension", "no built exllamav3 extension"),
        (("sums=none",), "sums", "no SHA256SUMS"),
        (("sums=131",), "sums", "lists 131 files"),
        (("missing=mtp.safetensors",), "", "1 files in SHA256SUMS are absent"),
        (("incomplete=2",), "incomplete", "2 *.incomplete"),
        ((f"mem_kib={100 * 2**20}",), "mem_kib", "MemAvailable 100.0 GiB"),
        (("listen=0.0.0.0:18100",), "", "0.0.0.0:18100 is already listening"),
        (("listen=10.0.0.1:29620",), "", "10.0.0.1:29620 is already listening"),
        (("unit_active=active",), "unit_active", "already active"),
        (("gpu_container=i2spark-r0",), "", "i2spark-r0"),
        (("gpu_container=vllm-rank0",), "", "hold the GPU"),
        (("gpu_app=4242, python3",), "", "a GPU process is running"),
    ],
)
def test_each_unready_node_is_refused(
    extra: tuple[str, ...], drop: str, says: str
) -> None:
    why = _why(*extra, drop=drop)
    assert len(why) == 1, why
    assert says in why[0]


def test_an_empty_probe_refuses_on_every_count() -> None:
    assert len(k.refusals({}, "i2", 1)) >= 5


def test_the_probe_names_the_pinned_paths_and_ports() -> None:
    shell = k.facts_shell("i2", 1)
    assert "@" not in shell
    assert k.MODEL in shell and k.BUILD_DIR in shell and k.EXTENSION_GLOB in shell
    assert ":(18100|29620)$" in shell
    assert "reset-failed i2-rank1" in shell
    assert 'git -C "$E" diff --quiet HEAD' in shell


def test_nccl_debug_info_logs_init_and_net() -> None:
    assert "NCCL_DEBUG_SUBSYS" not in k.container_env()
    env = _env(
        k.docker_run(rank=0, name="i2", home="/h", serve_args=[], nccl_debug="INFO")
    )
    assert env["NCCL_DEBUG"] == "INFO"
    assert env["NCCL_DEBUG_SUBSYS"] == "INIT,NET"


def test_the_worker_ssh_gives_up_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def run(argv: list[str], **kw: object) -> object:
        seen["argv"], seen["timeout"] = argv, kw.get("timeout")
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(k.subprocess, "run", run)
    r = k.on_node(1, "true")
    assert r.returncode == 124 and "no answer" in r.stderr
    assert f"ConnectTimeout={k.SSH_CONNECT_TIMEOUT_S}" in seen["argv"]  # type: ignore[operator]
    assert seen["timeout"] == k.NODE_TIMEOUT_S


class _Done:
    def __init__(self, out: str = "", code: int = 0) -> None:
        self.returncode, self.stdout, self.stderr = code, out, ""


def _cluster(tmp_path: pathlib.Path) -> None:
    template = tmp_path / k.ENGINE_TREE / k.TEMPLATE
    template.parent.mkdir(parents=True)
    template.write_text(PINNED_LINE)
    (tmp_path / k.BUILD_DIR).mkdir(parents=True)


def test_start_runs_the_worker_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    order: list[tuple[int, str]] = []

    def fake(rank: int, command: str) -> _Done:
        order.append((rank, command.split()[0]))
        return _Done(_good(str(tmp_path)))

    _cluster(tmp_path)
    monkeypatch.setattr(k, "on_node", fake)
    assert k.main(["start", "--name", "i2"]) == 0
    started = [rank for rank, cmd in order if cmd == "systemd-run"]
    assert started == [1, 0]
    assert 'else "low"' in (tmp_path / k.BUILD_DIR / k.LOW_TEMPLATE).read_text()


def test_a_refused_node_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []

    def fake(rank: int, command: str) -> _Done:
        calls.append(command.split()[0])
        out = _good(str(tmp_path))
        return _Done(out + "\nlisten=0.0.0.0:18100" if rank == 0 else out)

    _cluster(tmp_path)
    monkeypatch.setattr(k, "on_node", fake)
    assert k.main(["start", "--name", "i2"]) == 1
    assert "systemd-run" not in calls


def test_rank0_failing_to_start_stops_the_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[tuple[int, str]] = []

    def fake(rank: int, command: str) -> _Done:
        calls.append((rank, command))
        if rank == 0 and command.startswith("systemd-run"):
            return _Done(code=1)
        return _Done(_good(str(tmp_path)))

    _cluster(tmp_path)
    monkeypatch.setattr(k, "on_node", fake)
    assert k.main(["start", "--name", "i2"]) == 1
    rank, last = calls[-1]
    assert rank == 1 and "systemctl --user stop i2-rank1" in last
