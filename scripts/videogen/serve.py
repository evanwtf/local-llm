"""Print, or start, the exact vLLM-Omni server command for a video-gen model (#1406).

    serve.py --model h3                  # log the command, run nothing
    serve.py --model wan-t2v --start     # launch it in a systemd scope

The command comes from the model's `servers` block in
`benchmarks/videogen/arms-v1.json`: the env, the memory scope and the argv,
with `--served-model-name` so `generate.py` can name the model in each
request and refuse a server that serves another. A clip is reproducible only
if its server starts the same way every time, and run.json records this
block, so the command lives in the file and nowhere else.

It refuses an argv that sets `--boundary-ratio`: a value set at startup
overrides the `boundary_ratio` of every request, so the two Wan arms would
run at one value. Run it on the node the block names (`node`). `--start`
refuses when the port already answers on this machine.
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


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
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


def serve(
    name: str,
    *,
    start: bool,
    log: pathlib.Path | None = None,
    arms_file: pathlib.Path = generate.ARMS_FILE,
    home: pathlib.Path | None = None,
    is_open: Callable[[int], bool] = port_open,
    launcher: Launch = launch,
) -> int:
    arms = generate.load_json(arms_file)
    servers = arms["servers"]
    if name not in servers:
        logger.error("no server %r; the servers are %s", name, sorted(servers))
        return 2
    block = servers[name]
    try:
        generate.check_server(name, block)
    except ValueError as err:
        logger.error("%s", err)
        return 2
    argv, env = command(block, home or pathlib.Path.home())
    logger.info("%s", shell_line(argv, env))
    if not start:
        return 0
    if is_open(int(block["port"])):
        logger.error(
            "port %s already answers here; stop that server first", block["port"]
        )
        return 2
    path = log or DEFAULT_ROOT / "logs" / f"{name}-omni.log"
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
        "--model", required=True, help="a server block: h3, wan-t2v, wan-i2v"
    )
    p.add_argument("--start", action="store_true", help="launch it, not only log it")
    p.add_argument("--log", type=pathlib.Path, help="server log (with --start)")
    args = p.parse_args(argv)
    return serve(args.model, start=args.start, log=args.log)


if __name__ == "__main__":
    sys.exit(main())
