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

**Two modes, because the confound is a real workload shape.** The first #190
run measured every size against one server and one kv dir, so each reading
inherited the previous size's cache. That is not a bug in the engine -- it is
exactly what a coding agent does, growing one conversation with a long shared
prefix -- but it was mislabelled as a per-size property. `--isolate` (default)
starts a fresh server and wipes a fresh kv dir per size, so each reading is
independent. `--sequential` keeps the one-server shape, and the row carries a
`cross_size` flag so the inherited reuse is visible rather than inferred later
from timestamps.

**The reason is read out of the log, not guessed from the number.** ds4-server
narrates every store and hit on stderr (ds4_kvstore.c:1140 at ds4-main 9ab70534,
ds4_kvstore.c:1323 at ds4-main 9ab70534):

    kv cache stored tokens=2048  trimmed=597 reason=cold      key=token-text size=... MiB save=... ms
    kv cache hit text tokens=10240 text=... quant=... key=... load=... ms file=/path

`cached_tokens` alone cannot tell a cold checkpoint from a continued one, and
that is how the first #190 read-out published a wrong mechanism. Every row
carries the `reason` of the store that produced its reuse, the `file` the hit
landed on, and whether that file was written during a different size's
measurement.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import wait_ready as _wait_ready

logger = logging.getLogger(__name__)

READY_TIMEOUT_S = 300
REQUEST_TIMEOUT_S = 600

# The server narrates every store and hit on stderr (ds4_kvstore.c:1140 at ds4-main 9ab70534, ds4_kvstore.c:1323 at ds4-main 9ab70534).
# The store line has no file path -- the file is content-addressed -- so a hit
# is matched to the store that produced it by order: the reading request reuses
# the most recent store before it.
STORE_RE = re.compile(r"kv cache stored tokens=(\d+) trimmed=\d+ reason=(\w+)")
HIT_RE = re.compile(r"kv cache hit text tokens=(\d+).*file=(\S+)$")


@dataclass
class StoreEvent:
    reason: str
    tokens: int


@dataclass
class HitEvent:
    file: str
    tokens: int


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


def measure(
    port: int,
    model: str,
    n_defs: int,
    log_reader: LogReader | None = None,
) -> dict[str, object]:
    """Two identical requests; the second one is the reading.

    The first request's cached_tokens is always 0 on a cold cache and reporting
    it would say every configuration fails. Warm, then measure.

    When log_reader is given, the server log is read between the two requests
    so the store and hit events can be attributed to the request that produced
    them. The log fields are only added when a log_reader is provided, so the
    existing tests that call measure without one are unchanged.
    """
    prompt = build_prompt(n_defs)
    if log_reader is not None:
        log_reader.mark()
    post_chat(port, model, prompt)
    if log_reader is not None:
        warm_stores, warm_hits = parse_log(log_reader.read_since_mark())
        log_reader.mark()
    got = post_chat(port, model, prompt)
    if log_reader is not None:
        reading_stores, reading_hits = parse_log(log_reader.read_since_mark())
        got["warm_stores"] = warm_stores
        got["warm_hits"] = warm_hits
        got["reading_stores"] = reading_stores
        got["reading_hits"] = reading_hits
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


def parse_log(text: str) -> tuple[list[StoreEvent], list[HitEvent]]:
    """Parse a server log chunk into store and hit events.

    A store line records the reason (cold, continued, evict, ...) and the
    stored token count. A hit line records the file it landed on and the loaded
    token count. Lines that are neither are ignored.
    """
    stores: list[StoreEvent] = []
    hits: list[HitEvent] = []
    for line in text.splitlines():
        m = STORE_RE.search(line)
        if m:
            stores.append(StoreEvent(reason=m.group(2), tokens=int(m.group(1))))
            continue
        m = HIT_RE.search(line)
        if m:
            hits.append(HitEvent(file=m.group(2), tokens=int(m.group(1))))
    return stores, hits


