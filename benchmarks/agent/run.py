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
import atexit
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request

import excise
import grade
import preflight
import results
import smoke
import swift_excise

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
        cmd,
        cwd=cwd,
        env=env,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def git(args, cwd):
    r = run(["git", *args], cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


# Which excision implementation reads which language. Dispatching on the file
# extension rather than guessing: handing a .swift file to the Python `ast`
# parser does not crash, it finds nothing -- and a task that excises nothing
# leaves the control check passing, which records a broken task as a valid one.
EXCISERS = {".py": excise, ".swift": swift_excise}


def exciser_for(path):
    """The excision module for a source file, by extension."""
    suffix = pathlib.PurePath(path).suffix
    module = EXCISERS.get(suffix)
    if module is None:
        raise ValueError(f"no excision support for {suffix!r} ({path})")
    return module.excise


def task_target(cfg, task):
    """Where this task's repo, commit and test command come from.

    A task may name its own; otherwise it inherits the file-level defaults.
    Inheritance matters: 558 recorded rows name tasks defined before this
    existed, and a task name has to keep meaning what it meant.
    """
    return {
        "repo": task.get("repo", cfg["repo"]),
        "base_commit": task.get("base_commit", cfg["base_commit"]),
        "test_command": task.get(
            "test_command", cfg.get("test_command", "uv run pytest -q")
        ),
    }


def tests_pass(worktree, tests, timeout, command="uv run pytest -q"):
    """Run the oracle. Returns (passed, summary_line).

    `command` is per-repo: `uv run pytest -q` for Python, `swift test` for a
    SwiftPM package. Test node ids are appended for pytest; a runner that does
    not take them gets none, which is why `tests` may be empty.
    """
    r = run([*command.split(), *tests], cwd=worktree, timeout=timeout)
    return r.returncode == 0, summarise_run(r.stdout, r.stderr)


def summarise_run(stdout, stderr):
    """One line describing a test run, preferring stdout but falling back.

    `swift test` writes compile errors to **stderr** and leaves stdout empty, so
    reading only stdout reported `"no output"` for the first Swift failure --
    true, useless, and indistinguishable from a harness fault. The agent had
    written code that did not compile, which is a real and diagnosable failure.
    """
    for stream in (stdout, stderr):
        lines = [ln for ln in (stream or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1][:300]
    return "no output"


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
        "sampling_source": "modelfile" if sampling else "engine defaults (unrecorded)",
    }


def probe_ollama(backend):
    """Ask Ollama what sampler a model declares. Returns {} on any failure."""
    url = backend.get("base_url")
    if not url:
        return {}
    body = json.dumps({"model": backend["model"]}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/api/show",
        body,
        {"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as fh:
            return parse_ollama_show(json.load(fh))
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
        KeyError,
    ) as exc:
        logger.debug("no /api/show from %s: %s", url, exc)
        return {}


# What ds4 says its sampler is: nothing. `/v1/models` lists the parameters it
# *accepts* and there is no endpoint for the values in force. The source has two
# conflicting sets -- ds4.h defines TOP_P 1.0 / MIN_P 0.05, while ds4_cli.c:520
# overrides to top_p 0.95 / min_p 0.0 for CLI and agent paths -- and which one
# reaches the server cannot be settled by reading it. See #37.
# Trees an agent must never be working in. ~/bench-solutions accumulates one
# complete correct patch per trial, and this repo's tracked results.jsonl
# records their absolute paths -- so grepping either can hand the agent the
# answer. A trial that touched one is confounded (#54).
ANSWER_TREES = {"bench-solutions", "local-llm"}

DS4_SAMPLER_NOTE = "engine defaults (not reported by ds4)"


def parse_ds4_models(models):
    """Read what ds4's `/v1/models` can tell us about sampling. Which is: not much.

    Records `accepts_sampling` (the parameters the API takes) and an explicit
    note that the effective values are unreported. That distinction is the
    point: #28 and #36 both came from a sampler nobody wrote down, and an
    explicit unknown is a warning where silence is not.
    """
    data = (models or {}).get("data") or []
    entry = next(
        (
            d
            for d in data
            if "ds4" in str(d.get("id", ""))
            or "deepseek" in str(d.get("id", "")).lower()
        ),
        None,
    )
    if entry is None:
        return {}
    got = {"sampling": {}, "sampling_source": DS4_SAMPLER_NOTE}
    if entry.get("supported_parameters"):
        got["accepts_sampling"] = list(entry["supported_parameters"])
    if entry.get("context_length"):
        got["context_length"] = entry["context_length"]
    return got


def probe_ds4(backend):
    """Ask ds4 what it is serving. Returns {} on any failure."""
    url = backend.get("base_url")
    if not url:
        return {}
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {backend.get('auth_token', '')}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as fh:
            return parse_ds4_models(json.load(fh))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("no /v1/models from %s: %s", url, exc)
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
    sampling = {
        k: params[k]
        for k in ("temperature", "top_p", "top_k", "min_p", "seed", "samplers")
        if k in params
    }
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
                "%Y-%m-%dT%H:%M:%S", time.localtime(server.stat().st_mtime)
            )

    # llama.cpp is a source build off a pull request, not a released tag, so
    # "llama.cpp" alone would not identify it. Pin the commit the way ds4_head
    # pins ds4. There is no digest to record for a GGUF -- hashing 79 GB per
    # run is not free -- so the file's name, size and mtime stand in for one.
    # :11500 is the shim that fronts :8020 for Claude Code; either port means
    # this stack is in the run.
    if any(
        (b.get("base_url") or "").endswith((":8020", ":11500"))
        for b in backends.values()
    ):
        lcpp = pathlib.Path(
            os.environ.get("LLAMACPP_ROOT", "~/git/llama.cpp")
        ).expanduser()
        if (lcpp / ".git").exists():
            try:
                env["llamacpp_head"] = git(["rev-parse", "--short", "HEAD"], lcpp)
                env["llamacpp_dirty"] = bool(git(["status", "--porcelain"], lcpp))
            except RuntimeError:
                pass
        server = lcpp / "build" / "bin" / "llama-server"
        if server.exists():
            env["llamacpp_server_mtime"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(server.stat().st_mtime)
            )
    # Which GGUF is in service comes from the server itself, below. An earlier
    # revision globbed `GGUF_ROOT/*/*.gguf`, which spans every quant sitting in
    # that directory: rows recorded during the Q3 runs list the Q2 shards too,
    # and `gguf_bytes` sums both quants into a number that describes neither.
    # Those rows are still in results.jsonl; read `servers` instead.

    # One probe per backend, keyed by name, because a run can span several and
    # each row records which one it used.
    servers = {
        name: (probe_server(b) or probe_ollama(b) or probe_ds4(b))
        for name, b in backends.items()
    }
    servers = {k: v for k, v in servers.items() if v}
    if servers:
        env["servers"] = servers
    return {k: v for k, v in env.items() if v is not None}


# Shell state that must not reach a trial. VIRTUAL_ENV is the one that has
# actually caused trouble: a trial log shows the agent reading
# "warning: VIRTUAL_ENV=... does not match the project environment path .venv
# and will be ignored" in its own tool output, because the harness passed the
# launching shell's environment through wholesale. A benchmark whose result
# depends on which shell started it is not reproducible.
LEAKY_ENV = (
    "VIRTUAL_ENV",
    "PYTHONHOME",
    "PYTHONPATH",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "UV_PROJECT_ENVIRONMENT",
)


def agent_env(backend):
    env = dict(os.environ)
    for key in LEAKY_ENV:
        env.pop(key, None)

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
    argv = [
        "claude",
        "-p",
        task["prompt"],
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
    ]
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
            f"backend {backend['model']!r} has no opencode_model in tasks.toml"
        )
    return [
        "opencode",
        "run",
        "--model",
        model,
        "--format",
        "json",
        "--auto",
        task["prompt"],
    ]


