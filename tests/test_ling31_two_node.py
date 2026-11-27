from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import ling31_two_node as k


def _run(rank: int) -> list[str]:
    return k.docker_run(rank=rank, name="ling", home="/home/u", user="1000:1000")


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
        argv = k.serve_argv(rank)
        assert "--no-thinking" in argv
        assert "--thinking" not in argv


def test_the_recipe_settings_are_served_verbatim() -> None:
    argv = k.serve_argv(0)
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
    head, worker = k.serve_argv(0), k.serve_argv(1)
    assert _values(head, "--host") == ["0.0.0.0"]
    assert _values(worker, "--host") == ["127.0.0.1"]
    assert _after(head, "--rank") == "0" and _after(worker, "--rank") == "1"
    assert _after(worker, "--master") == "10.0.0.1"
    assert _after(head, "--port") == "8888"
    assert k.READY_LINE in f"serving x at http://0.0.0.0:{k.PORT}/v1 on CUDA, rank 0"


@pytest.mark.parametrize(
    "extra",
    [
        ["--", "--thinking"],
        ["--", "--context", "131072"],
        ["--", "--reasoning-effort", "high"],
        ["--", "--no-drafts"],
        ["--thinking"],
    ],
)
def test_start_takes_no_server_arguments(
    extra: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # #1309 review M3: `start -- --thinking` turned thinking on while the
    # recorded argv still said --no-thinking, and the rows pooled.
    calls: list[str] = []
    monkeypatch.setattr(k, "on_node", lambda rank, cmd: calls.append(cmd))
    with pytest.raises(SystemExit) as got:
        k.main(["start", "--name", "ling", *extra])
    assert got.value.code == 2
    assert calls == []


def test_the_sampler_is_named_in_the_argv() -> None:
    argv = k.serve_argv(0)
    assert _after(argv, "--temperature") == "1.0"
    assert _after(argv, "--top-k") == "20"
    assert _after(argv, "--top-p") == "0.95"


def test_nccl_names_all_four_fabric_paths_at_gid_3() -> None:
    env = _env(_run(1))
    assert len(env["NCCL_SOCKET_IFNAME"].split(",")) == 4
    assert len(env["NCCL_IB_HCA"].split(",")) == 4
    assert env["NCCL_IB_GID_INDEX"] == "3"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TENSORFOLD_NO_UPDATE_CHECK"] == "1"
    assert env["TENSORFOLD_ENGINE_COMMIT"] == k.ENGINE_SHA


def test_the_container_gets_the_rank_folder_and_the_pinned_trees() -> None:
    argv = _run(1)
    assert _after(argv, "--device") == "/dev/infiniband"
    assert "memlock=-1" in argv
    assert f"/home/u/models/{k.MODEL}-tp2-rank1:/model:ro" in argv
    assert f"/home/u/{k.ENGINE_TREE}:/work/engine:ro" in argv
    assert f"/home/u/{k.RECIPE_TREE}:/work/recipe:ro" in argv
    assert _after(argv, "--user") == "1000:1000"
    assert _env(argv)["HOME"] == "/work/build"
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
    argv = k.systemd_unit(rank=1, name="ling", home="/home/u", user="1000:1000")
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
    subprocess.run(["bash", "-n", "-c", shell], check=True)
    argv = k.prepare_run(rank=1, home="/home/u", user="1000:1000")
    assert "--rm" in argv and "/home/u/models:/models" in argv


def _good(home: str = "/home/u", rank: int = 0) -> str:
    return "\n".join(
        [
            f"home={home}",
            "user=1000:1000",
            f"engine_head={k.ENGINE_SHA}",
            "engine_dirty=0",
            f"recipe_head={k.RECIPE_SHA}",
            "recipe_dirty=0",
            "root_owned=0",
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
        (("engine_dirty=1",), "engine_dirty", "~/ling31-tensorfold has edits"),
        (("recipe_head=abc",), "recipe_head", "recipe checkout at abc"),
        (("recipe_dirty=2",), "recipe_dirty", "~/ling31-tf-recipe has edits"),
        (("root_owned=3",), "root_owned", "another user owns"),
        (("user=",), "user", "uid:gid"),
        (("prebuild=",), "prebuild", "no prebuild"),
        (("rank_shards=12",), "rank_shards", "holds 12 rank shards"),
        (("partial=1",), "", ".partial exists"),
        ((f"mem_kib={113 * 2**20}",), "mem_kib", "MemAvailable 113.0 GiB"),
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
    assert 'git -C "$E" status --porcelain' in shell
    assert 'git -C "$R" status --porcelain' in shell
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
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert f"ConnectTimeout={k.SSH_CONNECT_TIMEOUT_S}" in argv


def test_the_unit_line_survives_a_shell_round_trip() -> None:
    argv = k.systemd_unit(rank=0, name="ling", home="/home/u", user="1000:1000")
    assert shlex.split(shlex.join(argv)) == argv


def test_the_container_command_is_what_run_py_records_as_server_argv() -> None:
    # run.serving_tensorfold matches a container whose command has "serve" as
    # its own token and "--name <model>"; the row then carries --no-thinking.
    argv = _run(0)
    cmd = argv[argv.index(k.IMAGE) + 1 :]
    assert "serve" in cmd and "tensorfold" in " ".join(cmd)
    assert cmd[cmd.index("--name") + 1] == k.SERVED_NAME
    assert "--no-thinking" in cmd
    assert cmd[3:] == k.serve_argv(0)


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


REPO = pathlib.Path(__file__).resolve().parent.parent
BACKEND = "ling31f1019exl3tfbertnothinkdual2xrc"


def _backend() -> dict[str, object]:
    import tomllib

    text = (REPO / "benchmarks" / "agent" / "tasks.toml").read_text()
    backend: dict[str, object] = tomllib.loads(text)["backend"][BACKEND]
    return backend


def test_the_backend_matches_what_the_launcher_serves() -> None:
    b = _backend()
    assert b["model"] == k.SERVED_NAME
    assert b["base_url"] == f"http://127.0.0.1:{k.PORT}"
    assert b["context_tokens"] == int(_after(list(k.SERVE_ARGS), "--context"))
    assert b["client_effort"] == "unset"
    assert b["recipe_dir"] == f"~/{k.RECIPE_TREE}"
    assert "nothink" in BACKEND


def test_the_reference_opencode_entry_sends_no_effort() -> None:
    # A client effort turns thinking back on in this engine (#1309).
    sys.path.insert(0, str(REPO / "scripts"))
    import client_container

    path = REPO / "config" / "opencode.json"
    why = client_container.check_opencode_provider(
        BACKEND, _backend(), path.read_text(), path
    )
    assert why is None, why


def _prepare_in(tmp: pathlib.Path, stub: str) -> subprocess.CompletedProcess[str]:
    """Run the real prepare shell with its paths moved under `tmp` and a stub
    prebuild. The rank folder exists, so the split is skipped."""
    for d in ("engine", "build", "models", "recipe"):
        (tmp / d).mkdir(exist_ok=True)
    (tmp / "models" / k.rank_dir(1)).mkdir()
    (tmp / "prebuild.py").write_text(stub)
    shell = k.prepare_shell(1)
    for old, new in (
        (k.PREBUILD_SCRIPT, str(tmp / "prebuild.py")),
        ("/work/engine", str(tmp / "engine")),
        ("/work/build", str(tmp / "build")),
        ("/models", str(tmp / "models")),
    ):
        shell = shell.replace(old, new)
    return subprocess.run(
        ["bash", "-c", shell], capture_output=True, text=True, timeout=60, check=False
    )


READY_STUB = "".join(
    f"print('[prebuild] mod{i}: ready in 1 s')\n" for i in range(k.PREBUILD_READY)
)


def test_a_clean_prebuild_writes_the_mark(tmp_path: pathlib.Path) -> None:
    r = _prepare_in(tmp_path, READY_STUB)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "build" / k.PREBUILD_MARK).read_text().strip() == k.ENGINE_SHA


@pytest.mark.parametrize(
    ("stub", "says"),
    [
        # #1309 review M1: a crash before the module loop exits nonzero, and
        # `| tee` hid it; the mark was written anyway.
        ("raise RuntimeError('No CUDA GPUs are available')\n", "No CUDA GPUs"),
        # Fewer modules than the pinned engine builds.
        ("print('[prebuild] a: ready in 1 s')\n", "1 modules ready"),
        # prebuild.py catches a module's error and prints "build failed".
        (READY_STUB + "print('[prebuild] b: build failed (x)')\n", "failed"),
    ],
)
def test_a_bad_prebuild_writes_no_mark(
    tmp_path: pathlib.Path, stub: str, says: str
) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / k.PREBUILD_MARK).write_text("stale\n")
    r = _prepare_in(tmp_path, stub)
    assert r.returncode != 0
    assert says in r.stdout + r.stderr
    assert not (tmp_path / "build" / k.PREBUILD_MARK).exists()


DONE_OFF = "[tensorfold] done req-1 prompt=36 cached=0 thinking=False tokens=19"
DONE_ON = "[tensorfold] done req-2 prompt=36 cached=0 thinking=True tokens=99"


def _row(reasoning: object, backend: str = k.BACKEND) -> dict[str, object]:
    return {"backend": backend, "reasoning_tokens": reasoning}


def test_a_thinking_off_run_passes_the_check() -> None:
    rows = [_row(0), _row(0), _row(57, backend="other")]
    assert k.thinking_problems(f"x\n{DONE_OFF}\n{DONE_OFF}\n", rows) == []


@pytest.mark.parametrize(
    ("log", "rows", "says"),
    [
        (f"{DONE_OFF}\n{DONE_ON}\n", [_row(0)], "1 of 2 requests ran with thinking"),
        ("loading\nserving\n", [_row(0)], "no finished requests"),
        (f"{DONE_OFF}\n", [_row(0), _row(12)], "1 of 2"),
        (f"{DONE_OFF}\n", [_row(None)], "reasoning_tokens"),
        ("[tensorfold] done req-3 prompt=1 tokens=1\n", [], "no thinking= field"),
    ],
)
def test_a_run_that_thought_or_proves_nothing_fails_the_check(
    log: str, rows: list[dict[str, object]], says: str
) -> None:
    why = k.thinking_problems(log, rows)
    assert any(says in w for w in why), why


def test_check_thinking_exits_nonzero_on_a_thinking_request(
    tmp_path: pathlib.Path,
) -> None:
    import json

    log = tmp_path / "rank0.log"
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(json.dumps(_row(0)) + "\n")
    log.write_text(DONE_OFF + "\n")
    args = ["check-thinking", "--log", str(log), "--results", str(ledger)]
    assert k.main(args) == 0
    log.write_text(DONE_OFF + "\n" + DONE_ON + "\n")
    assert k.main(args) == 1


def test_the_recipe_pin_is_the_launchers_recipe() -> None:
    b = _backend()
    assert k.RECIPE_SHA.startswith(str(b["recipe_pin"]))
    assert "not approved" in str(b["recipe_pin_reason"])
