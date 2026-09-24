#!/usr/bin/env python3
"""Check a remote client is fit to run trials against the server. #562/#579

`benchmarks/agent/preflight.py` checks the machine that runs the harness *and*
serves the model. A remote client splits those, and the first remote A/B
(#579) showed what that misses: the agent's `python` was the harness's own
virtualenv, 52 of 85 `python -m pytest` calls on the client died at collection,
and the batch measured a broken toolchain instead of the server. Nothing ran
the agent's environment before the batch.

This does, before any trial. Each check prints PASS or FAIL with the number or
path it read; the exit status is non-zero if any check fails.

1. **Tools**: `uv`, `git`, `opencode`, and bwrap's namespace probe. OpenCode's
   version must match the server's facts file, so the client is not a second
   variable.
2. **Server**: the backend's `/v1/models` answers with its model; the server's
   node_exporter and DCGM exporter are readable (the memory gate and the
   idle watchdog depend on them).
3. **OpenCode config**: the backend's `opencode_model` is declared.
4. **Sandbox clone**: `sandbox/<name>` exists at the pinned base commit.
5. **Trial environment**: an export of the target at the base commit, prepared
   exactly as a trial is (`run.prepare_env`), then `python -m pytest
   --collect-only` run inside it with exactly the environment the agent gets
   (`run.agent_env`). `python` must resolve inside the trial's `.venv` and
   collection must succeed.

    LOCAL_LLM_SERVER_HOST=<server> LOCAL_LLM_SERVER_FACTS=<facts.json> \\
        uv run python scripts/client_preflight.py --backend <name>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import opencode_config
import preflight
import remote
import run


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str) -> bool:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
        self.failed += 0 if ok else 1
        return ok


def _version(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(
            argv, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else None


def _get(url: str, timeout: float = 10.0) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except OSError:
        return None


def trial_environment(cfg: dict, task: dict, backend: dict, rep: Report) -> None:
    """Build one trial workspace the harness's way and run the agent's command."""
    target = run.task_target(cfg, task)
    clone = run.sandbox_checkout(
        pathlib.Path(target["repo"]).expanduser(), target["sandbox"]
    )
    if clone is None:
        rep.check("trial environment", False, "no sandbox clone to export from")
        return
    work = pathlib.Path(tempfile.mkdtemp(prefix="client-preflight-"))
    try:
        dest = work / "trial"
        subprocess.run(
            ["git", "clone", "-q", "--no-checkout", str(clone), str(dest)], check=True
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "-q", target["base_commit"]],
            check=True,
        )
        prepared = run.prepare_env(dest)
        rep.check(
            "trial env prepared",
            bool(prepared.get("env_prepared")),
            json.dumps(prepared),
        )
        env = run.agent_env(backend, dest)
        which = subprocess.run(
            ["sh", "-c", "command -v python"],
            cwd=dest,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        rep.check(
            "agent's python is the trial's",
            which.startswith(str(dest / ".venv")),
            which or "python not on the agent's PATH",
        )
        tests = task.get("tests") or []
        got = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q", *tests],
            cwd=dest,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        tail = (got.stdout or got.stderr).strip().splitlines()[-1:] or ["(no output)"]
        rep.check(
            f"agent's `python -m pytest` collects {task['name']}",
            got.returncode == 0,
            tail[0][:160],
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--backend", required=True)
    p.add_argument(
        "--task",
        default="storage-blob-put",
        help="a Python-excision task whose tests import the target package",
    )
    p.add_argument("--tasks-file", type=pathlib.Path, default=run.HERE / "tasks.toml")
    args = p.parse_args(argv)
    rep = Report()

    server = remote.host()
    facts = remote.server_facts()
    rep.check("server host", bool(server), server or f"{remote.ENV_HOST} unset")
    rep.check(
        "server facts",
        facts is not None,
        (facts or {}).get("directory") or f"{remote.ENV_FACTS} unset",
    )
    cfg = tomllib.loads(args.tasks_file.read_text())
    backend = cfg["backend"].get(args.backend)
    if backend is None:
        rep.check("backend", False, f"{args.backend} not in {args.tasks_file}")
        return 1
    if server:
        backend = remote.rewrite(backend, server)

    # 1. tools
    for tool in ("uv", "git", "opencode"):
        rep.check(
            f"tool {tool}",
            shutil.which(tool) is not None,
            shutil.which(tool) or "missing",
        )
    mine = _version(["opencode", "--version"])
    theirs = ((facts or {}).get("env") or {}).get("opencode")
    rep.check(
        "opencode matches the server's",
        bool(mine) and (theirs is None or mine == theirs),
        f"client {mine}, server {theirs}",
    )
    rep.check(
        "bwrap namespaces", preflight.bwrap_probe(preflight.BWRAP), str(preflight.BWRAP)
    )

    # 2. server
    models = _get(f"{backend['base_url'].rstrip('/').removesuffix('/v1')}/v1/models")
    rep.check(
        "backend answers with its model",
        bool(models) and backend["model"] in models,
        f"{backend['base_url']} serves {backend['model']}" if models else "no answer",
    )
    if server:
        mem = remote.server_meminfo(server)
        rep.check(
            "server node_exporter",
            "MemAvailable" in mem,
            f"MemAvailable {mem.get('MemAvailable', 0):.1f} GiB",
        )
        watts = remote.gpu_watts(server)
        rep.check("server DCGM exporter", watts is not None, f"{watts} W")

    # 3. OpenCode config
    missing = opencode_config.missing(
        {args.backend: backend}, own_tier=backend.get("tier")
    )
    rep.check(
        "opencode model declared",
        not missing,
        backend.get("opencode_model", "?") if not missing else ", ".join(missing),
    )

    # 4 + 5. sandbox clone and the trial environment
    task = next((t for t in cfg["task"] if t["name"] == args.task), None)
    if task is None:
        rep.check("task", False, f"{args.task} not in {args.tasks_file}")
    else:
        target = run.task_target(cfg, task)
        clone = run.sandbox_checkout(
            pathlib.Path(target["repo"]).expanduser(), target["sandbox"]
        )
        head = (
            subprocess.run(
                ["git", "-C", str(clone), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if clone
            else ""
        )
        rep.check(
            "sandbox clone at the base commit",
            bool(head) and head.startswith(target["base_commit"]),
            f"{clone} at {head[:12] or 'missing'}, want {target['base_commit']}",
        )
        trial_environment(cfg, task, backend, rep)

    print(f"{'OK' if not rep.failed else 'FAILED'}: {rep.failed} check(s) failed")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
