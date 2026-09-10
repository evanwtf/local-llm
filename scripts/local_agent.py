#!/usr/bin/env python3
"""Start a recommended local stack and drop into a coding agent. #235

    uv run python scripts/local_agent.py <stack> [client] [--check] [-- args...]

    stacks   starter | fast | mainline | lineage   (see RECOMMENDATIONS.md)
    client   opencode (default) | claude

It fetches the weights if they are missing, fetches and builds the engine if it
is missing, starts the server and whatever shim the stack needs, waits until the
endpoint answers, then execs the agent.

Two things it deliberately does NOT do, ported verbatim from the shell:

  - It never downloads tens of gigabytes without asking. Every fetch prints the
    size and waits for a yes (`LOCAL_AGENT_YES=1` skips the prompt).
  - It never starts a second engine when one is already listening on the port.
    One model at a time is the rule the benchmark runs under, and it is the rule
    here for the same reason: this machine cannot hold two.

## Why this is a Python port and not a shell script (#235)

The shell version started its servers with `nohup ... &`, which leaves a process
that only `pgrep` can find. This port starts every server and shim as a
**named `unitctl` unit**: the pid is recorded at spawn, the child gets its own
process group, and `scripts/unitctl.py stop <name>` reaps it. The launcher then
`exec`s the agent, so the units outlive it exactly as the backgrounded shell
processes did.

The stack table is the one place anything differs between stacks, and every
value in it is a number a user depends on -- a wrong port or model path is a
silent "it started the wrong thing". `tests/test_local_agent.py` pins the table.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import ports
import provenance
import unitctl

logger = logging.getLogger(__name__)

HOME = pathlib.Path.home()
CLIENTS = ("opencode", "claude")


class LaunchError(RuntimeError):
    """A fetch, build or start step the operator must see and fix."""


@dataclasses.dataclass(frozen=True)
class Stack:
    """Everything that differs between stacks, and nothing that does not."""

    label: str
    engine: str  # ollama | ds4 | llamacpp
    engine_port: int
    ctx: int
    # weights
    model_file: str | None = None  # None for ollama (a tag, not a file)
    model_dir: str | None = None
    ple_file: str | None = None
    ollama_tag: str | None = None
    hf_repo: str | None = None
    hf_include: str | None = None
    dl_size: str = ""
    # engine build
    engine_tree: str | None = None
    engine_repo: str | None = None
    engine_branch: str | None = None
    # shims
    shim_port: int | None = None  # ds4 Qwen tool shim (#112)
    claude_port: int = 0
    claude_upstream: str | None = None  # set only where claude needs a wire shim
    # client wiring
    opencode_model: str = ""
    opencode_baseurl: str = ""
    claude_model: str = ""
    claude_token: str = ""


# The stack table. Ported one-for-one from scripts/local-agent.sh lines 64-112;
# every value is asserted against that source in tests/test_local_agent.py.
STACKS: dict[str, Stack] = {
    "starter": Stack(
        label="Qwen3.6-27B-coding on Ollama (slot 1: starting out)",
        engine="ollama",
        engine_port=11434,
        ctx=262144,
        ollama_tag="qwen3.6:27b-coding-mxfp8",
        dl_size="31 GB",
        opencode_model="ollama/qwen3.6:27b-coding-mxfp8",
        opencode_baseurl="http://127.0.0.1:11434/v1",
        claude_port=11500,
        claude_upstream="http://127.0.0.1:11434",
        claude_model="qwen3.6:27b-coding-mxfp8",
        claude_token="ollama",
    ),
    "fast": Stack(
        label="Qwen3.8-Flash-Next Q4_K imatrix on ds4 (slot 2: you want it fast)",
        engine="ds4",
        engine_port=8000,
        ctx=100000,
        shim_port=8101,
        engine_tree=str(HOME / "git" / "ds4-ivan-qwen38fn"),
        engine_repo="https://github.com/ivanfioravanti/ds4.git",
        engine_branch="qwen3.8-flash-next",
        model_dir=str(HOME / "models" / "qwen3.8-flash-next-ds4-q4k-imatrix"),
        model_file=str(
            HOME
            / "models"
            / "qwen3.8-flash-next-ds4-q4k-imatrix"
            / "Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
        ),
        ple_file=str(
            HOME
            / "models"
            / "qwen3.8-flash-next-ds4-q4k-imatrix"
            / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
        ),
        hf_repo="ivanfioravanti/Qwen3.8-Flash-Next-DS4-Q4",
        dl_size="105 GB (weights + a 30 GB PLE sidecar)",
        opencode_model="ds4qwenshim/qwen3.8-flash-next-q4",
        opencode_baseurl="http://127.0.0.1:8101/v1",
        claude_port=8101,
        claude_model="qwen3.8-flash-next-q4",
        claude_token="dsv4-local",
    ),
    "mainline": Stack(
        label="Qwen3.8-Flash-Next UD-Q3_K_XL on llama.cpp (the mainline fallback)",
        engine="llamacpp",
        engine_port=8020,
        ctx=131072,
        engine_tree=str(HOME / "git" / "llama.cpp"),
        engine_repo="https://github.com/ggml-org/llama.cpp",
        model_dir=str(HOME / "models" / "Qwen3.8-Flash-Next-GGUF" / "UD-Q3_K_XL"),
        model_file=str(
            HOME
            / "models"
            / "Qwen3.8-Flash-Next-GGUF"
            / "UD-Q3_K_XL"
            / "Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf"
        ),
        hf_repo="unsloth/Qwen3.8-Flash-Next-GGUF",
        hf_include="UD-Q3_K_XL/*",
        dl_size="84 GB",
        opencode_model="llamacpp/qwen3.8-flash-next-q3",
        opencode_baseurl="http://127.0.0.1:8020/v1",
        claude_port=11500,
        claude_upstream="http://127.0.0.1:8020",
        claude_model="qwen3.8-flash-next-q3",
        claude_token="llamacpp-local",
    ),
    "lineage": Stack(
        label="DeepSeek-V4-Flash on ds4 (slot 3: a second lineage)",
        engine="ds4",
        engine_port=8000,
        ctx=100000,
        engine_tree=str(HOME / "git" / "ds4-ivan-qwen38fn"),
        engine_repo="https://github.com/ivanfioravanti/ds4.git",
        engine_branch="qwen3.8-flash-next",
        model_dir=str(HOME / "git" / "ds4" / "gguf"),
        model_file=str(
            HOME
            / "git"
            / "ds4"
            / "gguf"
            / "DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"
        ),
        dl_size="91 GB",
        opencode_model="ds4/deepseek-v4-flash",
        opencode_baseurl="http://127.0.0.1:8000/v1",
        claude_port=8000,
        claude_model="deepseek-v4-flash",
        claude_token="dsv4-local",
    ),
}


@dataclasses.dataclass(frozen=True)
class Invocation:
    """The parsed command line, with side effects still to come."""

    stack: str
    client: str
    check_only: bool
    agent_args: list[str]


def parse_invocation(argv: list[str]) -> Invocation:
    """Parse argv the way the shell did, validating the client BEFORE anything.

    The shell validates the client immediately (lines 56-59) so that a typo'd
    client name costs a diagnostic, not a 105 GB download and a model load. The
    order matters and is part of the contract, so it is a pure function here and
    a test pins it.

    Positional 1 is the stack, positional 2 is the client (default `opencode`).
    Everything after the first two tokens passes through to the agent, minus a
    literal `--` separator and any `--check` flag.
    """
    if not argv:
        raise LaunchError(
            "usage: local_agent.py <starter|fast|mainline|lineage> "
            "[opencode|claude] [--check] [-- args]"
        )
    stack = argv[0]
    client = argv[1] if len(argv) > 1 else "opencode"
    if client not in CLIENTS:
        raise LaunchError(f"unknown client '{client}' ({'|'.join(CLIENTS)})")
    if stack not in STACKS:
        raise LaunchError(f"unknown stack '{stack}' ({'|'.join(STACKS)})")

    check_only = "--check" in argv
    # Drop the first min(len, 2) positionals, then collect the rest, dropping a
    # literal "--" separator and every --check. Mirrors the shell's seen_sep
    # loop (lines 237-245): both before and after "--" pass through.
    rest = argv[min(len(argv), 2) :]
    agent_args = [a for a in rest if a not in ("--check", "--")]
    return Invocation(stack, client, check_only, agent_args)


def log_dir() -> pathlib.Path:
    d = pathlib.Path(os.environ.get("LOCAL_AGENT_LOG_DIR", HOME / ".local-llm-agent"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def confirm(prompt: str) -> bool:
    """Ask the operator, honouring LOCAL_AGENT_YES=1."""
    if os.environ.get("LOCAL_AGENT_YES") == "1":
        logger.info("auto-yes: %s", prompt)
        return True
    reply = input(f"{prompt} [y/N] ")
    return reply[:1] in ("y", "Y")


def listening(port: int) -> bool:
    """Whether something accepts a connection on `port` (the shell's `nc -z`)."""
    return ports.answers(port)


def wait_ready(port: int, label: str, limit: int = 600) -> None:
    logger.info("waiting for %s on :%d", label, port)
    waited = 0
    while waited < limit:
        if listening(port):
            logger.info("%s is up after %ds", label, waited)
            return
        time.sleep(3)
        waited += 3
    raise LaunchError(
        f"{label} did not come up on :{port} within {limit}s -- see {log_dir()}"
    )


def _run(cmd: list[str], *, cwd: pathlib.Path | None = None) -> None:
    logger.info("run: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False)
    if result.returncode != 0:
        raise LaunchError(f"command failed (rc={result.returncode}): {' '.join(cmd)}")


def fetch_hf(repo: str, local_dir: str, include: str | None = None) -> None:
    if not _which("hf"):
        raise LaunchError(
            "the 'hf' CLI is not installed: pip install -U huggingface_hub"
        )
    cmd = ["hf", "download", repo]
    if include:
        cmd += ["--include", include]
    cmd += ["--local-dir", local_dir]
    env = dict(os.environ, HF_HUB_ENABLE_HF_TRANSFER="1")
    logger.info("run: %s", " ".join(cmd))
    if subprocess.run(cmd, env=env, check=False).returncode != 0:
        raise LaunchError("download failed")


def _which(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def check_report(stack: Stack) -> None:
    """The `--check` report: stop before any download, build or server start.

    `main` already logged the label and client, so this adds only the state.
    """
    state = "(listening)" if listening(stack.engine_port) else "(free)"
    logger.info("  engine port :%d %s", stack.engine_port, state)
    if stack.model_file:
        present = "present" if pathlib.Path(stack.model_file).is_file() else "MISSING"
        logger.info("  weights: %s %s", present, stack.model_file)
    if stack.ollama_tag:
        logger.info("  ollama tag: %s", stack.ollama_tag)
    logger.info("  --check: stopping before any download, build or server start")


def ensure_weights(stack: Stack) -> None:
    if stack.engine == "ollama":
        assert stack.ollama_tag
        if not _which("ollama"):
            raise LaunchError("ollama is not installed: brew install ollama")
        have = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, check=False
        ).stdout
        tags = {line.split()[0] for line in have.splitlines()[1:] if line.split()}
        if stack.ollama_tag in tags:
            logger.info("  model present: %s", stack.ollama_tag)
            return
        logger.info("model %s is not present (%s)", stack.ollama_tag, stack.dl_size)
        if not confirm(f"  download {stack.ollama_tag} ({stack.dl_size})?"):
            raise LaunchError("declined")
        _run(["ollama", "pull", stack.ollama_tag])
        return

    assert stack.model_file and stack.model_dir
    if not pathlib.Path(stack.model_file).is_file():
        logger.info("weights are missing: %s", stack.model_file)
        if not stack.hf_repo:
            raise LaunchError(
                "no download source recorded for this stack -- fetch it by hand"
            )
        if not confirm(f"  download {stack.hf_repo} ({stack.dl_size})?"):
            raise LaunchError("declined")
        pathlib.Path(stack.model_dir).mkdir(parents=True, exist_ok=True)
        fetch_hf(stack.hf_repo, stack.model_dir, stack.hf_include)
    else:
        logger.info("  weights present: %s", pathlib.Path(stack.model_file).name)

    # The kimat stack needs a PLE sidecar; missing it is the single easiest way
    # to get a confusing failure instead of a clear one.
    if stack.ple_file and not pathlib.Path(stack.ple_file).exists():
        logger.info("PLE sidecar missing: %s", stack.ple_file)
        if not confirm("  download the PLE sidecar (30 GB)?"):
            raise LaunchError("declined")
        assert stack.hf_repo and stack.model_dir
        fetch_hf(stack.hf_repo, stack.model_dir, "*PLE*")


def ensure_engine(stack: Stack) -> None:
    if stack.engine == "ollama":
        version = subprocess.run(
            ["ollama", "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
        logger.info("  engine: ollama %s", version.splitlines()[-1] if version else "")
        return
    assert stack.engine_tree
    tree = pathlib.Path(stack.engine_tree)
    if stack.engine == "ds4":
        binary = tree / "ds4-server"
        if binary.is_file() and os.access(binary, os.X_OK):
            logger.info("  engine: %s @ %s", tree, _git_head(tree))
            return
        logger.info("ds4 engine not built at %s", tree)
        if not confirm("  clone and build ds4 (a few minutes)?"):
            raise LaunchError("declined")
        if not (tree / ".git").is_dir():
            assert stack.engine_repo
            _run(["git", "clone", stack.engine_repo, str(tree)])
        assert stack.engine_branch
        _run(["git", "checkout", stack.engine_branch], cwd=tree)
        _run(["make"], cwd=tree)
    elif stack.engine == "llamacpp":
        binary = tree / "build" / "bin" / "llama-server"
        if binary.is_file() and os.access(binary, os.X_OK):
            logger.info("  engine: llama.cpp @ %s", _git_head(tree))
            return
        logger.info("llama.cpp not built at %s", tree)
        if not confirm("  clone and build llama.cpp (a few minutes)?"):
            raise LaunchError("declined")
        if not (tree / ".git").is_dir():
            assert stack.engine_repo
            _run(["git", "clone", stack.engine_repo, str(tree)])
        _run(
            [
                "cmake",
                "-B",
                str(tree / "build"),
                "-S",
                str(tree),
                "-DGGML_METAL=ON",
                "-DCMAKE_BUILD_TYPE=Release",
            ]
        )
        _run(["cmake", "--build", str(tree / "build"), "--config", "Release", "-j"])


def _git_head(tree: pathlib.Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() or "unknown"


def start_server(stack: Stack) -> None:
    """Start the engine as a named unit, unless the port is already answered."""
    if listening(stack.engine_port):
        logger.info(
            "  something is already listening on :%d -- reusing it, not starting "
            "a second engine",
            stack.engine_port,
        )
        return
    logger.info("starting %s on :%d", stack.engine, stack.engine_port)
    logs = log_dir()
    if stack.engine == "ds4":
        assert stack.engine_tree and stack.model_file
        cmd = ["./ds4-server", "--metal", "-m", stack.model_file]
        if stack.ple_file:
            cmd += ["--ple", stack.ple_file]
        cmd += [
            "--ctx",
            str(stack.ctx),
            "--warm-weights",
            "--host",
            "127.0.0.1",
            "--port",
            str(stack.engine_port),
        ]
        unitctl.start(
            "local-agent-ds4",
            cmd,
            log=logs / "ds4-server.log",
            cwd=pathlib.Path(stack.engine_tree),
        )
    elif stack.engine == "llamacpp":
        assert stack.engine_tree and stack.model_file
        cmd = [
            str(pathlib.Path(stack.engine_tree) / "build" / "bin" / "llama-server"),
            "-m",
            stack.model_file,
            "-a",
            stack.claude_model,
            "--host",
            "127.0.0.1",
            "--port",
            str(stack.engine_port),
            "-c",
            str(stack.ctx),
            "-np",
            "1",
            "--temp",
            "1.0",
            "--top-p",
            "0.95",
            "--top-k",
            "20",
            "--min-p",
            "0.0",
        ]
        unitctl.start("local-agent-llamacpp", cmd, log=logs / "llama-server.log")
    elif stack.engine == "ollama":
        unitctl.start(
            "local-agent-ollama", ["ollama", "serve"], log=logs / "ollama.log"
        )
    wait_ready(stack.engine_port, stack.engine, 900)


def start_shims(stack: Stack, client: str) -> None:
    logs = log_dir()
    # The ds4 Qwen stack always needs its tool-format shim: #112 measured the
    # scaffolding strip as worth 23 points of pass rate, so this is part of the
    # stack, not optional plumbing.
    if stack.shim_port and not listening(stack.shim_port):
        logger.info("starting the ds4 Qwen tool shim on :%d", stack.shim_port)
        unitctl.start(
            "local-agent-qwen-shim",
            [
                "uv",
                "run",
                "python",
                "ds4_qwen_tool_shim.py",
                "--upstream",
                f"http://127.0.0.1:{stack.engine_port}",
                "--port",
                str(stack.shim_port),
            ],
            log=logs / "qwen-tool-shim.log",
            cwd=REPO,
        )
        wait_ready(stack.shim_port, "tool shim", 120)

    # Claude Code speaks the Anthropic wire. Ollama and llama.cpp do not, so
    # those stacks need the translating shim in front before `claude` will talk
    # to them at all.
    if (
        client == "claude"
        and stack.claude_upstream
        and not listening(stack.claude_port)
    ):
        logger.info("starting the Anthropic-wire shim on :%d", stack.claude_port)
        unitctl.start(
            "local-agent-claude-shim",
            [
                "uv",
                "run",
                "python",
                "ollama_claude_shim.py",
                "--port",
                str(stack.claude_port),
                "--upstream",
                stack.claude_upstream,
            ],
            log=logs / "claude-shim.log",
            cwd=REPO,
        )
        wait_ready(stack.claude_port, "Anthropic shim", 120)


def declare_opencode_provider(config_path: pathlib.Path, model: str, base: str) -> None:
    """Declare the model's provider in OpenCode's config, creating it if absent.

    #69: OpenCode resolves a model only if its provider is declared in the
    config file, which lives outside this repo. An undeclared model made
    `opencode run` exit in 0.6s, and six client crashes were recorded as six
    model failures. Declaring it rather than letting that happen again is the
    whole reason this step exists.
    """
    provider, name = model.split("/", 1)
    data = json.loads(config_path.read_text()) if config_path.exists() else {}
    prov = data.setdefault("provider", {}).setdefault(provider, {})
    prov.setdefault("npm", "@ai-sdk/openai-compatible")
    prov.setdefault("options", {})["baseURL"] = base
    if name not in prov.setdefault("models", {}):
        prov["models"][name] = {}
        logger.info("  declared %s in %s", model, config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n")


def exec_client(stack: Stack, client: str, agent_args: list[str]) -> None:
    """Replace this process with the agent. Does not return on success."""
    if client == "opencode":
        config = pathlib.Path(
            os.environ.get(
                "OPENCODE_CONFIG", HOME / ".config" / "opencode" / "opencode.json"
            )
        ).expanduser()
        declare_opencode_provider(config, stack.opencode_model, stack.opencode_baseurl)
        logger.info("starting opencode on %s", stack.opencode_model)
        os.execvp(
            "opencode", ["opencode", "--model", stack.opencode_model, *agent_args]
        )
    elif client == "claude":
        logger.info("starting claude on %s", stack.claude_model)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{stack.claude_port}"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = stack.claude_token
        os.environ["ANTHROPIC_MODEL"] = stack.claude_model
        os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"] = stack.claude_model
        os.environ["ANTHROPIC_DEFAULT_OPUS_MODEL"] = stack.claude_model
        os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = stack.claude_model
        os.environ["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(stack.ctx)
        os.execvp("claude", ["claude", *agent_args])


def main(argv: list[str] | None = None) -> int:
    provenance.configure(show_name=True)
    try:
        inv = parse_invocation(list(sys.argv[1:] if argv is None else argv))
    except LaunchError as exc:
        logger.error("%s", exc)
        return 2
    stack = STACKS[inv.stack]
    logger.info("=== %s", stack.label)
    logger.info("  client: %s", inv.client)
    try:
        if inv.check_only:
            check_report(stack)
            return 0
        ensure_weights(stack)
        ensure_engine(stack)
        start_server(stack)
        start_shims(stack, inv.client)
        exec_client(stack, inv.client, inv.agent_args)
    except LaunchError as exc:
        logger.error("%s", exc)
        return 1
    return 0  # unreachable on a successful exec


if __name__ == "__main__":
    raise SystemExit(main())
