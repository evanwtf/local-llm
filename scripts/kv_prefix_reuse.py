"""Measure how much of a prompt ds4 reuses from its prefix cache (#190).

mlx-serve v26.9.1 shipped four fixes that stopped hybrid models -- Qwen 3.5/3.8
and Flash Next -- from cold-prefilling a long prompt on every turn. Our best
ds4 row runs a model from that family. This asks whether ds4 does the same
thing, and it asks ds4 rather than a wall clock: the server reports
`prompt_tokens_details.cached_tokens` per request, so reuse is read from the
engine's own accounting instead of inferred from timing that also moves with
thermal drift.

**Configuration dominates the answer.** A first attempt on 2026-09-07 read
`cached_tokens = 0` at every prompt size and looked like a total failure to
cache. The cause was a missing `--kv-disk-dir`: with no cache directory there
is no cache, and the run was measuring its own setup. Every arm here therefore
records the flags it ran under, and the script refuses to report a reuse figure
for a server it did not start itself.

The measurement is a *pair* of identical requests. The first populates the
cache and its `cached_tokens` is meaningless; the second is the reading. A
single request can only ever report a cold prefill.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import wait_ready as _wait_ready

logger = logging.getLogger(__name__)

READY_TIMEOUT_S = 300
REQUEST_TIMEOUT_S = 600


def build_prompt(n_defs: int) -> str:
    """A prompt whose length scales cleanly and whose prefix is stable.

    Generated rather than read from a corpus so the same n_defs gives a
    byte-identical prefix on every run and every machine -- a cache hit that
    depended on a file on this laptop would not reproduce anywhere else.
    """
    body = "\n".join(f"def f{i}(x): return x*{i}" for i in range(n_defs))
    return f"Consider this Python module:\n{body}\n\nHow many functions? One number."


def post_chat(port: int, model: str, prompt: str) -> dict[str, object]:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32,
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        body,
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        payload = json.load(resp)
    usage = payload.get("usage", {})
    details = usage.get("prompt_tokens_details", {})
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "cached_tokens": int(details.get("cached_tokens", 0)),
        "cache_write_tokens": int(details.get("cache_write_tokens", 0)),
    }


def measure(port: int, model: str, n_defs: int) -> dict[str, object]:
    """Two identical requests; the second one is the reading.

    The first request's cached_tokens is always 0 on a cold cache and reporting
    it would say every configuration fails. Warm, then measure.
    """
    prompt = build_prompt(n_defs)
    post_chat(port, model, prompt)
    got = post_chat(port, model, prompt)
    total = int(got["prompt_tokens"])
    cached = int(got["cached_tokens"])
    got["n_defs"] = n_defs
    got["reused_pct"] = round(100.0 * cached / total, 1) if total else 0.0
    got["reprefilled"] = total - cached
    return got


def wait_ready(port: int, model: str, deadline_s: int = READY_TIMEOUT_S) -> bool:
    """Ready means it answered a one-token request, not that /health replied.

    ds4-server has no /health -- it returns 404. The first version of this
    function polled that endpoint with urllib.request.urlopen, which *raises*
    HTTPError on a 404; HTTPError subclasses URLError, so the handler below
    read every reply as "not ready" and looped the full 300s beside a server
    that was up. The hand smoke-test that preceded the script used curl, which
    exits 0 on a 404, so it printed READY and hid the bug.

    benchmarks/agent/wait_ready.py had already learned this and says so: its
    health() is "advisory only -- it reports ok before it is true", and its
    serves() is "the real readiness test". Call its ready() rather than keep a
    second, worse copy of the same probe.
    """
    return _wait_ready.ready(
        f"http://127.0.0.1:{port}", model, "local", timeout=deadline_s
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tree", required=True, help="ds4 worktree holding ds4-server")
    p.add_argument("--gguf", required=True)
    p.add_argument("--ple", default=None)
    p.add_argument("--model", default="qwen3.8-flash-next-q4")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--ctx", type=int, default=32768)
    p.add_argument("--kv-disk-space-mb", type=int, default=8192)
    p.add_argument(
        "--no-kv-disk",
        action="store_true",
        help="omit --kv-disk-dir entirely; the control arm that shows what "
        "an unconfigured cache reports (it reports zero reuse, which is not "
        "the same finding as a cache that fails to reuse)",
    )
    p.add_argument(
        "--sizes",
        default="200,800,2000,5000",
        help="comma-separated n_defs values, smallest first",
    )
    p.add_argument("--out", default=None, help="write JSON results here")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    tree = pathlib.Path(args.tree).expanduser()
    server = tree / "ds4-server"
    if not server.is_file():
        logger.error("no ds4-server in %s", tree)
        return 1

    # check=False deliberately: a tree that is not a git checkout is a valid
    # engine (mlx-serve is a brew binary), and the version simply becomes
    # unknown rather than the run failing.
    sha = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(tree), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )

    kv_dir = pathlib.Path(f"/tmp/kv-prefix-reuse-{args.port}")
    cmd = [
        str(server),
        "--metal",
        "-m",
        args.gguf,
        "--ctx",
        str(args.ctx),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
    ]
    if args.ple:
        cmd += ["--ple", args.ple]
    if not args.no_kv_disk:
        shutil.rmtree(kv_dir, ignore_errors=True)
        kv_dir.mkdir(parents=True, exist_ok=True)
        cmd += [
            "--kv-disk-dir",
            str(kv_dir),
            "--kv-disk-space-mb",
            str(args.kv_disk_space_mb),
        ]

    logger.info("engine ds4 %s%s in %s", sha, " (DIRTY)" if dirty else "", tree)
    logger.info(
        "ctx=%d kv_disk=%s kv_space_mb=%d",
        args.ctx,
        "off" if args.no_kv_disk else str(kv_dir),
        args.kv_disk_space_mb,
    )

    log_path = pathlib.Path(f"/tmp/ds4-kvreuse-{args.port}.log")
    log = log_path.open("w")
    proc = subprocess.Popen(cmd, cwd=tree, stdout=log, stderr=subprocess.STDOUT)
    try:
        if not wait_ready(args.port, args.model):
            logger.error("server not ready in %ds", READY_TIMEOUT_S)
            return 1
        rows = []
        for n in [int(s) for s in args.sizes.split(",")]:
            row = measure(args.port, args.model, n)
            rows.append(row)
            logger.info(
                "n_defs=%-6d prompt=%-6d cached=%-6d reused=%5.1f%% reprefilled=%d",
                n,
                row["prompt_tokens"],
                row["cached_tokens"],
                row["reused_pct"],
                row["reprefilled"],
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    result = {
        "engine": "ds4",
        "engine_version": sha,
        "engine_dirty": dirty,
        "engine_tree": str(tree),
        "ctx": args.ctx,
        "kv_disk": not args.no_kv_disk,
        "kv_disk_space_mb": args.kv_disk_space_mb,
        "gguf": args.gguf,
        "ple": args.ple,
        "rows": rows,
    }
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
        logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
