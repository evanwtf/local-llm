from __future__ import annotations

import pathlib
import shlex
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


def test_the_preflight_checks_the_pinned_engine() -> None:
    line = k.preflight_shell("i2", 0)
    assert k.ENGINE_SHA in line
    assert k.EXTENSION_GLOB in line
    assert line.endswith('printf %s "$HOME"')


def test_start_runs_the_worker_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    order: list[tuple[int, str]] = []

    class Done:
        def __init__(self, out: str) -> None:
            self.returncode, self.stdout, self.stderr = 0, out, ""

    def fake(rank: int, command: str) -> Done:
        order.append((rank, command.split()[0]))
        return Done(str(tmp_path))

    template = tmp_path / k.ENGINE_TREE / k.TEMPLATE
    template.parent.mkdir(parents=True)
    template.write_text(PINNED_LINE)
    (tmp_path / k.BUILD_DIR).mkdir(parents=True)
    monkeypatch.setattr(k, "on_node", fake)
    assert k.main(["start", "--name", "i2"]) == 0
    started = [rank for rank, cmd in order if cmd == "systemd-run"]
    assert started == [1, 0]
    assert 'else "low"' in (tmp_path / k.BUILD_DIR / k.LOW_TEMPLATE).read_text()
