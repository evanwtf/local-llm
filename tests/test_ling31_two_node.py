from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import ling31_two_node as k


def _run(rank: int, extra: list[str] | None = None) -> list[str]:
    return k.docker_run(rank=rank, name="ling", home="/home/u", serve_args=extra or [])


def _env(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    return dict(p.split("=", 1) for p in pairs)


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def test_thinking_is_off_on_both_ranks() -> None:
    # The operator's arm (2026-11-27): thinking off, the author's clean setting.
    for rank in (0, 1):
        argv = k.serve_argv(rank, [])
        assert "--no-thinking" in argv
        assert "--thinking" not in argv


def test_the_recipe_settings_are_served_verbatim() -> None:
    argv = k.serve_argv(0, [])
    assert _after(argv, "--context") == "262144"
    assert _after(argv, "--max-tokens") == "4096"
    assert _after(argv, "--answer-reserve") == "0.25"
    assert "--parallel" not in argv  # the recipe: one request at a time
    env = _env(_run(0))
    for key, value in k.RECIPE_ENV.items():
        assert env[key] == value
    assert env["TF_LING_LATENT_MB"] == "270"
    assert env["TORCH_ALLOW_TF32_CUBLAS_OVERRIDE"] == "0"


def test_only_rank0_listens_on_the_lan_and_both_name_the_head() -> None:
    head, worker = k.serve_argv(0, []), k.serve_argv(1, [])
    assert _values(head, "--host") == ["0.0.0.0"]
    assert _values(worker, "--host") == ["127.0.0.1"]
    assert _after(head, "--rank") == "0" and _after(worker, "--rank") == "1"
    assert _after(worker, "--master") == "10.0.0.1"
    assert _after(head, "--port") == "8888"
    assert k.READY_LINE in f"serving x at http://0.0.0.0:{k.PORT}/v1 on CUDA, rank 0"


def test_extra_arguments_come_after_the_defaults_so_they_win() -> None:
    argv = k.serve_argv(0, ["--context", "131072"])
    assert _values(argv, "--context") == ["262144", "131072"]


def test_nccl_names_all_four_fabric_paths_at_gid_3() -> None:
    env = _env(_run(1))
    assert len(env["NCCL_SOCKET_IFNAME"].split(",")) == 4
    assert len(env["NCCL_IB_HCA"].split(",")) == 4
    assert env["NCCL_IB_GID_INDEX"] == "3"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TENSORFOLD_NO_UPDATE_CHECK"] == "1"


def test_the_container_gets_the_rank_folder_and_the_pinned_trees() -> None:
    argv = _run(1)
    assert _after(argv, "--device") == "/dev/infiniband"
    assert "memlock=-1" in argv
    assert f"/home/u/models/{k.MODEL}-tp2-rank1:/model:ro" in argv
    assert f"/home/u/{k.ENGINE_TREE}:/work/engine:ro" in argv
    assert f"/home/u/{k.RECIPE_TREE}:/work/recipe:ro" in argv
    at = argv.index(k.IMAGE)
    assert argv[at + 1 : at + 4] == ["-c", k.rank_shell(), "ling31"]


def test_the_container_runs_the_recipes_guards_beside_the_server() -> None:
    shell = k.rank_shell()
    assert "evict_page_cache.sh" in shell
    assert f"watchdog.sh $pid {k.STOP_FILE} /dev/stderr 1.5" in shell
    assert "trap " in shell and "kill -TERM $pid" in shell
    assert '"$@" & pid=$!' in shell
    # bash parses it
    subprocess.run(["bash", "-n", "-c", shell], check=True)


def test_each_rank_is_a_killmode_process_unit_that_appends_its_log() -> None:
    argv = k.systemd_unit(rank=1, name="ling", home="/home/u", serve_args=[])
    assert argv[:3] == ["systemd-run", "--user", "--unit=ling-rank1"]
    assert "--property=KillMode=process" in argv
    log = "/home/u/bench-logs/ling-rank1.log"
    assert f"--property=StandardOutput=append:{log}" in argv
    rest = argv[argv.index(f"--property=StandardError=append:{log}") + 1 :]
    assert rest[:2] == ["docker", "run"] and "-d" not in rest


def test_prepare_splits_into_a_partial_folder_and_renames_on_success() -> None:
    shell = k.prepare_shell(1)
    out = f"/models/{k.MODEL}-tp2-rank1"
    assert f"--rank 1 {out}.partial" in shell
    assert f"mv {out}.partial {out}" in shell
    assert shell.index("ling_split_check.py") < shell.index("cuda.split")
    assert shell.index("prebuild.py") < shell.index(k.PREBUILD_MARK)
    subprocess.run(["bash", "-n", "-c", shell], check=True)
    argv = k.prepare_run(rank=1, home="/home/u")
    assert "--rm" in argv and "/home/u/models:/models" in argv


def _good(home: str = "/home/u", rank: int = 0) -> str:
    return "\n".join(
        [
            f"home={home}",
            f"engine_head={k.ENGINE_SHA}",
            "engine_dirty=0",
            f"recipe_head={k.RECIPE_SHA}",
            f"prebuild={k.ENGINE_SHA}",
            f"rank_shards={k.MODEL_SHARDS}",
            f"mem_kib={115 * 2**20}",
            "unit_active=inactive",
            "gpu_container=dcgm-exporter",
            "gpu_container=dcgm",
        ]
    )


def _why(*extra: str, drop: str = "") -> list[str]:
    lines = [ln for ln in _good().splitlines() if not ln.startswith(drop or "\0")]
    return k.refusals(k.parse_facts("\n".join([*lines, *extra])), "ling", 0)


def test_a_ready_node_has_no_refusal() -> None:
    assert _why() == []


@pytest.mark.parametrize(
    ("extra", "drop", "says"),
    [
        (("engine_head=1234567",), "engine_head", "not " + k.ENGINE_SHA),
        (("engine_dirty=1",), "engine_dirty", "uncommitted"),
        (("recipe_head=abc",), "recipe_head", "recipe checkout at abc"),
        (("prebuild=",), "prebuild", "no prebuild"),
        (("rank_shards=12",), "rank_shards", "holds 12 rank shards"),
        (("partial=1",), "", ".partial exists"),
        ((f"mem_kib={100 * 2**20}",), "mem_kib", "MemAvailable 100.0 GiB"),
        (("listen=0.0.0.0:8888",), "", "0.0.0.0:8888 is already listening"),
        (("listen=10.0.0.1:29577",), "", "29577 is already listening"),
        (("unit_active=active",), "unit_active", "already active"),
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
    assert len(k.refusals({}, "ling", 1)) >= 6


def test_the_probe_names_the_pinned_paths_and_ports() -> None:
    shell = k.facts_shell("ling", 1)
    assert "@" not in shell
    assert f"{k.MODEL}-tp2-rank1" in shell and "*.rank1.safetensors" in shell
    assert ":(8888|29577)$" in shell
    assert "reset-failed ling-rank1" in shell
    subprocess.run(["bash", "-n", "-c", shell], check=True)


class _Done:
    def __init__(self, out: str = "", code: int = 0) -> None:
        self.returncode, self.stdout, self.stderr = code, out, ""


def test_start_drops_caches_then_runs_rank0_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[tuple[int, str]] = []

    def fake(rank: int, command: str) -> _Done:
        order.append((rank, command))
        return _Done(_good())

    monkeypatch.setattr(k, "on_node", fake)
    assert k.main(["start", "--name", "ling"]) == 0
    started = [r for r, c in order if c.startswith("systemd-run")]
    assert started == [0, 1]
    drops = [r for r, c in order if "drop_caches" in c]
    assert drops == [0, 1]
    first_start = next(i for i, (_, c) in enumerate(order) if c.startswith("systemd"))
    last_drop = max(i for i, (_, c) in enumerate(order) if "drop_caches" in c)
    assert last_drop < first_start


def test_a_refused_node_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(rank: int, command: str) -> _Done:
        calls.append(command)
        out = _good()
        return _Done(out + "\nlisten=0.0.0.0:8888" if rank == 0 else out)

    monkeypatch.setattr(k, "on_node", fake)
    assert k.main(["start", "--name", "ling"]) == 1
    assert not any(c.startswith("systemd-run") or "drop_caches" in c for c in calls)


def test_rank1_failing_to_start_stops_rank0(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str]] = []

    def fake(rank: int, command: str) -> _Done:
        calls.append((rank, command))
        if rank == 1 and command.startswith("systemd-run"):
            return _Done(code=1)
        return _Done(_good())

    monkeypatch.setattr(k, "on_node", fake)
    assert k.main(["start", "--name", "ling"]) == 1
    rank, last = calls[-1]
    assert rank == 0 and "systemctl --user stop ling-rank0" in last


def test_the_worker_ssh_gives_up_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def run(argv: list[str], **kw: object) -> object:
        seen["argv"], seen["timeout"] = argv, kw.get("timeout")
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(k.subprocess, "run", run)
    r = k.on_node(1, "true")
    assert r.returncode == 124 and "no answer" in r.stderr
    assert f"ConnectTimeout={k.SSH_CONNECT_TIMEOUT_S}" in seen["argv"]  # type: ignore[operator]


def test_the_unit_line_survives_a_shell_round_trip() -> None:
    argv = k.systemd_unit(rank=0, name="ling", home="/home/u", serve_args=[])
    assert shlex.split(shlex.join(argv)) == argv


def test_the_container_command_is_what_run_py_records_as_server_argv() -> None:
    # run.serving_tensorfold matches a container whose command has "serve" as
    # its own token and "--name <model>"; the row then carries --no-thinking.
    argv = _run(0)
    cmd = argv[argv.index(k.IMAGE) + 1 :]
    assert "serve" in cmd and "tensorfold" in " ".join(cmd)
    assert cmd[cmd.index("--name") + 1] == k.SERVED_NAME
    assert "--no-thinking" in cmd
    assert cmd[3:] == k.serve_argv(0, [])


def test_the_script_runs_its_arguments_as_the_server(tmp_path: pathlib.Path) -> None:
    shell = k.rank_shell().replace("cd /work/engine", f"cd {tmp_path}")
    shell = shell.replace("/work/recipe/serve/", f"{tmp_path}/")
    shell = shell.replace(k.STOP_FILE, str(tmp_path / "stop"))
    for script in ("evict_page_cache.sh", "watchdog.sh"):
        (tmp_path / script).write_text("exit 0\n")
    r = subprocess.run(
        ["bash", "-c", shell, "ling31", "sh", "-c", "echo served; exit 7"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert r.returncode == 7 and "served" in r.stdout
    assert (tmp_path / "stop").exists()
