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


def capture_versions(cfg, backends):
    """Record the software stack, once, into every row of this run.

    Without this, results.jsonl is undated evidence: six months on there is no
    way to attribute a row to a Claude Code version, an Ollama build, or a
    model that has since been re-pushed under the same tag. Prose in a report
    drifts away from the data; this travels with it.
    """
    def out(cmd):
        try:
            r = run(cmd, cwd=None, timeout=30)
            return r.stdout.strip().splitlines()[0] if r.stdout.strip() else None
        except Exception:
            return None

    env = {
        "claude": out(["claude", "--version"]),
        "opencode": out(["opencode", "--version"]),
        "macos": out(["sw_vers", "-productVersion"]),
        "machine": out(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "target_commit": cfg["base_commit"],
    }

    if any(b["base_url"].endswith(":11434") for b in backends.values()):
        env["ollama"] = out(["ollama", "--version"])
        # A tag can be re-pushed upstream; the digest cannot. Pin the digest.
        digests = {}
        listing = run(["ollama", "list"], cwd=None, timeout=30).stdout
        for line in listing.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                digests[parts[0]] = parts[1]
        for name, b in backends.items():
            if b["model"] in digests:
                env[f"digest_{name}"] = digests[b["model"]]

    if any(b["base_url"].endswith(":8000") for b in backends.values()):
        ds4_root = pathlib.Path(os.environ.get("DS4_ROOT", "~/git/ds4")).expanduser()
        if (ds4_root / ".git").exists():
            try:
                env["ds4_head"] = git(["rev-parse", "--short", "HEAD"], ds4_root)
                env["ds4_dirty"] = bool(git(["status", "--porcelain"], ds4_root))
            except RuntimeError:
                pass
        server = ds4_root / "ds4-server"
        if server.exists():
            # The binary may predate HEAD. Record when it was actually built.
            env["ds4_server_mtime"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(server.stat().st_mtime))
    return {k: v for k, v in env.items() if v is not None}


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


# --- clients --------------------------------------------------------------
# The client is the second axis. Holding the backend fixed and swapping the
# client asks whether the agent harness -- its system prompt, tool definitions
# and loop -- accounts for any of the difference between backends.
#
# Only `passed`, `wall_seconds` and `touched_tests` are strictly comparable
# across clients. Token and turn counts come from each client's own accounting
# and are recorded, not equated: see RESULTS.md.

def claude_argv(task, backend):
    return ["claude", "-p", task["prompt"], "--output-format", "json",
            "--permission-mode", "bypassPermissions"]


def claude_parse(stdout):
    payload = json.loads(stdout)
    usage = payload.get("usage", {})
    return dict(
        num_turns=payload.get("num_turns"),
        stop_reason=payload.get("stop_reason"),
        api_ms=payload.get("duration_api_ms"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        agent_error=payload.get("is_error"),
    )


def opencode_argv(task, backend):
    model = backend.get("opencode_model")
    if not model:
        raise SystemExit(
            f"backend {backend['model']!r} has no opencode_model in tasks.toml")
    return ["opencode", "run", "--model", model, "--format", "json",
            "--auto", task["prompt"]]


def opencode_parse(stdout):
    """Sum OpenCode's per-step accounting into one row.

    OpenCode emits a JSON event stream, not a summary object. Each assistant
    step ends with a `step_finish` carrying that step's token counts, so turns
    are counted and tokens are summed. Input tokens are *not* summed: every
    step resends the conversation, so a sum would count the same prompt many
    times over. The peak step input is recorded instead.

    Do not use the *last* step's input for this. A run's final step is often a
    short wrap-up whose input is tiny -- one observed row ended at 148 tokens
    after 12 turns -- so the last value is not a high-water mark. Rows written
    before 2026-08-17 carry the last step's input rather than the peak.
    """
    turns = 0
    out_tokens = 0
    reasoning = 0
    peak_input = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "step_finish":
            continue
        tokens = event.get("part", {}).get("tokens", {})
        turns += 1
        out_tokens += tokens.get("output") or 0
        reasoning += tokens.get("reasoning") or 0
        if tokens.get("input"):
            peak_input = max(peak_input or 0, tokens["input"])
    if not turns:
        raise json.JSONDecodeError("no step_finish events", stdout[:200], 0)
    return dict(
        num_turns=turns,
        input_tokens=peak_input,
        output_tokens=out_tokens,
        reasoning_tokens=reasoning,
    )


CLIENTS = {
    "claude": (claude_argv, claude_parse),
    "opencode": (opencode_argv, opencode_parse),
}


def one_trial(cfg, task, backend_name, backend, trial, workdir, timeout, dry_run,
              versions=None, client="claude"):
    repo = pathlib.Path(cfg["repo"]).expanduser()
    suffix = "" if client == "claude" else f"-{client}"
    name = f"{task['name']}-{backend_name}{suffix}-{trial}"
    worktree = workdir / name
    result = {
        "task": task["name"], "backend": backend_name, "trial": trial,
        "client": client,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": backend["model"], "context_tokens": backend["context_tokens"],
        "env": versions or {},
    }

    # A previous run killed mid-flight leaves its worktree behind, and
    # `git worktree add` then refuses the path. Clear it first so one aborted
    # run cannot block every later attempt at the same cell.
    if worktree.exists():
        logger.warning("%s: removing stale worktree from an aborted run", name)
        shutil.rmtree(worktree, ignore_errors=True)
        run(["git", "worktree", "prune"], cwd=repo)

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
        build_argv, parse = CLIENTS[client]
        t0 = time.monotonic()
        proc = run(build_argv(task, backend),
                   cwd=worktree, env=agent_env(backend), timeout=timeout)
        result["wall_seconds"] = round(time.monotonic() - t0, 1)
        try:
            result.update(parse(proc.stdout))
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
    p.add_argument("--client", choices=sorted(CLIENTS), default="claude",
                   help="agent harness driving the backend (default: claude)")
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

    versions = capture_versions(cfg, backends)
    versions["client"] = args.client
    logger.info("%d task(s) x %d backend(s) x %d trial(s), client=%s",
                len(tasks), len(backends), args.trials, args.client)
    logger.info("stack: %s", ", ".join(f"{k}={v}" for k, v in sorted(versions.items())))
    for trial in range(1, args.trials + 1):
        for task in tasks:
            for bname, backend in backends.items():
                r = one_trial(cfg, task, bname, backend, trial,
                              workdir, args.timeout, args.dry_run, versions,
                              client=args.client)
                with RESULTS.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
