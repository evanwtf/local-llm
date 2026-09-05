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
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request

import excise
import grade
import memcap
import plausibility
import preflight
import provenance
import results
import smoke
import swift_excise

logger = logging.getLogger("agent-bench")
HERE = pathlib.Path(__file__).parent
RESULTS = results.default_path()
# Outside the repo on purpose: transcripts carry file contents the agent
# read, and this repo does not commit prompts.
DEFAULT_CLIENT_LOG = "~/bench-logs"
DEFAULT_SOLUTIONS = "~/bench-solutions"
# Gates are cheap next to a trial -- ruff is under a second, mypy runs cold in
# a fresh worktree and still finishes in seconds -- but they must never be the
# thing that hangs a run, so they get their own short deadline.
GATE_TIMEOUT = 300
# The oracle's own deadline. A passing excision oracle finishes in about 0.1s
# ("17 passed in 0.09s"), so this is already three orders of magnitude of
# headroom. It previously inherited the agent's --timeout of 1800s, which let a
# runaway test run hold 49 GB for half an hour (#82).
ORACLE_TIMEOUT = 300
# The oracle's memory ceiling. A passing run on these tasks stays well under a
# gigabyte; 8 GiB is generous and would have stopped the 49 GB run in #82 before
# the machine reached swap. A timeout shortens an outage, a cap prevents one.
ORACLE_MEM_CAP_GIB = 8.0


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