def aider_argv(task, backend):
    """Aider, one-shot and headless (#61).

    Flags chosen deliberately:

    `--yes-always` is required for unattended operation and is also the thing
    that removes Aider's own confinement. `allowed_to_edit` guards edits with
    `io.confirm_ask` prompts rather than a repo-root check, so with this flag an
    absolute path outside the checkout would be edited without objection. That
    is the same structural weakness as OpenCode's `external_directory: ask`
    (#54), and the reason the sandbox sits below the client rather than being
    left to it. Aider *does* guard user-initiated `/add` -- there is a test for
    it citing Aider-AI/aider#178 -- but that is a different code path.

    `--no-gitignore` because Aider appends `.aider*` to `.gitignore` on startup.
    Our trial checkout is a fresh repo whose only commit is the excised state,
    so that write would dirty the tree and land in `solution_patch`.

    `--no-auto-commits` so the diff we grade is the working tree, the same
    surface every other client is graded on. Aider would otherwise commit its
    own edits, which is exactly the property that makes a silent no-op hard --
    but it would also make our patch capture inconsistent between clients.

    `--no-analytics` and `--no-check-update` keep the run offline and quiet.
    """
    model = backend.get("aider_model")
    if not model:
        raise SystemExit(
            f"backend {backend['model']!r} has no aider_model in tasks.toml"
        )
    argv = [
        "aider",
        "--model",
        model,
        "--message",
        task["prompt"],
        "--yes-always",
        "--no-auto-commits",
        "--no-gitignore",
        "--no-analytics",
        "--no-check-update",
        "--no-show-model-warnings",
        # Aider writes .aider.chat.history.md, .aider.input.history and
        # .aider.tags.cache.v4/ into the working directory. Left there they
        # land in `solution_patch` and in every guard that reads
        # `git status --porcelain`, so they go to the system temp dir instead.
        # --no-gitignore above stops the alternative, which is Aider editing
        # the repo's .gitignore on startup.
        "--chat-history-file",
        str(pathlib.Path(tempfile.gettempdir()) / "aider-chat-history.md"),
        "--input-history-file",
        str(pathlib.Path(tempfile.gettempdir()) / "aider-input-history"),
    ]
    base = backend.get("base_url")
    if base:
        argv[1:1] = [
            "--openai-api-base",
            base.rstrip("/") + "/v1",
            "--openai-api-key",
            backend.get("auth_token", "local"),
        ]
    return argv