class LogReader:
    """Read a server log file incrementally, so events can be attributed to
    the request that produced them.

    The server writes its kv cache narration to stderr, which is unbuffered, so
    the lines are in the file by the time the request that produced them has
    returned. mark() records the current end of the file; read_since_mark()
    returns everything written after the mark.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.pos = 0

    def mark(self) -> None:
        with open(self.path, "r", errors="replace") as fh:
            self.pos = fh.seek(0, 2)

    def read_since_mark(self) -> str:
        with open(self.path, "r", errors="replace") as fh:
            fh.seek(self.pos)
            text = fh.read()
            self.pos = fh.tell()
        return text


def build_row(
    n: int, got: dict[str, object], all_stores: list[tuple[int, str, int]]
) -> dict[str, object]:
    """Build the JSON row for size n.

    all_stores is the chronological store history up to the reading hit, as
    (size, reason, tokens). The reading request reuses the most recent store,
    so its reason is that store's reason, and cross_size is whether that store
    was written during a different size's measurement. In isolate mode the
    history is only this size's own stores, so cross_size is always False.
    """
    row = {
        "n_defs": n,
        "prompt_tokens": got["prompt_tokens"],
        "cached_tokens": got["cached_tokens"],
        "cache_write_tokens": got["cache_write_tokens"],
        "reused_pct": got["reused_pct"],
        "reprefilled": got["reprefilled"],
    }
    reading_hits = got.get("reading_hits", [])
    if reading_hits:
        hit = max(reading_hits, key=lambda h: h.tokens)
        row["file"] = hit.file
        if all_stores:
            last_size, last_reason, _ = all_stores[-1]
            row["reason"] = last_reason
            row["cross_size"] = last_size != n
        else:
            row["reason"] = None
            row["cross_size"] = False
    else:
        row["file"] = None
        row["reason"] = None
        row["cross_size"] = False
    return row


def fresh_kv_dir(port: int, n: int | None = None) -> pathlib.Path:
    """A fresh kv dir for a size, or for the whole run in sequential mode."""
    suffix = f"-{n}" if n is not None else ""
    return pathlib.Path(f"/tmp/kv-prefix-reuse-{port}{suffix}")


def wipe_kv_dir(kv_dir: pathlib.Path) -> None:
    """Empty the kv dir so a fresh server starts against no cache."""
    shutil.rmtree(kv_dir, ignore_errors=True)
    kv_dir.mkdir(parents=True, exist_ok=True)


def build_server_cmd(args: argparse.Namespace, kv_dir: pathlib.Path) -> list[str]:
    """The ds4-server command for one measurement."""
    tree = pathlib.Path(args.tree).expanduser()
    server = tree / "ds4-server"
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
    # Passed only when set: these gate the cold checkpoint and the continued
    # checkpoint step respectively, and their engine defaults (30000 and
    # 10000) are what #190 measured against.
    if args.kv_cache_cold_max_tokens is not None:
        cmd += ["--kv-cache-cold-max-tokens", str(args.kv_cache_cold_max_tokens)]
    if args.kv_cache_continued_interval_tokens is not None:
        cmd += [
            "--kv-cache-continued-interval-tokens",
            str(args.kv_cache_continued_interval_tokens),
        ]
    if not args.no_kv_disk:
        cmd += [
            "--kv-disk-dir",
            str(kv_dir),
            "--kv-disk-space-mb",
            str(args.kv_disk_space_mb),
        ]
    return cmd


def start_server(
    args: argparse.Namespace, kv_dir: pathlib.Path, log_path: pathlib.Path
) -> tuple[subprocess.Popen, object, LogReader]:
    """Start ds4-server against a wiped kv dir and a fresh log.

    Returns (proc, log, reader). The caller must stop the server with
    stop_server. The kv dir is wiped here so a fresh server never inherits a
    previous size's cache -- the #190 confound.
    """
    if not args.no_kv_disk:
        wipe_kv_dir(kv_dir)
    cmd = build_server_cmd(args, kv_dir)
    log = log_path.open("w")
    proc = subprocess.Popen(
        cmd,
        cwd=pathlib.Path(args.tree).expanduser(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log, LogReader(log_path)


def stop_server(proc: subprocess.Popen, log: object) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tree", required=True, help="ds4 worktree holding ds4-server")
    p.add_argument("--gguf", required=True)
    p.add_argument("--ple", default=None)
    p.add_argument("--model", default="qwen3.8-flash-next-q4")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--ctx", type=int, default=32768)
    p.add_argument("--kv-disk-space-mb", type=int, default=8192)
    # The two knobs #190 showed actually move the number. The disk budget is
    # measurably inert (8 GiB and 32 GiB were byte-identical at every prompt
    # size); these are not. Default None means "leave the engine default
    # alone", so an unset flag is absent from the command line rather than
    # passed as the value we believe the default to be.
    p.add_argument("--kv-cache-cold-max-tokens", type=int, default=None)
    p.add_argument("--kv-cache-continued-interval-tokens", type=int, default=None)
    p.add_argument(
        "--no-kv-disk",
        action="store_true",
        help="omit --kv-disk-dir entirely; the control arm that shows what "
        "an unconfigured cache reports (it reports zero reuse, which is not "
        "the same finding as a cache that fails to reuse)",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--isolate",
        dest="mode",
        action="store_const",
        const="isolate",
        help="a fresh server and a fresh kv dir per size (default)",
    )
    mode.add_argument(
        "--sequential",
        dest="mode",
        action="store_const",
        const="sequential",
        help="one server and one kv dir for all sizes; the growing-prefix "
        "shape a coding agent produces",
    )
    p.add_argument(
        "--sizes",
        default="200,800,2000,5000",
        help="comma-separated n_defs values, smallest first",
    )
    p.add_argument(
        "--out",
        default=None,
        help="write JSON results and one server log per arm to this directory",
    )
    p.set_defaults(mode="isolate")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    sizes = [int(s) for s in args.sizes.split(",")]
    out_dir = pathlib.Path(args.out).expanduser() if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("engine ds4 %s%s in %s", sha, " (DIRTY)" if dirty else "", tree)
    logger.info(
        "mode=%s ctx=%d kv_disk=%s kv_space_mb=%d",
        args.mode,
        args.ctx,
        "off" if args.no_kv_disk else "on",
        args.kv_disk_space_mb,
    )

    rows = []
    if args.mode == "isolate":
        for n in sizes:
            kv_dir = fresh_kv_dir(args.port, n)
            log_path = (
                out_dir / f"isolate-{n}-server.log"
                if out_dir
                else pathlib.Path(f"/tmp/ds4-kvreuse-{args.port}-{n}.log")
            )
            proc, log, reader = start_server(args, kv_dir, log_path)
            try:
                if not wait_ready(args.port, args.model):
                    logger.error("server not ready in %ds", READY_TIMEOUT_S)
                    return 1
                got = measure(args.port, args.model, n, reader)
                # Isolate mode: each size has its own server and kv dir, so the
                # store history is only this size's own stores -- no cross-size
                # reuse is possible.
                all_stores = [(n, s.reason, s.tokens) for s in got["warm_stores"]]
                row = build_row(n, got, all_stores)
                rows.append(row)
                logger.info(
                    "n_defs=%-6d prompt=%-6d cached=%-6d reused=%5.1f%% "
                    "reason=%s cross_size=%s",
                    n,
                    row["prompt_tokens"],
                    row["cached_tokens"],
                    row["reused_pct"],
                    row["reason"],
                    row["cross_size"],
                )
            finally:
                stop_server(proc, log)
    else:  # sequential
        kv_dir = fresh_kv_dir(args.port)
        log_path = (
            out_dir / "sequential-server.log"
            if out_dir
            else pathlib.Path(f"/tmp/ds4-kvreuse-{args.port}.log")
        )
        proc, log, reader = start_server(args, kv_dir, log_path)
        all_stores: list[tuple[int, str, int]] = []
        try:
            if not wait_ready(args.port, args.model):
                logger.error("server not ready in %ds", READY_TIMEOUT_S)
                return 1
            for n in sizes:
                got = measure(args.port, args.model, n, reader)
                for s in got["warm_stores"]:
                    all_stores.append((n, s.reason, s.tokens))
                row = build_row(n, got, all_stores)
                rows.append(row)
                for s in got["reading_stores"]:
                    all_stores.append((n, s.reason, s.tokens))
                logger.info(
                    "n_defs=%-6d prompt=%-6d cached=%-6d reused=%5.1f%% "
                    "reason=%s cross_size=%s",
                    n,
                    row["prompt_tokens"],
                    row["cached_tokens"],
                    row["reused_pct"],
                    row["reason"],
                    row["cross_size"],
                )
        finally:
            stop_server(proc, log)

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
        "mode": args.mode,
        "rows": rows,
    }
    if out_dir:
        (out_dir / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        logger.info("wrote %s", out_dir / "results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
