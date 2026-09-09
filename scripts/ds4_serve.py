"""Start ds4-server on one of two Metal kernel routes, and prove which one ran.

    uv run python scripts/ds4_serve.py fast
    uv run python scripts/ds4_serve.py vanilla

or the wrappers, which are the same thing with a shorter name:
`scripts/ds4-fast.sh`, `scripts/ds4-vanilla.sh`.

**fast** is Metal 4 TensorOps: about 21% quicker across the agent suite (#149,
paired walls 0.900/0.768/0.690) and *not* bit-exact -- it flips greedy tokens on
long prompts, `worst_rms 1.38592`, `worst_max_abs 7.26952`. **vanilla** is the
reference kernels: bit-exact at `worst_rms 0`, slower, and the only route a
reproducible quality number could be measured on.

Both modes run the SAME binary, `~/git/ds4-metal-ref`. That build withholds the
automatic M5 enable and honours `DS4_METAL_ENABLE_TENSOR`, so one binary serves
both routes. The ordinary `~/git/ds4-metal` build cannot serve vanilla at all:
on any device whose name contains "M5" the route enables itself
(`ds4_metal.m`, `default_enable`), and its only off switch,
`DS4_METAL_DISABLE_METAL4`, turns off the whole Metal 4 family rather than the
tensor route -- a larger change than the one that was measured.

Two things this module refuses to assume.

**One server at a time.** Both modes bind the same port on purpose, so the
operating system makes them mutually exclusive: a second server cannot quietly
come up beside the first and answer half the traffic. The port is checked
before launch so the failure is a sentence rather than a bind error buried in
a log.

**The route is asserted, never assumed.** Setting an environment variable is
not evidence it took effect. This project has twice this week measured an arm
that was not running the treatment it was named for -- #149's tensor route and
#148's MTP draft head -- so the server's own log has to say which route it
took before the server is handed over.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import pathlib
import socket
import subprocess
import sys
import time

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import provenance

logger = logging.getLogger(__name__)

MODELS = pathlib.Path.home() / "models" / "qwen3.8-flash-next-ds4-q4"
DEFAULT_TREE = pathlib.Path.home() / "git" / "ds4-metal-ref"
DEFAULT_MODEL = (
    MODELS
    / "Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
)
DEFAULT_PLE = MODELS / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"

# The line each route prints. `fast` names the route it took; `vanilla` names
# the route it declined and how to override, which is why the two cannot be
# told apart by presence alone -- both mention the tensor API.
MARKERS = {
    "fast": "Metal 4 tensor API enabled for Tensor kernels",
    "vanilla": "Metal 4 tensor API available but not enabled",
}
ANY_MARKER = "Metal 4 tensor API"

SERVER_PATTERN = "ds4-server --metal"


def confirm_route(log_text: str, mode: str) -> bool:
    """Did the server take the route we asked for?

    Substring match on the marker, not "the other marker is absent": a log
    that contains neither must fail, and a log that somehow contains both is
    not a pass for either.
    """
    other = MARKERS["vanilla" if mode == "fast" else "fast"]
    return MARKERS[mode] in log_text and other not in log_text


def port_holder(port: int) -> str | None:
    """Who is listening on `port`, or None if nothing is.

    Tries to bind rather than trusting a process list: something other than
    ds4-server may hold it, and the operator needs to be told what.
    """
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            pass
        else:
            return None
    try:
        out = (
            subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            .stdout.strip()
            .splitlines()
        )
    except (OSError, subprocess.SubprocessError):
        return "something (lsof did not run)"
    return out[1].split()[0] if len(out) > 1 else "an unidentified process"


def kv_dir(mode: str) -> pathlib.Path:
    """A KV directory per route.

    ds4's disk cache runs cross-quant=accept, so one shared directory would let
    a fast-route checkpoint be resumed by a vanilla server and the reverse --
    silently mixing the one thing the two modes exist to keep apart.
    """
    return pathlib.Path.home() / ".ds4" / f"server-kv-{mode}"


def build_command(mode: str, args: argparse.Namespace, extra: list[str]) -> list[str]:
    return [
        "./ds4-server",
        "--metal",
        "-m",
        str(args.model),
        "--ple",
        str(args.ple),
        "--ctx",
        str(args.ctx),
        "--warm-weights",
        "--kv-disk-dir",
        str(args.kv or kv_dir(mode)),
        "--kv-disk-space-mb",
        "8192",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        *extra,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=sorted(MARKERS))
    parser.add_argument("--tree", type=pathlib.Path, default=DEFAULT_TREE)
    parser.add_argument("--model", type=pathlib.Path, default=DEFAULT_MODEL)
    parser.add_argument("--ple", type=pathlib.Path, default=DEFAULT_PLE)
    parser.add_argument("--kv", type=pathlib.Path, default=None)
    # One port for both modes, deliberately: the OS then guarantees only one
    # server runs. Overridable, but changing it gives up that guarantee.
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ctx", type=int, default=100000)
    parser.add_argument("--log", type=pathlib.Path, default=None)
    parser.add_argument(
        "--timeout", type=int, default=180, help="seconds to wait for the route line"
    )
    args, extra = parser.parse_known_args(argv)

    server = args.tree / "ds4-server"
    if not os.access(server, os.X_OK):
        logger.error("no runnable ds4-server at %s", server)
        logger.error(
            "Both modes need the reference build -- the one carrying ivan's "
            "'Withhold the automatic Metal 4 tensor enable' commit. Point "
            "--tree at such a tree, or build one: cd %s && make ds4-server",
            args.tree,
        )
        return 1
    for path in (args.model, args.ple):
        if not path.is_file():
            logger.error("missing model file: %s", path)
            return 1

    if (holder := port_holder(args.port)) is not None:
        logger.error("REFUSING: port %d is already held by %s", args.port, holder)
        logger.error(
            "fast and vanilla share a port so only one can run. Stop it first: "
            "pkill -f '%s'",
            SERVER_PATTERN,
        )
        return 1

    log = args.log or (
        pathlib.Path.home()
        / "bench-logs"
        / f"ds4-{args.mode}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    (args.kv or kv_dir(args.mode)).mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["DS4_METAL_ENABLE_TENSOR"] = "1" if args.mode == "fast" else "0"

    logger.info("mode=%s tree=%s port=%d", args.mode, args.tree, args.port)
    logger.info("kv=%s", args.kv or kv_dir(args.mode))
    logger.info("log=%s", log)

    with log.open("wb") as sink:
        server = subprocess.Popen(
            build_command(args.mode, args, extra),
            cwd=args.tree,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.monotonic() + args.timeout
    text = ""
    while time.monotonic() < deadline:
        text = log.read_text(errors="replace")
        if ANY_MARKER in text:
            break
        time.sleep(1)

    if not confirm_route(text, args.mode):
        logger.error("REFUSING: %s route not confirmed in %s", args.mode, log)
        for line in text.splitlines():
            if ANY_MARKER in line:
                logger.error("  got: %s", line)
                break
        else:
            logger.error("  no '%s' line appeared within %ds", ANY_MARKER, args.timeout)
        subprocess.run(["pkill", "-f", SERVER_PATTERN], check=False)
        logger.error("server stopped; a server on the wrong route is worse than none")
        return 1

    for line in text.splitlines():
        if ANY_MARKER in line:
            logger.info("route confirmed: %s", line.strip())
            break

    # #149: the confirmation used to end here, and every row taken against this
    # server then carried no way to say which route produced it. Write it down.
    #
    # Imported inside main() on purpose: ds4_route imports MARKERS from this
    # module, so a module-level import back would be circular. By the time
    # main() runs, this module is fully loaded and the import is free.
    sys.path.insert(
        0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
    )
    import ds4_route

    ds4_route.write_record(
        ds4_route.DEFAULT_RECORD,
        mode=args.mode,
        port=args.port,
        pid=server.pid,
        log=log,
    )
    logger.info("route recorded for :%d in %s", args.port, ds4_route.DEFAULT_RECORD)

    logger.info("weights are still loading (~90 GiB; a cold start takes a minute)")
    logger.info(
        "ready check: uv run python benchmarks/agent/wait_ready.py "
        "--base-url http://127.0.0.1:%d --model qwen3.8-flash-next-q4",
        args.port,
    )
    logger.info("stop: pkill -f '%s'", SERVER_PATTERN)
    return 0


if __name__ == "__main__":
    provenance.configure(show_name=True)
    raise SystemExit(main())