def aider_parse(stdout):
    """Aider prints a human report, not an event stream.

    There is no machine-readable output mode, but it does print a token line
    per exchange:

        Tokens: 807 sent, 162 received.

    Sum those. Sent tokens are *summed* rather than peaked because Aider makes
    one exchange per message by default, unlike OpenCode's multi-step loop
    where summing would count the same prompt many times (see opencode_parse).

    Anything not printed is left absent. `results.new_row` treats absent as
    absent, never as zero (#29).
    """
    sent = received = turns = 0
    for m in re.finditer(
        r"Tokens:\s*([\d.]+)([kM]?)\s*sent,\s*([\d.]+)([kM]?)\s*received", stdout or ""
    ):
        scale = {"": 1, "k": 1_000, "M": 1_000_000}
        sent += int(float(m.group(1)) * scale[m.group(2)])
        received += int(float(m.group(3)) * scale[m.group(4)])
        turns += 1
    out = {}
    if turns:
        out = {"input_tokens": sent, "output_tokens": received, "num_turns": turns}
    return out


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
            f"backend {backend['model']!r} has no codex_profile in tasks.toml"
        )
    # `--ephemeral` keeps session rollout files off disk, which matters when a
    # matrix writes hundreds of them. `workspace-write` is the least permission
    # that lets the agent edit the checkout it was given.
    return [
        "codex",
        "exec",
        "--profile",
        profile,
        "--json",
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        task["prompt"],
    ]


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
    "aider": (aider_argv, aider_parse),
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
    return not dirty and head.startswith(commit[: len(head)][:7])


STASH_MARKER = pathlib.Path.home() / ".local-llm-bench-stash.json"


def stash_targets(pairs):
    """Move each real repository aside and stand the export in its place.

    #54: a model asked to implement `src/gmail_archive/parser.py` guesses that
    the repo lives at `~/git/gmail-archive`, and it is right. Under the old
    layout that guess reached an *un-excised* copy: tests green, nothing to fix,
    agent writes nothing, row looks like a model failure.

    Fighting the guess did not work -- OpenCode cannot be confined by its own
    config (anomalyco/opencode#41067). So satisfy it instead. The path the model
    guesses now holds the export: the right files, already excised, with no
    `.git` history the original body was ever in.

    The real checkout moves to `<name>-real` for the duration. A marker records
    the move so a run killed mid-batch is recoverable -- `restore_targets()`
    runs from preflight as well as from here.

    Returns [(export_path, source_path)] for the caller to materialise into.
    """
    if STASH_MARKER.exists():
        raise SystemExit(
            f"{STASH_MARKER} exists: a previous run left repositories moved "
            "aside. Run preflight.py to restore them before starting."
        )
    moved = []
    for repo, _commit in pairs:
        repo = pathlib.Path(repo).expanduser()
        real = repo.with_name(repo.name + "-real")
        if real.exists():
            raise SystemExit(f"{real} already exists; refusing to overwrite it.")
        repo.rename(real)
        moved.append({"export": str(repo), "real": str(real)})
        logger.info("stashed %s -> %s", repo.name, real.name)
    STASH_MARKER.write_text(json.dumps({"moved": moved, "pid": os.getpid()}, indent=1))
    return [(pathlib.Path(m["export"]), pathlib.Path(m["real"])) for m in moved]