def script_checks(worktree, entrypoint, checks, timeout):
    """Oracle for a script task: run the thing and look at what it prints.

    Returns (passed, summary), matching tests_pass() so both kinds of task
    share a call site.

    This is a different measurement from the excision tasks. There, the agent
    is handed a repository, a failing suite and a function signature, and has
    only to fill in a body. Here it starts with an empty directory and must
    produce a runnable artifact: the right filename, an argv read, and output
    on stdout. Trivial logic, real boilerplate -- and the boilerplate is the
    part that is never tested when scaffolding already exists.

    Two properties are recorded separately, because conflating them would
    measure formatting compliance as if it were capability:

      * `passed` compares stripped stdout, so a missing trailing newline does
        not fail an otherwise correct script.
      * `exact` notes whether the output matched byte for byte, newline
        included, since the prompt does ask for it.

    The checked inputs are NOT the input shown in the prompt. A script that
    hardcodes the demonstrated case fails here, the same rule the smoke probes
    follow.

    A check's first element is either a single argument or a list of them, so a
    task with flags (`script-transform`) uses the same oracle as one with a
    bare positional (`script-reverse`). It is never shell-interpreted: argv is
    passed as a list, so an input containing a space is one argument.
    """
    script = pathlib.Path(worktree) / entrypoint
    if not script.exists():
        return False, f"{entrypoint} was never created"

    failures = []
    exact = True
    for arg, want in checks:
        argv = [arg] if isinstance(arg, str) else list(arg)
        try:
            proc = subprocess.run(
                ["python3", entrypoint, *argv],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"{entrypoint} {' '.join(argv)!r} timed out"
        got = proc.stdout
        if got.strip() != want:
            detail = (
                (got.strip() or proc.stderr.strip().splitlines()[-1:] or [""])[0]
                if not got.strip()
                else got.strip()
            )
            shown = " ".join(argv)
            failures.append(f"{shown!r} -> {detail[:60]!r} (want {want!r})")
        elif got != want + "\n":
            exact = False

    if failures:
        return False, f"{len(failures)}/{len(checks)} failed: " + "; ".join(
            failures[:2]
        )
    note = "" if exact else " (output not exactly one trailing newline)"
    return True, f"{len(checks)}/{len(checks)} checks passed{note}"


def peak_child_rss_gib() -> float:
    """Peak RSS of any child reaped so far, in GiB.

    Cheap, and it makes a whole failure class visible as data. A correct-but-
    pathological implementation -- one that buffers a file the task says to
    stream -- is invisible to a binary oracle and obvious here. `gemma426`
    wrote a `scan` whose test run reached 49 GB and drove the machine into
    swap; nothing recorded that except the operator noticing (#82).

    ru_maxrss is bytes on macOS and kilobytes on Linux.
    """
    raw = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    divisor = 1024**3 if sys.platform == "darwin" else 1024**2
    return round(raw / divisor, 2)


def tests_pass(worktree, tests, timeout, command="uv run pytest -q"):
    """Run the oracle. Returns (passed, summary_line, killed_for_memory).

    `command` is per-repo: `uv run pytest -q` for Python, `swift test` for a
    SwiftPM package. Test node ids are appended for pytest; a runner that does
    not take them gets none, which is why `tests` may be empty.

    `timeout` is the ORACLE's deadline, not the agent's. It used to be handed
    the agent's `--timeout` -- 1800s by default -- for a step that takes about
    0.1s when it passes: four orders of magnitude of slack, chosen by nobody.
    Script tasks already used GATE_TIMEOUT; only this path did not (#82).

    The third return is item 4 of #82. A memcap kill means the code may well be
    correct and is certainly not runnable; without carrying that flag up, the
    caller sees passed=False and cannot tell it apart from "the model wrote
    wrong code". The row is then excluded rather than pooled with real
    failures. Matching the summary string here would be a fragile substitute
    that any refactor could quietly break.
    """
    r, peak, killed = memcap.run_capped(
        [*command.split(), *tests],
        cwd=worktree,
        timeout=timeout,
        cap_gib=ORACLE_MEM_CAP_GIB,
    )
    if killed:
        return (
            False,
            f"oracle memory-killed at {peak:.1f} GiB (cap {ORACLE_MEM_CAP_GIB:.1f} GiB)",
            True,
        )
    return r.returncode == 0, summarise_run(r.stdout, r.stderr), False


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


# ollama#16471 shipped in 0.33.3-rc0 and changed sampler precedence so that
# model-authored defaults (GGUF KVs, MLX generation_config.json) beat Ollama's
# own built-ins. See #84. The boundary is pinned by a test using these literals.
OLLAMA_MODEL_DEFAULTS_FROM = (0, 33, 3)


def ollama_honors_model_defaults(version):
    """True from 0.33.3, False before it, None when the version is unreadable.

    None rather than False on purpose: guessing "old" would let a row taken
    after the upgrade claim the pre-upgrade sampler, which is the silent
    mislabelling this whole guard exists to prevent.
    """
    if not version:
        return None
    head = str(version).strip().lstrip("v").split("-")[0]
    parts = head.split(".")
    try:
        numbers = tuple(int(part) for part in parts[:3])
    except ValueError:
        return None
    if len(numbers) < 3:
        return None
    return numbers >= OLLAMA_MODEL_DEFAULTS_FROM


def model_declared_sampling(show):
    """The sampler the GGUF itself declares, from /api/show `model_info` (#84).

    Ollama surfaces the model's own KVs here, so `general.sampling.*` is
    readable without opening the file. Keys are returned with the
    `general.sampling.` prefix stripped, so they line up with the modelfile
    PARAMETER names that occupy the same slot at a higher precedence.
    """
    info = show.get("model_info") if isinstance(show, dict) else None
    if not isinstance(info, dict):
        return {}
    prefix = "general.sampling."
    return {k[len(prefix) :]: v for k, v in info.items() if k.startswith(prefix)}


def parse_ollama_show(show, ollama_version=None):
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
    if sampling:
        # Precedence 2 still wins after ollama#16471, so these rows keep their
        # meaning across the upgrade.
        return {"sampling": sampling, "sampling_source": "modelfile"}

    # No modelfile parameters: the engine decides, and WHICH engine rule applies
    # changed in 0.33.3. Naming the regime is not the same as reading the
    # resolved values -- it stays "unrecorded" either way -- but it stops one
    # string describing two different samplers.
    honors = ollama_honors_model_defaults(ollama_version)
    # #84's remaining half. /api/show returns `model_info`, which carries the
    # GGUF's own KVs -- including general.sampling.* when the model declares
    # them. So the resolved sampler is readable from the response we already
    # fetch: no GGUF path, no separate header read. NEXT.md had this down as
    # "wire gguf_meta.py into probe_ollama()", which is not needed.
    declared = model_declared_sampling(show)
    if honors is None:
        regime = "engine defaults (unrecorded; ollama version unknown)"
    elif honors:
        if declared:
            # These are the numbers actually in force from 0.33.3 on.
            return {
                "sampling": declared,
                "sampling_source": (
                    "model-authored GGUF defaults, resolved from /api/show "
                    "model_info (ollama >= 0.33.3)"
                ),
            }
        # Absent model_info and empty model_info are different facts. Absent
        # means we could not see what the model declares -- an older ollama, a
        # truncated response -- so the regime is all we can name. Empty means
        # we looked and it declares nothing, so the built-ins apply and saying
        # so is more useful than naming the regime.
        elif isinstance(show.get("model_info"), dict):
            regime = (
                "engine defaults (unrecorded; model declares no sampler, so "
                "ollama built-ins apply even at >= 0.33.3)"
            )
        else:
            regime = (
                "engine defaults (unrecorded; model-authored GGUF/"
                "generation_config defaults honored, ollama >= 0.33.3; "
                "model_info absent so the values could not be read)"
            )
    else:
        # Pre-0.33.3 the built-ins win, so what the model declares is NOT what
        # ran. Recording it anyway would be a lie about this row; naming it as
        # overridden is the useful half, because it says what the same row
        # would get after an upgrade.
        regime = (
            "engine defaults (unrecorded; ollama built-in defaults, ollama <= 0.33.2)"
        )
        if declared:
            regime += f" -- model declares {declared}, overridden at this version"
    return {"sampling": sampling, "sampling_source": regime}


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
            # The installed version decides which sampler precedence applies,
            # so the row records the regime rather than a bare "engine
            # defaults" that means two different things either side of 0.33.3.
            return parse_ollama_show(
                json.load(fh),
                ollama_version=provenance.engine_versions().get("ollama"),
            )
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


def parse_openai_models(models, backend=None):
    """Read what an OpenAI-compatible `/v1/models` can tell us about a backend.

    Every server we run except Ollama answers this endpoint -- ds4, LM Studio,
    MTPLX -- so one parser covers them. It used to match the entry whose id
    contained "ds4" or "deepseek", which silently returned {} for anything
    else: GLM-5.3 is served by ds4 under the id `glm-5.3-flash`, so every
    `glm53ds4` row carries full `ds4_head` provenance and an empty `servers`
    entry, and LM Studio was dropped from `servers` entirely. **A substring
    match against a model name is not a probe.**

    Selection is now, in order: the id the backend declares, then the only
    entry if there is exactly one. Anything else is ambiguous and returns {}
    rather than guessing -- picking the wrong row here would attribute one
    model's context length to another.

    Records `accepts_sampling` (the parameters the API takes) and an explicit
    note that the effective values are unreported. That distinction is the
    point: #28 and #36 both came from a sampler nobody wrote down, and an
    explicit unknown is a warning where silence is not.
    """
    data = (models or {}).get("data") or []
    if not data:
        return {}
    wanted = (backend or {}).get("model")
    entry = next((d for d in data if str(d.get("id", "")) == wanted), None)
    if entry is None and len(data) == 1:
        entry = data[0]
    if entry is None:
        return {}

    got = {"sampling": {}, "sampling_source": DS4_SAMPLER_NOTE}
    if entry.get("id"):
        got["served_model_id"] = entry["id"]
    if entry.get("supported_parameters"):
        got["accepts_sampling"] = list(entry["supported_parameters"])
    if entry.get("context_length"):
        got["context_length"] = entry["context_length"]
    # LM Studio's /v1/models carries the fields that actually identify a build.
    for key in ("quantization", "arch", "publisher", "state", "max_context_length"):
        if entry.get(key) is not None:
            got[key] = entry[key]
    return got


# Kept as the old name so callers and tests that predate the generalisation
# keep working; ds4 is now one of several servers this reads.
def parse_ds4_models(models):
    return parse_openai_models(models)


def probe_openai_models(backend):
    """Ask an OpenAI-compatible server what it is serving. {} on any failure."""
    url = backend.get("base_url")
    if not url:
        return {}
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {backend.get('auth_token', '')}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as fh:
            return parse_openai_models(json.load(fh), backend)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("no /v1/models from %s: %s", url, exc)
        return {}


probe_ds4 = probe_openai_models


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


def serving_ds4_root():
    """The tree the *running* ds4-server was launched from, or None.

    `DS4_ROOT` and the `~/git/ds4` default both describe where ds4 is expected
    to live, not where the server actually came from. On 2026-08-31 the engine
    under test was a worktree at `~/git/ds4-main` (upstream/main `ec7642c`)
    while the default pointed at the fork at `399acbb`, so every row would have
    been stamped with an engine that was not running -- and would later have
    been compared against real `399acbb` rows as if they matched.

    Asking the live process removes the operator from the loop. `ds4-server` is
    usually launched as `./ds4-server`, so argv is not enough; the process cwd
    is what identifies the tree.
    """
    try:
        pids = subprocess.run(
            ["pgrep", "-f", "ds4-server"], capture_output=True, text=True, check=False
        ).stdout.split()
        for pid in pids:
            out = subprocess.run(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            for line in out.splitlines():
                if line.startswith("n"):
                    root = pathlib.Path(line[1:])
                    if (root / ".git").exists():
                        return root
    except Exception:  # noqa: BLE001 - provenance is best-effort, never fatal
        return None
    return None


def serving_gguf(root=None):
    """The weights the running ds4-server actually has open, or None.

    `model` in a row is a server-side alias -- `glm-5.3-flash`, `deepseek-v4-flash`
    -- and ds4 serves whatever GGUF it was started with regardless of the name
    requested. On 2026-08-31 the server advertised `glm-5.2*` aliases while
    holding a GLM-5.3 file. Without the path, a row cannot say which weights
    produced it, and a re-quantised checkpoint at the same filename is
    indistinguishable from the original.

    Returns path, size and mtime. No hash: the file is ~90 GB and hashing it on
    every run is not free. Size plus mtime catches a swapped or re-downloaded
    checkpoint, which is the realistic failure.
    """
    try:
        out = subprocess.run(
            ["ps", "ax", "-o", "command="], capture_output=True, text=True, check=False
        ).stdout
        for line in out.splitlines():
            if "ds4-server" not in line or "-m" not in line:
                continue
            parts = line.split()
            for i, tok in enumerate(parts):
                if tok in ("-m", "--model") and i + 1 < len(parts):
                    gguf = pathlib.Path(parts[i + 1]).expanduser()
                    if gguf.exists():
                        stat = gguf.stat()
                        return {
                            "gguf_path": str(gguf),
                            "gguf_bytes": stat.st_size,
                            "gguf_mtime": time.strftime(
                                "%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)
                            ),
                            "server_argv": " ".join(parts),
                        }
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return None
    return None


def metal_ceiling_mb():
    """The Metal wired limit. Decides whether a ~90 GiB model loads at all.

    Raised with `sysctl iogpu.wired_limit_mb`. Persisted since 2026-09-01 by
    scripts/install-metal-ceiling.sh; before that a reboot reverted it, so two
    runs a reboot apart could differ on whether a model ran with nothing in the
    row to explain it. Recorded regardless -- a persisted setting can still be
    unloaded.
    """
    try:
        out = subprocess.run(
            ["sysctl", "-n", "iogpu.wired_limit_mb"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:  # noqa: BLE001
        return None


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
        # macos/machine kept for continuity with 979 existing rows; the
        # portable facts arrive below from preflight.machine_facts().
        "macos": out(["sw_vers", "-productVersion"]),
        "machine": out(["sysctl", "-n", "machdep.cpu.brand_string"]),
        "target_commit": cfg["base_commit"],
        # aider is a client like the others; it was the only one not recorded.
        "aider": out(["aider", "--version"]),
        # #84: ollama 0.33.3 changed which sampler a model gets. 343 of 1394
        # rows carry this and the rest do not, so "which ollama" is already
        # unanswerable for most of the corpus. The regime string on the row
        # names the precedence rule; this names the build that applied it,
        # which is what a release note is looked up by.
        "ollama": out(["ollama", "--version"]),
    }

    # The harness itself: which run.py produced this row. A row that cannot name
    # its own code cannot be re-derived once the code moves on.
    try:
        env["harness_head"] = git(["rev-parse", "--short", "HEAD"], HERE)
        env["harness_dirty"] = bool(
            git(["status", "--porcelain", "--untracked-files=no"], HERE)
        )
    except RuntimeError:
        pass

    # Decides whether a ~90 GiB model loads at all. Persisted since 2026-09-01.
    ceiling = metal_ceiling_mb()
    if ceiling:
        env["metal_ceiling_mb"] = ceiling

    # Interrogate the machine on every run rather than assuming last time's.
    # `macos` and `machine` above are Darwin sysctls and were simply absent on
    # Linux, so a desktop row could not say what hardware produced it. Includes
    # `confinement`, because sandbox-exec is macOS-only and a Linux row's
    # workspace_escapes gate is unenforced -- without it the two rows look
    # identical in results.jsonl while meaning different things (#81).
    env.update(preflight.machine_facts())

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
        ds4_root = (
            serving_ds4_root()
            or pathlib.Path(os.environ.get("DS4_ROOT", "~/git/ds4")).expanduser()
        )
        if (ds4_root / ".git").exists():
            try:
                env["ds4_head"] = git(["rev-parse", "--short", "HEAD"], ds4_root)
                # --untracked-files=no: a gguf/ directory of weights is not source drift,
                # and reporting dirty for it trains the reader to ignore the field.
                env["ds4_dirty"] = bool(
                    git(["status", "--porcelain", "--untracked-files=no"], ds4_root)
                )
            except RuntimeError:
                pass
        # #64/#62: the alias in `model` does not identify the weights.
        weights = serving_gguf()
        if weights:
            env.update(weights)

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

    # LM Studio's version lived only in a tasks.toml `description` string
    # ("LM Studio 0.4.23"), which is prose: it goes stale the next time the app
    # updates itself and nothing notices. `lms --version` reports the CLI's
    # commit, not the app build -- an incomplete answer, recorded under a name
    # that says which one it is rather than implying the app version.
    if any((b.get("base_url") or "").endswith(":1234") for b in backends.values()):
        env["lmstudio_cli"] = out(["lms", "--version"])

    # One probe per backend, keyed by name, because a run can span several and
    # each row records which one it used.
    servers = {
        name: (probe_server(b) or probe_ollama(b) or probe_openai_models(b))
        for name, b in backends.items()
    }
    servers = {k: v for k, v in servers.items() if v}

    # #78: every gap in this record arrived the same way -- a backend was added,
    # no probe covered it, and the rows came out unstamped in silence. LM Studio
    # went six backends' worth of comparison with no server identity at all, and
    # GLM-5.3 lost its `servers` entry to a substring match. Say so on the row.
    #
    # A warning, not a refusal: this is provenance, and the surrounding probes
    # are all documented as never taking a trial down. But an explicit absence
    # is a warning where silence is not -- the same reason `sampling_source`
    # records "engine defaults (unrecorded)" rather than omitting the key.
    unstamped = sorted(
        name
        for name, b in backends.items()
        if b.get("base_url") and name not in servers
    )
    if unstamped:
        env["servers_unidentified"] = unstamped
        logger.warning(
            "no server identity for %s -- rows will not name the engine that "
            "served them (#78)",
            ", ".join(unstamped),
        )

    # A hosted backend has no base_url and no build to pin. That is not the same
    # as a local server we failed to probe, and it must not read as one: the
    # hosted model is the reference the task ceilings are set against, so it is
    # the row where an unnoticed change upstream does the most damage.
    hosted = sorted(name for name, b in backends.items() if not b.get("base_url"))
    if hosted:
        env["hosted_unpinned"] = hosted

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


def claude_argv(task, backend, worktree=None):
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


def claude_prompt_tokens(usage):
    """Every input token the server had to process, cached or not.

    #74. Claude Code splits input three ways and `input_tokens` counts only the
    UNCACHED remainder:

        "input_tokens": 0,
        "cache_creation_input_tokens": 26494,
        "cache_read_input_tokens": 26636,

    Reading `input_tokens` alone reported **0** against ds4 for a prompt of
    53,130 tokens. Cache reads are cheaper than fresh prefill but they are not
    free, and they are exactly what #64 is about -- so the sum is the number
    that belongs in a row.

    Returns None when the usage block carries none of the three, because absent
    must never be recorded as zero (#29).
    """
    keys = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    present = [usage.get(k) for k in keys if isinstance(usage.get(k), int)]
    return sum(present) if present else None


def claude_parse(stdout):
    payload = json.loads(stdout)
    usage = payload.get("usage", {})
    return dict(
        num_turns=payload.get("num_turns"),
        stop_reason=payload.get("stop_reason"),
        api_ms=payload.get("duration_api_ms"),
        input_tokens=claude_prompt_tokens(usage),
        uncached_input_tokens=usage.get("input_tokens"),
        cache_read_input_tokens=usage.get("cache_read_input_tokens"),
        output_tokens=usage.get("output_tokens"),
        agent_error=payload.get("is_error"),
    )


def opencode_argv(task, backend, worktree=None):
    model = backend.get("opencode_model")
    if not model:
        raise SystemExit(
            f"backend {backend['model']!r} has no opencode_model in tasks.toml"
        )
    # --dir is REQUIRED, not a nicety. `opencode run` attaches to a persistent
    # server ("path on remote server if attaching"), and that server holds the
    # working directory it was started with -- not the cwd of the invoking
    # process, which run.py sets correctly to the worktree.
    #
    # Without it, OpenCode solved script-reverse and wrote the answer to
    # ~/git/local-llm/benchmarks/agent/reverse.py -- run.py's own directory --
    # and scored 0/3 with "reverse.py was never created". The recovered file
    # passed all three checks. #67.
    # Refuse rather than default. A missing --dir does not fail: the client
    # runs, solves the task, writes the answer somewhere else, and the oracle
    # reports a model failure. That silent mode is what voided 64 trials, so
    # the only safe default is no default.
    if worktree is None:
        raise SystemExit(
            "opencode_argv requires a worktree: without --dir the client writes "
            "to the server's directory and every trial scores as a model failure (#67)"
        )
    return [
        "opencode",
        "run",
        "--dir",
        str(worktree),
        "--model",
        model,
        "--format",
        "json",
        "--auto",
        task["prompt"],
    ]


def aider_argv(task, backend, worktree=None):
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

    Also records per-step TTFT (#96). The transcript stamps every event with a
    millisecond timestamp, and each real step brackets `step_start` -> first
    `text`/`tool_use` -> `step_finish`. The delta from step_start to the first
    content event is the closest thing to per-turn TTFT we can compute from
    this transcript: it is what the agent USER experienced, including OpenCode's
    own serialization overhead. It is not the wire TTFT from ds4's perspective.

    Tool-response acknowledgment steps -- where OpenCode records a tool result
    with no model call -- also produce a step_start/step_finish pair with a
    TTFT of a few milliseconds. Those are filtered by a > 100 ms threshold so
    the recorded median describes real model turns, not stream bookkeeping.
    """
    turns = 0
    out_tokens = 0
    reasoning = 0
    peak_input = None
    step_ttfts_ms: list[int] = []
    current_step_open: int | None = None
    current_step_saw_content = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        ts = event.get("timestamp")
        if etype == "step_start":
            current_step_open = ts
            current_step_saw_content = False
        elif (
            etype in ("text", "tool_use")
            and not current_step_saw_content
            and current_step_open is not None
            and isinstance(ts, int)
        ):
            step_ttfts_ms.append(ts - current_step_open)
            current_step_saw_content = True
        elif etype == "step_finish":
            tokens = event.get("part", {}).get("tokens", {})
            turns += 1
            out_tokens += tokens.get("output") or 0
            reasoning += tokens.get("reasoning") or 0
            if tokens.get("input"):
                peak_input = max(peak_input or 0, tokens["input"])
            current_step_open = None
            current_step_saw_content = False
    if not turns:
        raise json.JSONDecodeError("no step_finish events", stdout[:200], 0)

    row = dict(
        num_turns=turns,
        input_tokens=peak_input,
        output_tokens=out_tokens,
        reasoning_tokens=reasoning,
    )
    # A trial with no timestamps (a very old transcript, or a client that
    # emits none) records no TTFT rather than a bogus zero. Absence is a
    # different signal than "0 ms".
    if step_ttfts_ms:
        real_model_ttfts = [t for t in step_ttfts_ms if t > 100]
        row["step_ttft_ms_median"] = _median_int(step_ttfts_ms)
        row["step_ttft_ms_p90"] = _percentile_int(step_ttfts_ms, 0.90)
        row["num_steps"] = len(step_ttfts_ms)
        row["num_model_steps"] = len(real_model_ttfts)
        if real_model_ttfts:
            row["model_step_ttft_ms_median"] = _median_int(real_model_ttfts)
    return row


def _median_int(values: list[int]) -> int:
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) // 2


def _percentile_int(values: list[int], p: float) -> int:
    """Nearest-rank percentile in the closed interval [min, max].

    Not linear interpolation: with n=1 the p90 must be the single sample, not
    itself; with n=10 the p90 is the 9th-ranked value. Interpolation would
    produce fractional answers for token/millisecond quantities, which are
    integers by construction.
    """
    ordered = sorted(values)
    if not ordered:
        return 0
    k = max(0, min(len(ordered) - 1, round(p * (len(ordered) - 1))))
    return ordered[k]


def codex_argv(task, backend, worktree=None):
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
        # Record the move BEFORE making it. The marker used to be written once,
        # after every rename, which left a window where the repositories were
        # moved and nothing on disk said so: a kill in that window produced
        # stashed repos with no marker, so restore_targets() found nothing to
        # restore and the only trace was the next run refusing to overwrite
        # <name>-real. An error, not a recovery.
        moved.append({"export": str(repo), "real": str(real)})
        _write_marker(moved)
        repo.rename(real)
        logger.info("stashed %s -> %s", repo.name, real.name)
    return [(pathlib.Path(m["export"]), pathlib.Path(m["real"])) for m in moved]


def _write_marker(moved: list[dict]) -> None:
    """Write the stash marker atomically, so a kill never truncates the map."""
    payload = json.dumps({"moved": moved, "pid": os.getpid()}, indent=1)
    tmp = STASH_MARKER.with_suffix(".tmp")
    tmp.write_text(payload)
    os.replace(tmp, STASH_MARKER)


def _stash_owner_alive(pid: object) -> bool:
    """Is the process that stashed these repositories still running?

    Signal 0 checks for existence and never kills. A pid we cannot read is
    treated as dead, because the marker predates the pid being recorded and
    those stashes must stay recoverable.
    """
    if not isinstance(pid, int) or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def restore_targets():
    """Put the real repositories back. Safe to call when nothing is stashed.

    **Refuses when a different, living process owns the stash.** This function
    is destructive -- it `rmtree`s the export standing in the repository's
    place -- and `preflight.py` calls it on every invocation. Running preflight
    during a live batch therefore used to unstash the targets underneath the
    running harness, which then destroyed the real checkout on its next trial
    and left nothing on disk at all.

    That is not hypothetical: it happened on 2026-09-04 at 21:07, mid-run,
    from a preflight invocation whose only purpose was to read the run lock. It
    cost the operator's `~/git/gmail-archive` checkout, recoverable only
    because the harness had logged it pristine at a known commit seconds
    earlier.

    The marker records the stashing pid, so ownership is knowable. Restore
    when the owner is dead -- the crash recovery this exists for -- or when the
    owner is us, which is the `atexit` path. Never otherwise.
    """
    if not STASH_MARKER.exists():
        return []
    state = json.loads(STASH_MARKER.read_text())
    unrestored: list[dict] = []
    owner = state.get("pid")
    if _stash_owner_alive(owner):
        logger.warning(
            "%s is owned by live pid %s -- refusing to restore under a running "
            "batch. Stop that run first; restoring here would destroy the real "
            "checkout it is using.",
            STASH_MARKER,
            owner,
        )
        return []
    restored = []
    for m in state.get("moved", []):
        export, real = pathlib.Path(m["export"]), pathlib.Path(m["real"])
        if not real.exists():
            logger.error("%s is missing; cannot restore %s", real, export)
            unrestored.append(m)
            continue
        if export.exists():
            shutil.rmtree(export, ignore_errors=True)
        real.rename(export)
        restored.append(export.name)
        logger.info("restored %s", export.name)
    if unrestored:
        # The marker is the only map back. Deleting it after a partial restore
        # makes the entries that failed unrecoverable -- and a failed entry is
        # exactly when the map matters. Keep the remainder instead.
        _write_marker(unrestored)
        logger.error(
            "%d repository(ies) could not be restored; the marker keeps them "
            "so a later run can try again",
            len(unrestored),
        )
    else:
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


def prepare_env(dest, timeout=600):
    """Create the checkout's virtualenv before the agent sees it (#4).

    METHODOLOGY section 9: a fresh export has no `.venv`, so part of every
    wall-time number is the agent working out how to run pytest -- installing
    dependencies, guessing at `python -m`, or discovering `uv` for itself.
    That is real agent behaviour, but it is not the thing being compared, and
    it lands in the same number as solving the task.

    Returns what happened, for the row. Never raises: a checkout whose env
    cannot be built is still a runnable trial, and the agent may well sort it
    out -- which is exactly the confound, so the row must say which state it
    started in rather than the harness pretending it is uniform.

    **This starts a new series.** Wall times taken with a prepared env are not
    comparable with the 398 rows taken without one, and `env_prepared` on the
    row is what tells them apart.
    """
    if not (dest / "pyproject.toml").exists():
        return {"env_prepared": False, "env_reason": "no pyproject.toml"}
    try:
        got = subprocess.run(
            ["uv", "sync", "--frozen"],
            cwd=dest,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"env_prepared": False, "env_reason": str(exc)[:120]}
    if got.returncode != 0:
        # --frozen refuses when the lockfile is stale. Say so rather than
        # silently falling back to a resolve, which would install different
        # versions than the lockfile pins and quietly change the environment
        # under the comparison.
        return {
            "env_prepared": False,
            "env_reason": (got.stderr or got.stdout).strip().splitlines()[-1][:160]
            if (got.stderr or got.stdout).strip()
            else f"uv sync exited {got.returncode}",
        }
    return {"env_prepared": True, "env_reason": "uv sync --frozen"}


# Where the clients themselves live. A client naming its own binary in stdout
# is not an escape -- see paths_outside().
CLIENT_INSTALL_RE = re.compile(
    r"/\.local/(?:share|bin)/|/\.nvm/|/\.bun/|/homebrew/|/\.asdf/"
)


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
        # Nor is a client naming its own installation. Aider prints
        # "## Running: ~/.local/share/uv/tools/aider-chat/bin/python -m pytest"
        # whenever it runs the suite, which flagged an escape on every such
        # trial -- 2 of 2 on qwen36coding, against 0 of 30 on backends where it
        # happened not to print that line. A tool invoking its own interpreter
        # is not a workspace escape, and recording it as one would have
        # published "Aider escapes on Ollama backends".
        if CLIENT_INSTALL_RE.search(full):
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

    # `~/git/local-llm` stays readable (see above), but two files inside it are
    # answers. `tasks.toml` carries every prompt AND, for a script task, the
    # exact checks -- "Benchmarking" -> "gnikramhcneB". An agent that reads it
    # has the test.
    #
    # This cost the whole 2026-08-31 OpenCode cell: 9 of 12 rows auto-excluded
    # for answer exposure, leaving nothing citable about the project's own
    # designated primary harness. Denying the two files rather than the tree
    # keeps OpenCode able to start while closing the leak.
    for leak in (HERE / "tasks.toml", RESULTS):
        leak = str(leak.resolve())
        if not keep.startswith(leak) and leak not in denied:
            denied.append(leak)
    rules = "\n".join(
        f'(deny file-read* ({"literal" if pathlib.Path(d).is_file() else "subpath"} "{d}"))'
        for d in denied
    )
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
    body = stdout or ""
    # #112: never overwrite a transcript. The trial name repeats across
    # sweeps, so a second sweep into the same --client-log directory used to
    # destroy the first one's evidence in silence. That is how #112's
    # pre-remedy transcripts were lost: the before-side of the only question
    # that issue asks is gone and cannot be reconstructed, because the six
    # later sweeps wrote these exact filenames.
    #
    # Identical bytes are not new evidence, so a re-write of the same content
    # is a no-op rather than a pile of numbered duplicates.
    collision = False
    if out.exists() and out.read_text() != body:
        collision = True
        index = 2
        while True:
            candidate = client_log / f"{name}.stdout{suffix}.{index}.jsonl"
            if not candidate.exists():
                out = candidate
                break
            if candidate.read_text() == body:
                out = candidate
                break
            index += 1
        logger.warning(
            "%s already holds a different transcript for %s; writing %s "
            "instead. Two sweeps are sharing one --client-log directory, and "
            "the earlier evidence is being kept (#112).",
            client_log,
            name,
            out.name,
        )
    out.write_text(body)
    if stderr:
        stderr_path = client_log / f"{name}.stderr{suffix}.log"
        if collision:
            stderr_path = client_log / f"{out.stem}.log"
        stderr_path.write_text(stderr)
    result["client_log"] = str(out)
    result["client_log_partial"] = partial
    if collision:
        result["client_log_collision"] = True
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


def trial_order(backends, trial):
    """The backends for one trial, in the order they should run (#130).

    Throughput declines across a measurement window, so a fixed order
    penalises whichever backend always runs last. @adamlawi measured that
    bias on antirez/ds4#952 as larger than three of the four effects being
    compared -- at one frontier the sign of the result depended only on which
    arm loaded first.

    Odd trials run in order, even trials reversed, so the drift divides
    between the arms instead of landing on one. With an odd number of trials
    the split is uneven -- 2 of 3 in the first position -- which is better
    than 3 of 3 and is why the count is worth recording on the row rather
    than assumed to cancel.
    """
    ordered = list(backends.items())
    if trial % 2 == 0:
        ordered.reverse()
    return ordered


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
    run_position=None,
    run_arms=None,
    prepare_env_first=True,
):
    target = task_target(cfg, task)
    repo = pathlib.Path(target["repo"]).expanduser()
    suffix = "" if client == "claude" else f"-{client}"
    name = f"{task['name']}-{backend_name}{suffix}-{trial}"
    # #54: while the real checkout is stashed at <name>-real, the export stands
    # in its place, so the path a model guesses holds the excised tree. Trials
    # are serial, so one export at a time is fine.
    stashed_source = repo.with_name(repo.name + "-real")
    is_script = task.get("kind") == "script"
    # A script task starts from an empty directory: no repo, so no export, no
    # stash, no excision and nothing to leak. It never stands in the guessed
    # path, because there is no answer anywhere on disk to find.
    worktree = (
        workdir / name
        if is_script
        else (repo if stashed_source.exists() else workdir / name)
    )
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
        run_position=run_position,
        run_arms=run_arms,
    )

    # A previous run killed mid-flight leaves its directory behind. Clear it so
    # one aborted run cannot block every later attempt at the same cell.
    if worktree.exists():
        logger.warning("%s: removing stale checkout from an aborted run", name)
        shutil.rmtree(worktree, ignore_errors=True)

    if is_script:
        worktree.mkdir(parents=True, exist_ok=True)
        # The control for a script task is trivial and worth asserting anyway:
        # an empty directory must fail its own oracle, or the task proves
        # nothing. Same role as `control_fails_as_expected` on an excision.
        control_ok, _ = script_checks(
            worktree, task["entrypoint"], task["checks"], GATE_TIMEOUT
        )
        result["control_fails_as_expected"] = not control_ok
        result["removed_lines"] = 0
        result["removed_symbols"] = []
    else:
        # The export is materialised FROM the stashed real checkout, INTO the
        # path the model guesses. `source` differs from `repo` only while
        # stashed.
        build_checkout(
            stashed_source if stashed_source.exists() else repo,
            target["base_commit"],
            worktree,
        )
        # #4: build the env before the agent sees it, so wall time measures
        # the task and not the discovery of how to run pytest. Recorded on the
        # row because it starts a new series.
        if prepare_env_first:
            result.update(prepare_env(worktree))
    try:
        if not is_script:
            # 1. Hollow out the target, then make it the repository's only commit.
            #    Committing the excised state as the initial commit means the
            #    original body exists nowhere in this checkout -- not in history,
            #    not in an object store shared with anything else.
            keep_doc = task.get("keep_docstring", True)
            result["keep_docstring"] = keep_doc
            excised = []
            for t in targets(task):
                path = worktree / t["file"]
                body = exciser_for(t["file"])(
                    path, t["symbol"], keep_docstring=keep_doc
                )
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
            # A memcap kill here would mean the control check itself hit the cap,
            # which is a different failure mode than a memkill on the trial's
            # oracle and is left for a separate fix.
            ok, summary, _control_killed = tests_pass(
                worktree, task["tests"], ORACLE_TIMEOUT, target["test_command"]
            )
            result["control_fails_as_expected"] = not ok
            # Baseline the quality gates here, on the excised tree. gmail-archive
            # carries 18 mypy errors of its own, so only a delta against this state
            # says anything about what the agent wrote.
            if gates and not dry_run:
                result["gates_before"] = grade.gates(worktree, GATE_TIMEOUT)
            if ok:
                logger.error(
                    "%s: tests still pass after excision -- task is broken", name
                )
                result["error"] = "control passed"
                return result

        if gates and not dry_run and is_script:
            # No excised tree to baseline against; an empty directory is the
            # baseline, so the gates start from nothing.
            result["gates_before"] = {}

        if dry_run:
            result["dry_run"] = True
            # A script task has no excision, so there is no control to check --
            # `summary` is only bound in the excision branch above, and naming
            # it here raised UnboundLocalError for every script task since the
            # class was added. --dry-run is the one path nobody had run on one.
            logger.info(
                "%s: control ok (%s)",
                name,
                summary if not is_script else "script task; no control to check",
            )
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
                build_argv(task, backend, worktree),
                worktree,
                target["repo"],
                worktree.parent,
            )
            if sandbox
            else (build_argv(task, backend, worktree), [])
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
        if is_script:
            passed, summary = script_checks(
                worktree, task["entrypoint"], task["checks"], GATE_TIMEOUT
            )
        else:
            passed, summary, oracle_killed = tests_pass(
                worktree, task["tests"], ORACLE_TIMEOUT, target["test_command"]
            )
            if oracle_killed:
                # #82 item 4. Do not count a memkill as a model failure --
                # the code may be correct, and it is certainly not runnable
                # by this oracle. results.usable() drops the row on this flag,
                # so it never enters a pass rate.
                result["oracle_killed"] = True
                result["excluded"] = True
                result["exclusion_reason"] = summary
        result["passed"] = passed
        result["pytest"] = summary
        # #82: the number that would have caught a 49 GB oracle run before the
        # machine noticed. Recorded for every trial, pass or fail.
        result["peak_rss_gib"] = peak_child_rss_gib()
        # Guard against the obvious cheat. A script task ships no tests to
        # tamper with and has no git history to diff against.
        if is_script:
            result["touched_tests"] = False
        else:
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
        #
        # Meaningless for a script task: nothing was hollowed out, so there is
        # no original text to have recalled. Left as None rather than False,
        # since False would assert the agent wrote something new -- a claim this
        # check cannot make when there was never a reference.
        if not is_script:
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
        help="repeatable; DEFAULT opencode. Name another client only when "
        "the run is about that client. Multiple clients are interleaved "
        "per task so neither gets a systematically warmer or colder "
        "server than the other.",
    )
    p.add_argument(
        "--allow-implausible",
        action="store_true",
        help="do not halt when a cell collapses against this backend's record "
        "under another client. Use only when deliberately measuring a setup "
        "known to be broken (#55).",
    )
    p.add_argument(
        "--no-prepare-env",
        action="store_true",
        help="do not run `uv sync` in the checkout before the agent sees it "
        "(#4). Leaves the empty-virtualenv confound in the wall time, which is "
        "how every row before this was taken.",
    )
    p.add_argument(
        "--no-lock",
        action="store_true",
        help="do not claim the machine for this batch (#133). Only when you "
        "know nothing else will measure -- the lock exists because a process "
        "scan cannot see a batch that is between trials with its server down.",
    )
    p.add_argument(
        "--results",
        type=pathlib.Path,
        default=RESULTS,
        help="where rows are appended (default: benchmarks/agent/results.jsonl). "
        "One file, one hardware baseline (#20): a second machine writes to its "
        "own file under hardware/<machine>/, whose name comes from "
        "`uv run python scripts/hardware_id.py`.",
    )
    p.add_argument(
        "--allow-foreign-hardware",
        action="store_true",
        help="append rows even though results.jsonl holds rows from other "
        "hardware. One file, one hardware baseline (#20): every comparison in "
        "it assumes a shared machine, so mixing breaks them silently.",
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

    provenance.configure()
    cfg = tomllib.loads(pathlib.Path(args.tasks_file).read_text())
    tasks = [t for t in cfg["task"] if not args.task or t["name"] in args.task]
    backends = {
        k: v for k, v in cfg["backend"].items() if not args.backend or k in args.backend
    }
    # A retired backend stays in tasks.toml -- its rows are still valid and the
    # config is the record of how they were made -- but it is out of the default
    # matrix. Naming it explicitly still runs it, so a retirement can be
    # revisited without editing config back in.
    if not args.backend:
        for name, b in sorted(backends.items()):
            if b.get("retired"):
                logger.info("skipping retired backend %s: %s", name, b["retired"])
            elif b.get("tier"):
                # A tier belongs to other hardware. Its config lives here so the
                # rows it produced are reproducible, but it must never join the
                # default matrix on a machine that cannot serve it -- and its
                # rows are kept out of this file by foreign_hardware() (#20).
                logger.info("skipping backend %s: tier %s", name, b["tier"])
        backends = {
            k: v
            for k, v in backends.items()
            if not v.get("retired") and not v.get("tier")
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

    # 2026-09-01: OpenCode is the default and usually the only client.
    # Sweeping every client multiplies machine time across an axis that is
    # already measured, and the interesting axes are models and engines.
    clients = args.client or ["opencode"]
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

    # The smoke gate is what makes the model resident, so the served context is
    # only observable from here. `context_tokens` is written into every row, and
    # until 2026-09-02 nothing checked it against what the server loaded: a 9B
    # ran a repository task in a 4096 window while its rows claimed 131072.
    # Refuse rather than measure a truncation (#79).
    for gap in preflight.check_served_context(backends):
        raise SystemExit(f"served context is smaller than declared -- {gap}")

    # #133: claim the machine before the first trial. A batch runs for hours
    # and the restart-between-trials protocol leaves windows with no server
    # up, where a process scan truthfully reports "all clear". Released in the
    # finally below; pid liveness is what covers a SIGKILL, not this.
    if not args.no_lock:
        taken, why = preflight.acquire_lock(
            f"run.py {len(tasks)} tasks x {args.trials} trials on "
            f"{','.join(sorted(backends))}"
        )
        if not taken:
            raise SystemExit(why)
        logger.info("%s", why)
        # atexit rather than try/finally: the plausibility gate (#55) raises
        # SystemExit from inside the trial loop, and atexit covers that, a
        # clean finish and an unhandled exception alike without wrapping the
        # whole loop. It does NOT cover SIGKILL -- nothing does, which is why
        # pid liveness and not this is what makes a stale lock recoverable.
        atexit.register(lambda: logger.info("%s", preflight.release_lock()[1]))

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
    # Every trustworthy row already on disk, read once: the gate calibrates
    # against this project's own record rather than anyone's claims.
    # #20: one file, one machine. The first Linux run appended 13 rows to the
    # tracked results.jsonl and nothing objected -- it surfaced only because
    # `git pull` refused to merge over them. Mixing hardware does not corrupt a
    # row, it corrupts every comparison drawn across the file, after the fact.
    foreign = results.foreign_hardware(
        results.trials(args.results), preflight.machine_facts()
    )
    if foreign and not args.allow_foreign_hardware:
        raise SystemExit(
            f"{args.results} already holds rows from other hardware: "
            + "; ".join(" / ".join(f) for f in sorted(foreign))
            + f"\nThis machine is {preflight.machine_facts().get('cpu')}. "
            "One file, one hardware baseline (#20) -- point --results at a "
            "separate tier file, or pass --allow-foreign-hardware if you have "
            "decided the mixing is correct."
        )

    history = [r for r in results.trials(args.results) if not results.is_excluded(r)]
    cell: dict[tuple[str, str], list[dict]] = {}
    for trial in range(1, args.trials + 1):
        # #130: alternate which backend runs first. Throughput declines across
        # a measurement window, so a fixed order penalises whichever backend
        # always runs last. @adamlawi measured that bias on antirez/ds4#952 as
        # larger than three of the four effects being compared. Odd trials run
        # the backends in order, even trials reversed, so the drift divides
        # between them instead of landing on one. The client loop stays
        # innermost for the reason below.
        ordered = trial_order(backends, trial)
        for task in tasks:
            for position, (bname, backend) in enumerate(ordered, start=1):
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
                        run_position=position,
                        run_arms=len(ordered),
                        prepare_env_first=not args.no_prepare_env,
                    )
                    # Inside the client loop. Outside it, only the last
                    # client's row survives and half the run vanishes.
                    # write_row validates, stamps schema_valid/schema_errors
                    # and appends. A row that violates the schema is still
                    # written -- a trial costs up to half an hour and losing one
                    # to a schema bug is worse than storing a flagged row.
                    r["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    results.write_row(r, args.results)

                    # #55: let the batch disbelieve itself. A widely-used
                    # client collapsing on a backend that works under another
                    # client is the shape a harness bug makes -- it is what
                    # --dir produced, and it was published twice before anyone
                    # asked. Check after every trial so the alarm costs four
                    # trials rather than fifteen.
                    cell.setdefault((bname, client), []).append(r)
                    if not args.allow_implausible:
                        why = plausibility.implausible(
                            cell[(bname, client)], history, bname, client
                        )
                        if why:
                            logger.error("IMPLAUSIBLE: %s", why)
                            raise SystemExit("halted by the plausibility gate (#55)")


if __name__ == "__main__":
    main()
