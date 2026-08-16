#!/usr/bin/env python3
"""Benchmark local models as Claude Code backends.

Each trial hollows out one function in a real repository, hands the repo to
Claude Code running against one backend, and then runs the repository's own
test suite. The suite is the oracle: pass or fail, no rubric, no judge model.

    uv run benchmarks/agent/run.py --trials 3
    uv run benchmarks/agent/run.py --backend qwen --task mbox-strip-envelope
    uv run benchmarks/agent/run.py --dry-run      # verify tasks, run no agent

Every trial runs in its own git worktree, and the excision is committed there,
so an agent cannot restore the answer with `git checkout`.

Results append to results.jsonl. Nothing is overwritten, so runs accumulate.
"""
import argparse
import json
import logging
import os
import pathlib
import shutil
import subprocess
import time
import tomllib

import excise

logger = logging.getLogger("agent-bench")
HERE = pathlib.Path(__file__).parent
RESULTS = HERE / "results.jsonl"


def run(cmd, cwd, env=None, timeout=None):
    return subprocess.run(
        cmd, cwd=cwd, env=env, timeout=timeout,
        capture_output=True, text=True,
    )


def git(args, cwd):
    r = run(["git", *args], cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def pytest_passes(worktree, tests, timeout):
    """Run the oracle. Returns (passed, summary_line)."""
    r = run(["uv", "run", "pytest", "-q", *tests], cwd=worktree, timeout=timeout)
    tail = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return r.returncode == 0, (tail[-1] if tail else "no output")


def agent_env(backend):
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.update(
        ANTHROPIC_BASE_URL=backend["base_url"],
        ANTHROPIC_AUTH_TOKEN=backend["auth_token"],
        ANTHROPIC_MODEL=backend["model"],
        ANTHROPIC_DEFAULT_SONNET_MODEL=backend["model"],
        ANTHROPIC_DEFAULT_OPUS_MODEL=backend["model"],
        ANTHROPIC_DEFAULT_HAIKU_MODEL=backend["model"],
        CLAUDE_CODE_MAX_CONTEXT_TOKENS=str(backend["context_tokens"]),
    )
    return env


def one_trial(cfg, task, backend_name, backend, trial, workdir, timeout, dry_run):
    repo = pathlib.Path(cfg["repo"]).expanduser()
    name = f"{task['name']}-{backend_name}-{trial}"
    worktree = workdir / name
    result = {
        "task": task["name"], "backend": backend_name, "trial": trial,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    git(["worktree", "add", "--detach", str(worktree), cfg["base_commit"]], repo)
    try:
        # 1. Hollow out the target and commit it, so `git checkout` cannot undo it.
        target = worktree / task["file"]
        removed = excise.excise(target, task["symbol"])
        result["removed_lines"] = len(removed.splitlines())
        git(["add", "-A"], worktree)
        git(["-c", "user.email=bench@local", "-c", "user.name=bench",
             "commit", "-q", "-m", f"benchmark: remove {task['symbol']}"], worktree)

        # 2. Control: the tests must fail now, or the task proves nothing.
        ok, summary = pytest_passes(worktree, task["tests"], timeout)
        result["control_fails_as_expected"] = not ok
        if ok:
            logger.error("%s: tests still pass after excision -- task is broken", name)
            result["error"] = "control passed"
            return result
        if dry_run:
            result["dry_run"] = True
            logger.info("%s: control ok (%s)", name, summary)
            return result

        # 3. Hand it to the agent.
        t0 = time.monotonic()
        proc = run(
            ["claude", "-p", task["prompt"], "--output-format", "json",
             "--permission-mode", "bypassPermissions"],
            cwd=worktree, env=agent_env(backend), timeout=timeout,
        )
        result["wall_seconds"] = round(time.monotonic() - t0, 1)
        try:
            payload = json.loads(proc.stdout)
            usage = payload.get("usage", {})
            result.update(
                num_turns=payload.get("num_turns"),
                stop_reason=payload.get("stop_reason"),
                api_ms=payload.get("duration_api_ms"),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                agent_error=payload.get("is_error"),
            )
        except json.JSONDecodeError:
            result["agent_error"] = True
            result["stderr_tail"] = proc.stderr[-400:]

        # 4. The oracle.
        passed, summary = pytest_passes(worktree, task["tests"], timeout)
        result["passed"] = passed
        result["pytest"] = summary
        # Guard against the obvious cheat.
        diff = git(["diff", "HEAD", "--stat", "--", "tests/"], worktree)
        result["touched_tests"] = bool(diff)
        logger.info("%s: %s in %ss (%s)", name,
                    "PASS" if passed else "FAIL", result.get("wall_seconds"), summary)
    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
        logger.error("%s: timed out after %ss", name, timeout)
    finally:
        shutil.rmtree(worktree, ignore_errors=True)
        run(["git", "worktree", "prune"], cwd=repo)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--backend", action="append", help="repeatable; default all")
    p.add_argument("--task", action="append", help="repeatable; default all")
    p.add_argument("--timeout", type=int, default=1800, help="seconds per step")
    p.add_argument("--dry-run", action="store_true",
                   help="verify each task's control failure, run no agent")
    p.add_argument("--tasks-file", default=str(HERE / "tasks.toml"))
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = tomllib.loads(pathlib.Path(args.tasks_file).read_text())
    tasks = [t for t in cfg["task"] if not args.task or t["name"] in args.task]
    backends = {k: v for k, v in cfg["backend"].items()
                if not args.backend or k in args.backend}
    if not tasks or not backends:
        raise SystemExit("no tasks or no backends selected")

    workdir = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "agent-bench"
    workdir.mkdir(parents=True, exist_ok=True)

    logger.info("%d task(s) x %d backend(s) x %d trial(s)",
                len(tasks), len(backends), args.trials)
    for trial in range(1, args.trials + 1):
        for task in tasks:
            for bname, backend in backends.items():
                r = one_trial(cfg, task, bname, backend, trial,
                              workdir, args.timeout, args.dry_run)
                with RESULTS.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
