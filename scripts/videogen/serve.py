"""Print, or start, the exact vLLM-Omni server command for a video-gen model (#1406).

    serve.py --model h3                  # log the command, run nothing
    serve.py --model wan-t2v --start     # launch it in a systemd scope

The command comes from the model's `servers` block in
`benchmarks/videogen/arms-v1.json` or `fast-arms-v1.json` (video, #1412), `image-arms-v1.json` or
`edit-arms-v1.json` (images, #1424): the env, the memory scope and the argv,
with `--served-model-name` so `generate.py` can name the model in each
request and refuse a server that serves another. A clip is reproducible only
if its server starts the same way every time, and run.json records this
block, so the command lives in the file and nowhere else.

It refuses an argv that sets `--boundary-ratio`: a value set at startup
overrides the `boundary_ratio` of every request, so the two Wan arms would
run at one value. It also refuses an argv that fuses a distill LoRA
(`--lora-backend distill`) and quantizes the weights: 0.32.0 fuses after it
quantizes, so the LoRA fails to load or is dropped (#1412). Run it on the node the block names (`node`), or pass
`--host` to start it there over ssh (#1430). `--start` refuses when the port
already answers on the target machine.

`--host` runs `ssh -f -n HOST "cd ~ && nohup env ... > LOG 2>&1 < /dev/null &"`.
The `-f` matters: a plain ssh waits on the systemd scope and never returns
(it hung twice on 2026-11-18, #1424). Paths are this machine's: both nodes
must have the same home and checkout layout.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import shlex
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen import generate
from videogen.common import DEFAULT_ROOT

import logs

logger = logging.getLogger(__name__)

#: (argv, env, log path) -> pid. Tests replace it; nothing starts there.
Launch = Callable[[list[str], dict[str, str], pathlib.Path], int]


def expand(item: str, home: pathlib.Path) -> str:
    """`~/x` becomes `<home>/x`; anything else is unchanged."""
    if item == "~" or item.startswith("~/"):
        return str(home) + item[1:]
    return item


def command(
    block: Mapping[str, Any], home: pathlib.Path
) -> tuple[list[str], dict[str, str]]:
    """(systemd-run argv, the env to add) for a server block."""
    scope = block["scope"]
    argv = [
        *("systemd-run", "--user", "--scope"),
        *("-p", f"MemoryMax={scope['MemoryMax']}"),
        *("-p", f"MemorySwapMax={scope['MemorySwapMax']}"),
        f"--unit={scope['unit']}",
        "--",
        *(expand(item, home) for item in block["argv"]),
    ]
    return argv, {str(k): str(v) for k, v in block["env"].items()}


def shell_line(argv: list[str], env: Mapping[str, str]) -> str:
    """The command as one pasteable shell line."""
    prefix = [f"{k}={shlex.quote(v)}" for k, v in env.items()]
    return " ".join([*prefix, *(shlex.quote(a) for a in argv)])


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def launch(argv: list[str], env: dict[str, str], log: pathlib.Path) -> int:
    """Start the command detached, output to `log`; return its pid."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        proc = subprocess.Popen(
            argv,
            env={**os.environ, **env},
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid


def remote_argv(
    host: str, argv: list[str], env: Mapping[str, str], log: pathlib.Path
) -> list[str]:
    """The ssh argv that starts `argv` on `host`, detached, output to `log`."""
    inner = " ".join(["env", shell_line(argv, env)])
    remote = f"cd ~ && nohup {inner} > {shlex.quote(str(log))} 2>&1 < /dev/null &"
    return ["ssh", "-f", "-n", host, remote]


def launch_remote(
    host: str,
    argv: list[str],
    env: dict[str, str],
    log: pathlib.Path,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> int:
    """Start the command on `host` and return once ssh has gone to the background.

    Returns the exit status of the foreground ssh (0 when the remote shell
    took the command), not a pid: the server's pid lives on the other node.

    No ssh stream is inherited. The backgrounded ssh lives as long as the
    remote scope, so an inherited stdout keeps a caller's pipe open and
    `serve.py ... | tail` never ends (seen on 2026-11-18, #1430). stderr goes
    to a temporary file and is logged on failure.
    """
    with tempfile.TemporaryFile() as err:
        done = run(
            remote_argv(host, argv, env, log),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            check=False,
            timeout=60,
        )
        if done.returncode != 0:
            err.seek(0)
            text = err.read().decode(errors="replace").strip()
            logger.error("ssh to %s: %s", host, text or "(no message)")
    return done.returncode


RemoteLaunch = Callable[[str, list[str], dict[str, str], pathlib.Path], int]


def serve(
    name: str,
    *,
    start: bool,
    log: pathlib.Path | None = None,
    arms_file: pathlib.Path | None = None,
    home: pathlib.Path | None = None,
    is_open: Callable[..., bool] = port_open,
    launcher: Launch = launch,
    host: str | None = None,
    remote_launcher: RemoteLaunch = launch_remote,
) -> int:
    try:
        # One file if asked; else every arms file, which also refuses two
        # different blocks under one name.
        servers = generate.all_servers(
            [arms_file] if arms_file is not None else generate.ARMS_FILES
        )
    except ValueError as err:
        logger.error("%s", err)
        return 2
    if name not in servers:
        logger.error("no server %r; the servers are %s", name, sorted(servers))
        return 2
    block, media = servers[name]
    try:
        generate.check_server(name, block, media)
    except ValueError as err:
        logger.error("%s", err)
        return 2
    argv, env = command(block, home or pathlib.Path.home())
    logger.info("%s", shell_line(argv, env))
    if not start:
        return 0
    port = int(block["port"])
    if is_open(port) if host is None else is_open(port, host):
        logger.error(
            "port %s already answers on %s; stop that server first",
            port,
            host or "this machine",
        )
        return 2
    path = log or DEFAULT_ROOT / "logs" / f"{name}-omni.log"
    if host is not None:
        rc = remote_launcher(host, argv, env, path)
        if rc != 0:
            logger.error("ssh to %s exited %d; the server did not start", host, rc)
            return 2
        logger.info(
            "started %s on %s (log %s there); it is ready when GET %s:%s/v1/models "
            "lists %s",
            name,
            host,
            path,
            host,
            port,
            block["served_model_name"],
        )
        return 0
    pid = launcher(argv, env, path)
    logger.info(
        "started %s (pid %d, log %s); it is ready when GET :%s/v1/models lists %s",
        name,
        pid,
        path,
        block["port"],
        block["served_model_name"],
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--model",
        required=True,
        help="a server block: h3, wan-t2v, wan-i2v, fastwan, wan-t2v-4step, or an image server "
        "(qwen-image, hidream-o1, z-image-turbo, flux2-klein-4b, qwen-image-edit)",
    )
    p.add_argument("--start", action="store_true", help="launch it, not only log it")
    p.add_argument("--log", type=pathlib.Path, help="server log (with --start)")
    p.add_argument(
        "--host",
        help="start it on this host over ssh -f (with --start); default: here",
    )
    args = p.parse_args(argv)
    if args.host and not args.start:
        p.error("--host needs --start")
    return serve(args.model, start=args.start, log=args.log, host=args.host)


if __name__ == "__main__":
    sys.exit(main())
