"""scripts/videogen/serve.py: the exact server command per model, and its refusals (#1406).

Nothing here starts a server: the launcher and the port check are fakes.
"""

from __future__ import annotations

import json
import logging
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import generate, serve

HOME = pathlib.Path("/home/someone")


def block(name: str) -> dict:
    data: dict = generate.load_json(generate.ARMS_FILE)["servers"][name]
    return data


def test_h3_command_is_the_runbook_command_with_a_served_name() -> None:
    argv, env = serve.command(block("h3"), HOME)
    assert argv[:9] == [
        "systemd-run",
        "--user",
        "--scope",
        "-p",
        "MemoryMax=115G",
        "-p",
        "MemorySwapMax=0",
        "--unit=h3-omni",
        "--",
    ]
    assert argv[9:12] == [
        "/home/someone/venvs/vllm-omni-0.32.0/bin/vllm",
        "serve",
        "/home/someone/models/MiniMax-H3/FL2VA",
    ]
    assert argv[-2:] == ["--served-model-name", "MiniMaxAI/MiniMax-H3"]
    assert "--trust-remote-code" in argv and "--port" in argv
    assert argv[argv.index("--port") + 1] == "8040"
    assert env == {
        "CUDA_VISIBLE_DEVICES": "0",
        "FLASHINFER_DISABLE_VERSION_CHECK": "1",
        "VLLM_WORKER_MULTIPROC_METHOD": "spawn",
        "VLLM_OMNI_VIDEO_SYNC_TIMEOUT": "7200",
    }


def test_wan_command_has_no_boundary_ratio() -> None:
    for name in ("wan-t2v", "wan-i2v"):
        argv, _ = serve.command(block(name), HOME)
        assert not any("boundary" in a for a in argv)
        assert argv[argv.index("--port") + 1] == "8041"


def test_the_shell_line_quotes_and_prefixes_the_env() -> None:
    line = serve.shell_line(["a b", "c"], {"K": "v w"})
    assert line == "K='v w' 'a b' c"


def never(*args: object) -> int:
    raise AssertionError("must not launch")


def test_without_start_nothing_launches(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    rc = serve.serve("wan-t2v", start=False, home=HOME, launcher=never)
    assert rc == 0
    assert "VLLM_OMNI_VIDEO_SYNC_TIMEOUT=7200 systemd-run" in caplog.text
    assert "--served-model-name Wan-AI/Wan2.2-T2V-A14B-Diffusers" in caplog.text


def test_start_launches_the_same_command(tmp_path: pathlib.Path) -> None:
    calls: list[tuple[list[str], dict[str, str], pathlib.Path]] = []

    def launcher(argv: list[str], env: dict[str, str], log: pathlib.Path) -> int:
        calls.append((argv, env, log))
        return 4242

    log = tmp_path / "wan.log"
    rc = serve.serve(
        "wan-i2v",
        start=True,
        log=log,
        home=HOME,
        is_open=lambda port: False,
        launcher=launcher,
    )
    assert rc == 0
    assert calls == [(*serve.command(block("wan-i2v"), HOME), log)]


def test_start_refuses_when_the_port_answers() -> None:
    rc = serve.serve(
        "h3", start=True, home=HOME, is_open=lambda port: port == 8040, launcher=never
    )
    assert rc == 2


def test_a_boundary_ratio_in_the_argv_is_refused(tmp_path: pathlib.Path) -> None:
    data = generate.load_json(generate.ARMS_FILE)
    data["servers"]["wan-t2v"]["argv"] += ["--boundary-ratio", "0.875"]
    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps(data))
    rc = serve.serve(
        "wan-t2v",
        start=True,
        arms_file=arms,
        home=HOME,
        is_open=lambda p: False,
        launcher=never,
    )
    assert rc == 2


def test_an_unknown_model_is_refused() -> None:
    assert serve.serve("wan", start=False, launcher=never) == 2


def test_the_remote_argv_detaches_ssh_and_quotes_the_command() -> None:
    # ssh -f: a plain ssh waits on the scope and never returns (#1430).
    argv, env = serve.command(
        serve.generate.all_servers(serve.generate.ARMS_FILES)["wan-t2v"][0], HOME
    )
    log = pathlib.Path("/h/bench-logs/videogen/logs/wan-t2v-omni.log")
    got = serve.remote_argv("worker-host", argv, env, log)
    assert got[:4] == ["ssh", "-f", "-n", "worker-host"]
    assert len(got) == 5
    remote = got[4]
    assert remote.startswith("cd ~ && nohup env ")
    assert remote.endswith(f" > {log} 2>&1 < /dev/null &")
    assert serve.shell_line(argv, env) in remote


def test_start_with_a_host_launches_remotely_and_checks_its_port(
    tmp_path: pathlib.Path,
) -> None:
    seen: list[tuple[str, list[str]]] = []
    asked: list[tuple[int, str]] = []

    def remote(
        host: str, argv: list[str], env: dict[str, str], log: pathlib.Path
    ) -> int:
        seen.append((host, argv))
        return 0

    def is_open(port: int, host: str = "127.0.0.1") -> bool:
        asked.append((port, host))
        return False

    rc = serve.serve(
        "wan-t2v",
        start=True,
        home=HOME,
        log=tmp_path / "x.log",
        is_open=is_open,
        launcher=never,
        host="worker-host",
        remote_launcher=remote,
    )
    assert rc == 0
    assert [h for h, _ in seen] == ["worker-host"]
    assert seen[0][1][0] == "systemd-run"
    assert asked and all(h == "worker-host" for _, h in asked)


def test_a_failed_remote_start_is_an_error(tmp_path: pathlib.Path) -> None:
    rc = serve.serve(
        "wan-t2v",
        start=True,
        home=HOME,
        log=tmp_path / "x.log",
        is_open=lambda port, host="": False,
        launcher=never,
        host="worker-host",
        remote_launcher=lambda host, argv, env, log: 255,
    )
    assert rc == 2


def test_host_without_start_is_refused() -> None:
    with pytest.raises(SystemExit):
        serve.main(["--model", "wan-t2v", "--host", "worker-host"])


def test_the_remote_launch_inherits_no_stream(tmp_path: pathlib.Path) -> None:
    # An inherited stdout kept a caller's pipe open for as long as the remote
    # server ran: `serve.py --host ... | tail` never ended (#1430).
    calls: list[dict[str, object]] = []

    def run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(kw)
        return subprocess.CompletedProcess(argv, 0)

    rc = serve.launch_remote("worker-host", ["true"], {}, tmp_path / "x.log", run=run)
    assert rc == 0
    kw = calls[0]
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["stdout"] == subprocess.DEVNULL
    assert kw["stderr"] not in (None, subprocess.PIPE)