def restore_targets():
    """Put the real repositories back. Safe to call when nothing is stashed."""
    if not STASH_MARKER.exists():
        return []
    state = json.loads(STASH_MARKER.read_text())
    restored = []
    for m in state.get("moved", []):
        export, real = pathlib.Path(m["export"]), pathlib.Path(m["real"])
        if not real.exists():
            logger.error("%s is missing; cannot restore %s", real, export)
            continue
        if export.exists():
            shutil.rmtree(export, ignore_errors=True)
        real.rename(export)
        restored.append(export.name)
        logger.info("restored %s", export.name)
    STASH_MARKER.unlink(missing_ok=True)
    return restored


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
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)


def paths_outside(stdout, worktree):
    """Absolute paths the client touched that are not inside the trial checkout.

    #54: OpenCode was observed grepping, reading and running pytest in the
    operator's REAL repository -- which is not excised, so its tests pass and
    the agent correctly concludes there is nothing to do. It then writes
    nothing, and the row looks like a model failure: patch absent, no error,
    and the control's exact test counts.

    The trial checkout is not the problem. run.py exports with `git archive`,
    so the files arrive with no `.git`, and OpenCode respects cwd when run in
    an isolated directory. The model appears to guess a plausible absolute path
    -- `~/git/gmail-archive` is exactly what a prompt naming
    `src/gmail_archive/parser.py` suggests -- and the client executes there
    without confining tools to the workspace.

    Reads cannot be prevented from here, but they can be *recorded*, so a row
    that measured the wrong tree is never mistaken for a model verdict.
    Returns the distinct offending prefixes, most-mentioned first.
    """
    home = str(pathlib.Path.home())
    inside = str(pathlib.Path(worktree).resolve())
    seen: dict[str, int] = {}
    # Match the whole path so the noise filter can see all of it, then report
    # only the two leading segments -- the tree, not every file inside it.
    for match in re.finditer(rf"{re.escape(home)}(?:/[A-Za-z0-9_.-]+)+", stdout or ""):
        full = match.group(0)
        if full.startswith(inside):
            continue
        # Every `uv run` prints its venv and cache; those are not escapes.
        if "/.cache/" in full or "/.venv" in full or "/Library/" in full:
            continue
        parts = full[len(home) + 1 :].split("/")[:2]
        tree = f"{home}/" + "/".join(parts)
        seen[tree] = seen.get(tree, 0) + 1
    return sorted(seen, key=lambda k: -seen[k])


def ensure_pristine(repo, commit):
    """Put a target repository into a known-good state, or refuse to run.

    The operator's requirement, and it is the right one: **a known commit hash
    that is synced with upstream, and no stray files.** A benchmark that starts
    from an unknown state measures nothing, and this project has already had an
    agent delete 33 lines from a checkout it was never pointed at (#54).

    Four things, in order, each of which can refuse:

    1. fetch, so "synced with upstream" is checked against something current
    2. assert the pinned commit is reachable from an `origin/*` ref -- a local
       commit that exists nowhere else is not a baseline anyone can reproduce
    3. `reset --hard` to it, then `clean -ffd`
    4. assert the tree is clean afterwards

    `clean` deliberately omits `-x`: every stray here is an ignored build
    artifact (`.venv`, `.build`, caches), and deleting those would force a full
    recompile per trial while removing nothing an agent could have planted.
    Untracked *source* files are still removed, which is the case that matters.

    Returns the verified sha. Raises on anything it cannot guarantee.
    """
    repo = pathlib.Path(repo).expanduser()
    # Tolerate an offline machine: a stale fetch still lets the
    # containment check below run against what we last saw.
    run(["git", "fetch", "--quiet", "origin"], repo)

    refs = run(["git", "branch", "-r", "--contains", commit], repo).stdout
    upstream = [r.strip() for r in refs.splitlines() if r.strip().startswith("origin/")]
    if not upstream:
        raise SystemExit(
            f"{repo}: {commit} is not reachable from any origin/* ref. "
            "A baseline that exists only on this machine is not reproducible."
        )

    git(["reset", "--hard", "--quiet", commit], repo)
    git(["clean", "-ffd", "--quiet"], repo)

    dirty = run(["git", "status", "--porcelain"], repo).stdout.strip()
    if dirty:
        raise SystemExit(f"{repo}: still dirty after reset+clean:\n{dirty}")

    sha = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
    logger.info(
        "%s: pristine at %s (upstream: %s)", repo.name, sha[:9], ", ".join(upstream[:2])
    )
    return sha


