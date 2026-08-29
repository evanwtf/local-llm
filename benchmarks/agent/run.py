#!/usr/bin/env python3
"""Benchmark local models, and the agent clients that drive them.

Each trial hollows out one function in a real repository, hands the repo to an
agent client running against one backend, and then runs the repository's own
test suite. The suite is the oracle: pass or fail, no rubric, no judge model.

    uv run benchmarks/agent/run.py --trials 3
    uv run benchmarks/agent/run.py --backend qwen --task mbox-strip-envelope
    uv run benchmarks/agent/run.py --dry-run      # verify tasks, run no agent

    # Two clients, interleaved per task so server drift hits both equally:
    uv run benchmarks/agent/run.py --backend ds4 --client claude --client codex

Two axes: `--backend` selects the model and server, `--client` the agent
harness (claude, opencode, codex). They interact -- no client is fastest on
every backend -- so both belong in any conclusion.

Each trial runs in a standalone copy of the pinned commit, exported with
`git archive`, whose only commit is the already-excised state. There is no
shared object store and no path back to the source repo: an agent can neither
recover the original body from history nor reach the operator's checkout.

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
import urllib.error
import urllib.request

import excise
import grade
import preflight
import results

logger = logging.getLogger("agent-bench")
HERE = pathlib.Path(__file__).parent
RESULTS = HERE / "results.jsonl"
# Outside the repo on purpose: transcripts carry file contents the agent
# read, and this repo does not commit prompts.
DEFAULT_CLIENT_LOG = "~/bench-logs"
DEFAULT_SOLUTIONS = "~/bench-solutions"
# Gates are cheap next to a trial -- ruff is under a second, mypy runs cold in
# a fresh worktree and still finishes in seconds -- but they must never be the
# thing that hangs a run, so they get their own short deadline.
GATE_TIMEOUT = 300


def run(cmd, cwd, env=None, timeout=None):
    # stdin must be closed, not inherited. `codex exec` prints "Reading
    # additional input from stdin..." and blocks forever on an inherited stdin
    # that never reaches EOF -- it hung a trial for 11 minutes before this was
    # found. Any agent client may do the same; none of them should be waiting
    # on input here.
    return subprocess.run(
        cmd, cwd=cwd, env=env, timeout=timeout,
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
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


def parse_ollama_show(show):
    """Read the sampler out of an Ollama `/api/show` response.

    Ollama reports a model's *modelfile*, not the sampler actually in force. If
    the modelfile sets no PARAMETER lines the engine's built-in defaults apply
    and the API does not say what they are.

    That silence is the whole reason #28 and #36 happened: `ornith-1.5:35b`
    sets none, so Ollama's defaults (top_p 0.9, repeat_penalty 1.1) applied
    while `llamacpp-up` used 0.95 and never set repeat_penalty. Two backends,
    two different samplers, nothing on either row saying so.

    So an empty modelfile records `engine defaults (unrecorded)` rather than
    nothing at all. A missing key reads as "not checked"; an explicit string
    reads as "checked, and the answer is that we cannot see it".
    """
    modelfile = show.get("modelfile") if isinstance(show, dict) else None
    if not modelfile:
        return {}
    sampling = {}
    for line in modelfile.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].upper() == "PARAMETER":
            sampling[parts[1]] = parts[2]
    return {
        "sampling": sampling,
        "sampling_source": "modelfile" if sampling
                           else "engine defaults (unrecorded)",
    }


def probe_ollama(backend):
    """Ask Ollama what sampler a model declares. Returns {} on any failure."""
    url = backend.get("base_url")
    if not url:
        return {}
    body = json.dumps({"model": backend["model"]}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/api/show", body,
        {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as fh:
            return parse_ollama_show(json.load(fh))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
            OSError, KeyError) as exc:
        logger.debug("no /api/show from %s: %s", url, exc)
        return {}


def probe_server(backend):
    """Ask the running server what it is actually serving.

    `/props` answers with the exact GGUF path, the build the binary was made
    from, and -- the reason this exists -- the sampling parameters in force.

    Those parameters were never recorded, and #26 spent its life blaming a KV
    cache for a 1.74x median wall-time spread that is really the model emitting
    1.4x the tokens on an unlucky draw. llama.cpp serves at temperature 1.0,
    top_p 0.95, top_k 40 and a fresh random seed per request, so every trial is
    an independent sample from a wide distribution. That is a legitimate thing
    to measure -- it is what the model does in real use -- but it has to be on
    the row, or the next person reads a median as if it were a measurement.

    A server behind the Claude Code shim does not answer GET, so a backend can
    name `props_url` to point at the real server. Returns {} on any failure:
    this is provenance, and it must never take a trial down with it.
    """
    url = backend.get("props_url") or backend.get("base_url")
    if not url:
        return {}
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/props", timeout=10) as fh:
            props = json.load(fh)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("no /props from %s: %s", url, exc)
        return {}

    got = {}
    for key in ("model_path", "model_alias", "build_info", "total_slots"):
        if props.get(key) is not None:
            got[key] = props[key]
    params = (props.get("default_generation_settings") or {}).get("params") or {}
    sampling = {k: params[k] for k in
                ("temperature", "top_p", "top_k", "min_p", "seed", "samplers")
                if k in params}
    if sampling:
        got["sampling"] = sampling
    return got


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
        "codex": out(["codex", "--version"]),
        "macos": out(["sw_vers", "-productVersion"]),
        "machine": out(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "target_commit": cfg["base_commit"],
    }

    # .get(): a hosted backend has no base_url at all.
    if any((b.get("base_url") or "").endswith(":11434") for b in backends.values()):
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

    if any((b.get("base_url") or "").endswith(":8000") for b in backends.values()):
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

    # llama.cpp is a source build off a pull request, not a released tag, so
    # "llama.cpp" alone would not identify it. Pin the commit the way ds4_head
    # pins ds4. There is no digest to record for a GGUF -- hashing 79 GB per
    # run is not free -- so the file's name, size and mtime stand in for one.
    # :11500 is the shim that fronts :8020 for Claude Code; either port means
    # this stack is in the run.
    if any((b.get("base_url") or "").endswith((":8020", ":11500"))
           for b in backends.values()):
        lcpp = pathlib.Path(
            os.environ.get("LLAMACPP_ROOT", "~/git/llama.cpp")).expanduser()
        if (lcpp / ".git").exists():
            try:
                env["llamacpp_head"] = git(["rev-parse", "--short", "HEAD"], lcpp)
                env["llamacpp_dirty"] = bool(git(["status", "--porcelain"], lcpp))
            except RuntimeError:
                pass
        server = lcpp / "build" / "bin" / "llama-server"
        if server.exists():
            env["llamacpp_server_mtime"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(server.stat().st_mtime))
    # Which GGUF is in service comes from the server itself, below. An earlier
    # revision globbed `GGUF_ROOT/*/*.gguf`, which spans every quant sitting in
    # that directory: rows recorded during the Q3 runs list the Q2 shards too,
    # and `gguf_bytes` sums both quants into a number that describes neither.
    # Those rows are still in results.jsonl; read `servers` instead.

    # One probe per backend, keyed by name, because a run can span several and
    # each row records which one it used.
    servers = {name: (probe_server(b) or probe_ollama(b))
               for name, b in backends.items()}
    servers = {k: v for k, v in servers.items() if v}
    if servers:
        env["servers"] = servers
    return {k: v for k, v in env.items() if v is not None}


def agent_env(backend):
    env = dict(os.environ)

    # A backend with no base_url is the hosted API -- the reference point the
    # local backends are measured against. Leave the ambient auth alone and
    # override nothing but the model: pointing ANTHROPIC_BASE_URL at
    # api.anthropic.com and clearing ANTHROPIC_API_KEY would break the normal
    # subscription login.
    if not backend.get("base_url"):
        env["ANTHROPIC_MODEL"] = backend["model"]
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = backend["model"]
        return env

    env.pop("ANTHROPIC_API_KEY", None)
    env.update(
        ANTHROPIC_BASE_URL=backend["base_url"],
        ANTHROPIC_AUTH_TOKEN=backend["auth_token"],
        ANTHROPIC_MODEL=backend["model"],
        ANTHROPIC_DEFAULT_SONNET_MODEL=backend["model"],
        ANTHROPIC_DEFAULT_OPUS_MODEL=backend["model"],
        ANTHROPIC_DEFAULT_HAIKU_MODEL=backend["model"],
        CLAUDE_CODE_MAX_CONTEXT_TOKENS=str(backend["context_tokens"]),
        # Codex profiles declare `env_key = "CODEX_API_KEY"`. The harness never
        # set it, so every Codex row before 2026-08-28 depended on the operator
        # having exported it in the shell that launched the run. Unattended,
        # Codex exits in 0.7 s with "Missing environment variable" and the row
        # records as a model failure -- indistinguishable, on the row, from the
        # model giving up. Same token as the Anthropic path: both are the local
        # server's non-secret local credential.
        CODEX_API_KEY=backend["auth_token"],
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
    argv = ["claude", "-p", task["prompt"], "--output-format", "json",
            "--permission-mode", "bypassPermissions"]
    # Reasoning effort is a client-side setting, not a server one: it belongs
    # on the command line rather than in the environment. Local backends leave
    # it unset and take the model's own default.
    if backend.get("effort"):
        argv += ["--effort", backend["effort"]]
    return argv


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


def codex_argv(task, backend):
    profile = backend.get("codex_profile")
    if not profile:
        raise SystemExit(
            f"backend {backend['model']!r} has no codex_profile in tasks.toml")
    # `--ephemeral` keeps session rollout files off disk, which matters when a
    # matrix writes hundreds of them. `workspace-write` is the least permission
    # that lets the agent edit the checkout it was given.
    return ["codex", "exec", "--profile", profile, "--json",
            "--sandbox", "workspace-write", "--ephemeral",
            task["prompt"]]


def codex_parse(stdout):
    """Read Codex's JSONL event stream.

    **`num_turns` is deliberately None for Codex.** Codex emits one
    `turn.completed` per *exec*, not per model round trip, so counting them
    yields 1 for a session where Claude Code reports 10. Those numbers are not
    the same quantity and putting them in one column would invite a false
    comparison. Its work appears instead as `command_execution` and
    `file_change` items, recorded as `tool_items`.

    `codex_error_items` counts `error` items. These are not necessarily
    failures: driving a model Codex has no metadata for emits one warning per
    run ("Model metadata for ... not found. Defaulting to fallback metadata"),
    which is itself worth recording -- see RESULTS.md.
    """
    out_tokens = 0
    reasoning = 0
    peak_input = None
    exec_turns = 0
    tool_items = 0
    errors = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "item.completed":
            item_type = (event.get("item") or {}).get("type")
            if item_type == "error":
                errors += 1
            elif item_type in ("command_execution", "file_change"):
                tool_items += 1
        elif kind == "turn.completed":
            usage = event.get("usage", {})
            exec_turns += 1
            out_tokens += usage.get("output_tokens") or 0
            reasoning += usage.get("reasoning_output_tokens") or 0
            if usage.get("input_tokens"):
                peak_input = max(peak_input or 0, usage["input_tokens"])
    if not exec_turns:
        raise json.JSONDecodeError("no turn.completed events", stdout[:200], 0)
    return dict(
        num_turns=None,
        codex_exec_turns=exec_turns,
        tool_items=tool_items,
        input_tokens=peak_input,
        output_tokens=out_tokens,
        reasoning_tokens=reasoning,
        codex_error_items=errors,
    )


CLIENTS = {
    "claude": (claude_argv, claude_parse),
    "opencode": (opencode_argv, opencode_parse),
    "codex": (codex_argv, codex_parse),
}


def source_repo_intact(repo, commit):
    """Is the source repository still clean and on the commit we started from?

    Cheap tripwire, recorded per trial. It cannot prevent an escape -- it
    detects one that already happened, which is what was missing when this
    went unnoticed for a whole run on 2026-08-17.
    """
    try:
        dirty = bool(git(["status", "--porcelain"], repo))
        head = git(["rev-parse", "--short", "HEAD"], repo)
    except RuntimeError:
        return False
    return not dirty and head.startswith(commit[:len(head)][:7])


def build_checkout(repo, commit, dest):
    """Materialise `commit` as a standalone directory with no link to `repo`.

    This used to be `git worktree add`. A linked worktree shares the parent's
    object store and keeps a pointer back to it, and on 2026-08-17 an agent
    followed that path: it reached the source repository and ran a checkout
    there, leaving the operator's working copy on a benchmark commit with agent
    edits in it. See METHODOLOGY.md.

    A worktree isolates files. It does not isolate the agent, which has a shell
    and can go anywhere. So the tree is now exported with `git archive` -- the
    files arrive with no `.git` at all, and the caller creates a fresh
    repository whose only commit is the already-excised state.

    Two things follow. The parent repo is not reachable through anything in the
    checkout, and the original function body is not recoverable from history,
    because this checkout has no history that ever contained it.
    """
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", "--format=tar", commit],
                             cwd=repo, capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dest)],
                   input=archive.stdout, check=True)


def save_transcript(client_log, name, stdout, stderr, result, partial=False):
    """Keep the client's own event stream for this trial, if asked to.

    A results row records that the agent did not fix the code, never why. The
    autocompact-thrashing message that explained a 842s run existed only here.

    Opt-in and written outside the repo by default: transcripts carry file
    contents the agent read, and this repo does not commit prompts.
    """
    if not client_log:
        return
    client_log.mkdir(parents=True, exist_ok=True)
    suffix = ".partial" if partial else ""
    out = client_log / f"{name}.stdout{suffix}.jsonl"
    out.write_text(stdout or "")
    if stderr:
        (client_log / f"{name}.stderr{suffix}.log").write_text(stderr)
    result["client_log"] = str(out)
    result["client_log_partial"] = partial


def targets(task):
    """Every symbol a task hollows out, in the order tasks.toml lists them.

    A task names either one target inline (`file` + `symbol`) or several under
    `targets`. The inline form is kept because 398 recorded rows name tasks
    defined that way, and a task name has to keep meaning what it meant.

    Several targets is direction 1 of issue #4: every task in the original suite
    was a single function, so nothing tested whether an agent can hold two
    pieces of a convention in agreement across modules -- which is where real
    changes go wrong.

    Raises KeyError for a task that names neither. A task that removes nothing
    would leave the control check passing, and the run would record
    `control_fails_as_expected: false` for every trial instead of failing here.
    """
    if "targets" in task:
        return task["targets"]
    return [{"file": task["file"], "symbol": task["symbol"]}]


def one_trial(cfg, task, backend_name, backend, trial, workdir, timeout, dry_run,
              versions=None, client="claude", client_log=None, solutions=None,
              gates=True):
    repo = pathlib.Path(cfg["repo"]).expanduser()
    suffix = "" if client == "claude" else f"-{client}"
    name = f"{task['name']}-{backend_name}{suffix}-{trial}"
    worktree = workdir / name
    # results.new_row is the only place a row is shaped. It stamps the schema
    # version and sets both exclusion keys explicitly -- see results.py for why
    # "absent" must never be allowed to mean "not excluded".
    result = results.new_row(
        task=task["name"], backend=backend_name, client=client, trial=trial,
        model=backend["model"], context_tokens=backend["context_tokens"],
        effort=backend.get("effort"), env=versions or {},
    )

    # A previous run killed mid-flight leaves its directory behind. Clear it so
    # one aborted run cannot block every later attempt at the same cell.
    if worktree.exists():
        logger.warning("%s: removing stale checkout from an aborted run", name)
        shutil.rmtree(worktree, ignore_errors=True)

    build_checkout(repo, cfg["base_commit"], worktree)
    try:
        # 1. Hollow out the target, then make it the repository's only commit.
        #    Committing the excised state as the initial commit means the
        #    original body exists nowhere in this checkout -- not in history,
        #    not in an object store shared with anything else.
        keep_doc = task.get("keep_docstring", True)
        result["keep_docstring"] = keep_doc
        excised = []
        for t in targets(task):
            path = worktree / t["file"]
            body = excise.excise(path, t["symbol"], keep_docstring=keep_doc)
            excised.append((path, t["symbol"], body))
        result["removed_lines"] = sum(len(b.splitlines()) for _, _, b in excised)
        result["removed_symbols"] = [t["symbol"] for t in targets(task)]
        git(["init", "-q", "-b", "main"], worktree)
        git(["add", "-A"], worktree)
        git(["-c", "user.email=bench@local", "-c", "user.name=bench",
             "commit", "-q", "-m",
             f"benchmark: {', '.join(result['removed_symbols'])} removed"], worktree)

        # 2. Control: the tests must fail now, or the task proves nothing.
        ok, summary = pytest_passes(worktree, task["tests"], timeout)
        result["control_fails_as_expected"] = not ok
        # Baseline the quality gates here, on the excised tree. gmail-archive
        # carries 18 mypy errors of its own, so only a delta against this state
        # says anything about what the agent wrote.
        if gates and not dry_run:
            result["gates_before"] = grade.gates(worktree, GATE_TIMEOUT)
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
        # A failed row records that the agent did not fix the code, never why.
        # --client-log keeps the client's own event stream so the next failure
        # is diagnosable instead of only countable. It is opt-in and written
        # outside the repo by default: these transcripts carry file contents
        # the agent read, and this repo does not commit prompts.
        save_transcript(client_log, name, proc.stdout, proc.stderr, result)
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
        # Did the agent leave the sandbox? The source repo should be untouched
        # and on its original commit. An agent that wandered there invalidates
        # both the isolation and, potentially, the excision.
        # Secondary measurements. None of these reaches results.verdict() --
        # the oracle stays the authority; these only describe the solution it
        # accepted or rejected. See grade.py.
        if gates:
            after = grade.gates(worktree, GATE_TIMEOUT)
            result["gates_after"] = after
            result["gates_delta"] = grade.delta(result.get("gates_before") or {}, after)
        # True only if every hollowed-out symbol came back unchanged; None if
        # any of them is unreadable. A partial match is not recall.
        result["restored_verbatim"] = grade.all_restored_verbatim(excised, keep_doc)
        result["source_repo_intact"] = source_repo_intact(repo, cfg["base_commit"])
        if not result["source_repo_intact"]:
            logger.error("%s: SOURCE REPO WAS MODIFIED -- agent left the sandbox", name)
        logger.info("%s: %s in %ss (%s)", name,
                    "PASS" if passed else "FAIL", result.get("wall_seconds"), summary)
    except subprocess.TimeoutExpired as exc:
        result["error"] = "timeout"
        # The killed process still has whatever it emitted before the deadline,
        # and a timeout is the row you most want to read: it records only that
        # the agent ran out of clock, never what it was doing with it. Decode
        # defensively -- these are bytes on some platforms and may be cut
        # mid-character.
        def _text(v):
            if v is None:
                return ""
            return v if isinstance(v, str) else v.decode("utf-8", "replace")
        save_transcript(client_log, name, _text(exc.stdout), _text(exc.stderr),
                        result, partial=True)
        logger.error("%s: timed out after %ss", name, timeout)
    finally:
        # Before the tree goes. In `finally` on purpose: a timed-out trial has
        # written code too, and that half-finished patch is the most diagnostic
        # artifact a timeout produces.
        if solutions and not dry_run and worktree.exists():
            result.update(grade.save_solution(solutions, name, worktree))
        shutil.rmtree(worktree, ignore_errors=True)
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
    # On by default: a results row records THAT a trial failed, never why.
    # Only 138 of the first 439 rows had a transcript and most of those files
    # are already gone, so the interesting failures were unreconstructable.
    p.add_argument("--client-log", metavar="DIR", default=DEFAULT_CLIENT_LOG,
                   help="keep each trial's raw client event stream in DIR "
                        f"(default: {DEFAULT_CLIENT_LOG}). Transcripts include "
                        "file contents the agent read, so this points outside "
                        "the repo and is never committed.")
    p.add_argument("--no-client-log", action="store_true",
                   help="disable transcript capture entirely.")
    # The worktree used to be deleted with the solution still in it, so no
    # trial before 2026-08-28 has one. That is why #4 could never ask which
    # model writes better code -- the evidence was thrown away 398 times.
    p.add_argument("--solutions", metavar="DIR", default=DEFAULT_SOLUTIONS,
                   help="keep each trial's diff in DIR "
                        f"(default: {DEFAULT_SOLUTIONS}). Patches carry "
                        "repository content, so this points outside the repo "
                        "and is never committed.")
    p.add_argument("--no-solutions", action="store_true",
                   help="disable solution capture entirely.")
    p.add_argument("--no-gates", action="store_true",
                   help="skip ruff and mypy. They are measurements only and "
                        "never change a verdict, but they cost a few seconds "
                        "per trial.")
    p.add_argument("--client", action="append", choices=sorted(CLIENTS),
                   help="repeatable; default claude. Multiple clients are "
                        "interleaved per task so neither gets a systematically "
                        "warmer or colder server than the other.")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = tomllib.loads(pathlib.Path(args.tasks_file).read_text())
    tasks = [t for t in cfg["task"] if not args.task or t["name"] in args.task]
    backends = {k: v for k, v in cfg["backend"].items()
                if not args.backend or k in args.backend}
    if not tasks or not backends:
        raise SystemExit("no tasks or no backends selected")

    # Pre-flight: the reference repo must be clean and hold the pinned commit
    # before anything runs. A dirty tree means a previous run leaked into it,
    # and every trial after that would be exported from contaminated state --
    # which is exactly how 2026-08-17 went unnoticed. Refuse rather than
    # produce rows nobody can trust.
    repo = pathlib.Path(cfg["repo"]).expanduser()
    dirty = run(["git", "status", "--porcelain"], cwd=repo).stdout.strip()
    if dirty:
        raise SystemExit(
            f"reference repo {repo} is dirty -- refusing to run.\n"
            f"{dirty}\n"
            "Commit, stash or discard these changes first. A benchmark that "
            "starts from an unknown state measures nothing.")
    if run(["git", "cat-file", "-e", f"{cfg['base_commit']}^{{commit}}"],
           cwd=repo).returncode != 0:
        raise SystemExit(
            f"base_commit {cfg['base_commit']} not found in {repo}")
    logger.info("reference repo clean, base_commit %s present", cfg["base_commit"])

    workdir = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "agent-bench"
    workdir.mkdir(parents=True, exist_ok=True)

    clients = args.client or ["claude"]
    client_log = (None if args.no_client_log
                  else pathlib.Path(args.client_log).expanduser())
    solutions = (None if args.no_solutions
                 else pathlib.Path(args.solutions).expanduser())

    # What else is on this machine, and what is it holding? A server left up
    # from an earlier session contends for memory and bandwidth for the whole
    # batch, and the result is a timing measurement of a machine that was busy
    # doing something else. Advisory: it warns and never refuses.
    preflight.log_report(preflight.inspect(backends))

    versions = capture_versions(cfg, backends)
    versions["client"] = ",".join(clients)
    logger.info("%d task(s) x %d backend(s) x %d client(s) x %d trial(s)",
                len(tasks), len(backends), len(clients), args.trials)
    logger.info("stack: %s", ", ".join(f"{k}={v}" for k, v in sorted(versions.items())))
    for trial in range(1, args.trials + 1):
        for task in tasks:
            for bname, backend in backends.items():
                # Clients innermost: the same task runs back to back on each,
                # so server state drifts across the pair rather than between
                # two runs hours apart.
                for client in clients:
                    r = one_trial(cfg, task, bname, backend, trial,
                                  workdir, args.timeout, args.dry_run, versions,
                                  client=client, client_log=client_log,
                                  solutions=solutions, gates=not args.no_gates)
                    # Inside the client loop. Outside it, only the last
                    # client's row survives and half the run vanishes.
                    # write_row validates, stamps schema_valid/schema_errors
                    # and appends. A row that violates the schema is still
                    # written -- a trial costs up to half an hour and losing one
                    # to a schema bug is worse than storing a flagged row.
                    r["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    results.write_row(r, RESULTS)


if __name__ == "__main__":
    main()