def sandbox_profile(worktree, repo):
    """A macOS sandbox profile that hides every other copy of the answer.

    #54: OpenCode is not confined to its workspace. `opencode run` is headless,
    `external_directory` defaults to `ask`, and with nobody to ask it read the
    operator's real un-excised repository -- saw green tests, concluded nothing
    needed fixing, and wrote nothing. One trial went further and deleted 33
    lines from a working function in a checkout it was never pointed at.

    Configuration cannot fix this on 1.18.25. A `{"*": "deny"}` rule loads and
    orders last, and is still bypassed, because anomalyco/opencode#41067
    submits out-of-worktree paths as `../...` which cannot match. The client's
    own permission layer is the wrong place to enforce this.

    So enforce it below the client. `sandbox-exec` denies the read in the
    kernel, is inherited by every descendant (verified bash -> sh -> cat), and
    does not care what shape the path took.

    Denied: every other checkout of the target repos, plus the directories that
    hold previous trials' solutions and transcripts -- `~/bench-solutions` has
    186 correct patches and this repo's tracked results.jsonl names their paths.

    `allow default` on purpose: the agent still needs its venv, caches, the
    model server and the trial checkout. This hides the answers, nothing else.
    """
    home = pathlib.Path.home()
    keep = str(pathlib.Path(worktree).resolve())
    denied = []
    repo_path = pathlib.Path(repo).expanduser().resolve()
    candidates = [
        home / "git/gmail-archive",
        home / "git/monitor",
        home / "git/local-llm-testing",
        home / "bench-solutions",
        home / "bench-logs",
        # NOT ~/git/local-llm. Denying it kills OpenCode before it starts:
        # it lstat()s the launching process's cwd, which is
        # benchmarks/agent, and dies with EPERM in 0.4s. The pointer leak it
        # would close is already harmless -- results.jsonl names patch paths,
        # but ~/bench-solutions is denied, so those paths cannot be opened.
        # Measured: one trial enumerated 39 of them and read zero (no diff
        # headers, no permission errors). Confinement has to leave the agent
        # able to run.
        repo_path,
        # The stashed real checkout keeps full history, so `git show
        # <commit>:path` there would hand over the original body. Deny it too.
        repo_path.with_name(repo_path.name + "-real"),
        home / "git/gmail-archive-real",
        home / "git/monitor-real",
    ]
    for path in candidates:
        path = str(path)
        if path == keep or keep.startswith(path + "/"):
            continue  # never hide the tree the trial is supposed to edit
        if path not in denied:
            denied.append(path)
    rules = "\n".join(f'(deny file-read* (subpath "{d}"))' for d in denied)
    return f"(version 1)\n(allow default)\n{rules}\n", denied


def sandboxed(argv, worktree, repo, tmpdir):
    """Wrap an agent invocation in the sandbox. Returns argv unchanged if the
    platform has no sandbox-exec, so this degrades to today's behaviour rather
    than silently not running."""
    if not pathlib.Path("/usr/bin/sandbox-exec").exists():
        logger.warning("no sandbox-exec on this platform; agent runs unconfined")
        return argv, []
    profile, denied = sandbox_profile(worktree, repo)
    path = pathlib.Path(tmpdir) / "confine.sb"
    path.write_text(profile)
    return ["/usr/bin/sandbox-exec", "-f", str(path), *argv], denied


def save_transcript(
    client_log, name, stdout, stderr, result, partial=False, worktree=""
):
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
    # #54: record when the agent worked outside the trial checkout. A row that
    # measured the wrong tree is not a model verdict and must not be counted
    # as one.
    escaped = paths_outside(stdout, worktree)
    if escaped:
        result["workspace_escapes"] = escaped
        # An escape into a tree that holds answers is a confound, not a verdict.
        # ~/bench-solutions accumulates a complete correct patch per trial, and
        # this repo's tracked results.jsonl records their absolute paths -- so
        # grepping either one can hand the agent the solution. Every other guard
        # here (touched_tests, source_repo_intact, restored_verbatim) looks
        # inward at the trial checkout and would report clean.
        tainted = [e for e in escaped if ANSWER_TREES.intersection(e.split("/"))]
        if tainted and not result.get("excluded"):
            result["excluded"] = True
            result["exclusion_reason"] = (
                "Answer exposure (#54): agent worked in "
                + ", ".join(tainted)
                + ", which can disclose a previous trial's solution patch. "
                "Not a verdict about the model."
            )


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


def one_trial(
    cfg,
    task,
    backend_name,
    backend,
    trial,
    workdir,
    timeout,
    dry_run,
    versions=None,
    client="claude",
    client_log=None,
    solutions=None,
    gates=True,
    sandbox=True,
):
    target = task_target(cfg, task)
    repo = pathlib.Path(target["repo"]).expanduser()
    suffix = "" if client == "claude" else f"-{client}"
    name = f"{task['name']}-{backend_name}{suffix}-{trial}"
    # #54: while the real checkout is stashed at <name>-real, the export stands
    # in its place, so the path a model guesses holds the excised tree. Trials
    # are serial, so one export at a time is fine.
    stashed_source = repo.with_name(repo.name + "-real")
    worktree = repo if stashed_source.exists() else workdir / name
    # results.new_row is the only place a row is shaped. It stamps the schema
    # version and sets both exclusion keys explicitly -- see results.py for why
    # "absent" must never be allowed to mean "not excluded".
    result = results.new_row(
        task=task["name"],
        backend=backend_name,
        client=client,
        trial=trial,
        model=backend["model"],
        context_tokens=backend["context_tokens"],
        effort=backend.get("effort"),
        env=versions or {},
    )

    # A previous run killed mid-flight leaves its directory behind. Clear it so
    # one aborted run cannot block every later attempt at the same cell.
    if worktree.exists():
        logger.warning("%s: removing stale checkout from an aborted run", name)
        shutil.rmtree(worktree, ignore_errors=True)

    # The export is materialised FROM the stashed real checkout, INTO the path
    # the model guesses. `source` differs from `repo` only while stashed.
    build_checkout(
        stashed_source if stashed_source.exists() else repo,
        target["base_commit"],
        worktree,
    )
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
            body = exciser_for(t["file"])(path, t["symbol"], keep_docstring=keep_doc)
            excised.append((path, t["symbol"], body))
        result["removed_lines"] = sum(len(b.splitlines()) for _, _, b in excised)
        result["removed_symbols"] = [t["symbol"] for t in targets(task)]
        git(["init", "-q", "-b", "main"], worktree)
        git(["add", "-A"], worktree)
        git(
            [
                "-c",
                "user.email=bench@local",
                "-c",
                "user.name=bench",
                "commit",
                "-q",
                "-m",
                f"benchmark: {', '.join(result['removed_symbols'])} removed",
            ],
            worktree,
        )

        # 2. Control: the tests must fail now, or the task proves nothing.
        ok, summary = tests_pass(
            worktree, task["tests"], timeout, target["test_command"]
        )
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
        # #54: confine the agent below the client. Its own permission layer
        # cannot do this (anomalyco/opencode#41067 submits out-of-worktree
        # paths as `../...`, which no pattern matches), so the kernel does.
        # sandbox=False only for the integration fixtures, whose stub agent
        # "solves" a task by copying from the un-excised reference -- the very
        # shortcut this confinement exists to stop.
        argv, denied = (
            sandboxed(
                build_argv(task, backend), worktree, target["repo"], worktree.parent
            )
            if sandbox
            else (build_argv(task, backend), [])
        )
        if denied:
            result["sandbox_denied"] = denied
        proc = run(
            argv,
            cwd=worktree,
            env=agent_env(backend),
            timeout=timeout,
        )
        result["wall_seconds"] = round(time.monotonic() - t0, 1)
        # A failed row records that the agent did not fix the code, never why.
        # --client-log keeps the client's own event stream so the next failure
        # is diagnosable instead of only countable. It is opt-in and written
        # outside the repo by default: these transcripts carry file contents
        # the agent read, and this repo does not commit prompts.
        save_transcript(
            client_log, name, proc.stdout, proc.stderr, result, worktree=worktree
        )
        try:
            result.update(parse(proc.stdout))
        except json.JSONDecodeError:
            result["agent_error"] = True
            result["stderr_tail"] = proc.stderr[-400:]

        # 4. The oracle.
        passed, summary = tests_pass(
            worktree, task["tests"], timeout, target["test_command"]
        )
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
        result["target_repo"] = target["repo"]
        # #54: while stashed, the real checkout is at <name>-real and the
        # export stands at `repo`. The tripwire has to watch the real one --
        # the export is *supposed* to be modified, that is the trial.
        # #54: while stashed, the real checkout is at <name>-real and the
        # export stands at `repo`. The tripwire has to watch the real one --
        # the export is *supposed* to be modified; that is the trial.
        guarded = repo.with_name(repo.name + "-real")
        result["source_repo_intact"] = source_repo_intact(
            guarded if guarded.exists() else repo, target["base_commit"]
        )
        if not result["source_repo_intact"]:
            logger.error("%s: SOURCE REPO WAS MODIFIED -- agent left the sandbox", name)
        logger.info(
            "%s: %s in %ss (%s)",
            name,
            "PASS" if passed else "FAIL",
            result.get("wall_seconds"),
            summary,
        )
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

        save_transcript(
            client_log, name, _text(exc.stdout), _text(exc.stderr), result, partial=True
        )
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
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="verify each task's control failure, run no agent",
    )
    p.add_argument("--tasks-file", default=str(HERE / "tasks.toml"))
    # On by default: a results row records THAT a trial failed, never why.
    # Only 138 of the first 439 rows had a transcript and most of those files
    # are already gone, so the interesting failures were unreconstructable.
    p.add_argument(
        "--client-log",
        metavar="DIR",
        default=DEFAULT_CLIENT_LOG,
        help="keep each trial's raw client event stream in DIR "
        f"(default: {DEFAULT_CLIENT_LOG}). Transcripts include "
        "file contents the agent read, so this points outside "
        "the repo and is never committed.",
    )
    p.add_argument(
        "--no-client-log",
        action="store_true",
        help="disable transcript capture entirely.",
    )
    # The worktree used to be deleted with the solution still in it, so no
    # trial before 2026-08-28 has one. That is why #4 could never ask which
    # model writes better code -- the evidence was thrown away 398 times.
    p.add_argument(
        "--solutions",
        metavar="DIR",
        default=DEFAULT_SOLUTIONS,
        help="keep each trial's diff in DIR "
        f"(default: {DEFAULT_SOLUTIONS}). Patches carry "
        "repository content, so this points outside the repo "
        "and is never committed.",
    )
    p.add_argument(
        "--no-solutions", action="store_true", help="disable solution capture entirely."
    )
    p.add_argument(
        "--no-gates",
        action="store_true",
        help="skip ruff and mypy. They are measurements only and "
        "never change a verdict, but they cost a few seconds "
        "per trial.",
    )
    p.add_argument(
        "--client",
        action="append",
        choices=sorted(CLIENTS),
        help="repeatable; default claude. Multiple clients are "
        "interleaved per task so neither gets a systematically "
        "warmer or colder server than the other.",
    )
    p.add_argument(
        "--skip-smoke",
        action="store_true",
        help="skip the pre-batch coding gate (#63). The gate makes each backend "
        "write three trivial functions and executes them, which catches a server "
        "that is answering in a degraded mode -- the failure preflight cannot see. "
        "Skip it only when you are deliberately measuring a broken backend.",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    cfg = tomllib.loads(pathlib.Path(args.tasks_file).read_text())
    tasks = [t for t in cfg["task"] if not args.task or t["name"] in args.task]
    backends = {
        k: v for k, v in cfg["backend"].items() if not args.backend or k in args.backend
    }
    if not tasks or not backends:
        raise SystemExit("no tasks or no backends selected")

    # Pre-flight: the reference repo must be clean and hold the pinned commit
    # before anything runs. A dirty tree means a previous run leaked into it,
    # and every trial after that would be exported from contaminated state --
    # which is exactly how 2026-08-17 went unnoticed. Refuse rather than
    # produce rows nobody can trust.
    # Every repo any selected task uses -- not just the file-level default.
    # A second target repo that is dirty, or missing its pinned commit, must
    # stop the run for the same reason the first one does.
    targets = {}
    for t in tasks:
        got = task_target(cfg, t)
        targets[(got["repo"], got["base_commit"])] = got
    for (repo_str, commit), got in targets.items():
        repo = pathlib.Path(repo_str).expanduser()
        dirty_t = run(["git", "status", "--porcelain"], cwd=repo).stdout.strip()
        if dirty_t:
            raise SystemExit(
                f"reference repo {repo} is dirty -- refusing to run.\n{dirty_t}\n"
                "Commit, stash or discard these changes first. A benchmark that "
                "starts from an unknown state measures nothing."
            )
        if (
            run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo).returncode
            != 0
        ):
            raise SystemExit(f"base_commit {commit} not found in {repo}")
        logger.info("target ok: %s @ %s via %r", repo, commit, got["test_command"])

    repo = pathlib.Path(cfg["repo"]).expanduser()
    dirty = run(["git", "status", "--porcelain"], cwd=repo).stdout.strip()
    if dirty:
        raise SystemExit(
            f"reference repo {repo} is dirty -- refusing to run.\n"
            f"{dirty}\n"
            "Commit, stash or discard these changes first. A benchmark that "
            "starts from an unknown state measures nothing."
        )
    if (
        run(
            ["git", "cat-file", "-e", f"{cfg['base_commit']}^{{commit}}"], cwd=repo
        ).returncode
        != 0
    ):
        raise SystemExit(f"base_commit {cfg['base_commit']} not found in {repo}")
    logger.info("reference repo clean, base_commit %s present", cfg["base_commit"])

    workdir = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "agent-bench"
    workdir.mkdir(parents=True, exist_ok=True)

    clients = args.client or ["claude"]
    client_log = (
        None if args.no_client_log else pathlib.Path(args.client_log).expanduser()
    )
    solutions = None if args.no_solutions else pathlib.Path(args.solutions).expanduser()

    # What else is on this machine, and what is it holding? A server left up
    # from an earlier session contends for memory and bandwidth for the whole
    # batch, and the result is a timing measurement of a machine that was busy
    # doing something else. Advisory: it warns and never refuses.
    preflight.log_report(preflight.inspect(backends))

    # #63: preflight proves the machine is ready; this proves the *model* is.
    # A backend can be up, current, and pristine while answering in a degraded
    # mode -- on 2026-08-31 a shim rewrote thinking to `disabled` and a GLM cell
    # produced three empty patches before anyone noticed. Every request returned
    # 200 OK. Three trivial functions, executed rather than eyeballed, catch it
    # in under a minute. Runs before the targets are stashed, so a refusal never
    # leaves a checkout moved aside.
    if not args.skip_smoke:
        for backend_name in sorted(backends):
            smoke.gate(backends[backend_name], backend_name)
    else:
        logger.warning("smoke gate skipped (--skip-smoke): rows are not trustworthy")

    versions = capture_versions(cfg, backends)
    versions["client"] = ",".join(clients)

    # #54: every target at a known commit that exists upstream, with no strays,
    # before a single trial runs. A benchmark that starts from an unknown state
    # measures nothing -- and an agent has already damaged a checkout it was
    # never pointed at.
    pairs = sorted(
        {
            (task_target(cfg, t)["repo"], task_target(cfg, t)["base_commit"])
            for t in tasks
        }
    )
    for repo, commit in pairs:
        ensure_pristine(repo, commit)

    # #54: stand the export where the model expects the repo to be, so a
    # guessed path reaches the excised tree instead of an intact one. The real
    # checkouts move to <name>-real until the batch ends.
    restore_targets()  # in case a previous run died mid-batch
    stash_targets(pairs)
    atexit.register(restore_targets)

    logger.info(
        "%d task(s) x %d backend(s) x %d client(s) x %d trial(s)",
        len(tasks),
        len(backends),
        len(clients),
        args.trials,
    )
    logger.info("stack: %s", ", ".join(f"{k}={v}" for k, v in sorted(versions.items())))
    for trial in range(1, args.trials + 1):
        for task in tasks:
            for bname, backend in backends.items():
                # Clients innermost: the same task runs back to back on each,
                # so server state drifts across the pair rather than between
                # two runs hours apart.
                for client in clients:
                    r = one_trial(
                        cfg,
                        task,
                        bname,
                        backend,
                        trial,
                        workdir,
                        args.timeout,
                        args.dry_run,
                        versions,
                        client=client,
                        client_log=client_log,
                        solutions=solutions,
                        gates=not args.no_gates,
                    )
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
