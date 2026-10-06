#!/usr/bin/env python3
"""Run every OpenCode tool the trials use, in the client image, with no GPU. #968

From 2026-09-22, when trials moved into the client image (#683), every
OpenCode `grep` and `glob` call failed: 731 of 731 and 3,829 of 3,829. OpenCode
looks for `rg` on PATH, finds none in the image, and unpacks its own ripgrep
tarball. That `tar` runs as root under bwrap, where root cannot give a file
to another uid, so restoring the tarball's owner fails with "Cannot change
ownership". `skill` fails the same way. The model saw a tool error, worked
around it with `bash`, and the row scored the detour as the model's. Nothing
refused, because nothing checked a tool.

This checks every tool. It starts a stub OpenAI-compatible server that
answers with a fixed script of tool calls, one per tool, then runs the real
`opencode run` against it:

* **in the image**, through `client_container.container_argv`: the trial's
  user, privileges, mounts and memory limit;
* **under the trial's sandbox**, through `run.client_launch_argv` and
  `run.agent_env`: the same bwrap wrapper, deny list and environment;
* **in a throwaway repo** under the trials' own scratch root.

Then it reads the transcript. Every tool part must be `completed`, and its
effect must be on disk or in its output: the file written, the edit
applied, the planted string found by grep, the planted file matched by glob.
A failure names the tool and its error.

The stub lives inside the container, so the test needs no server, no GPU
and no network beyond loopback. It takes seconds. `build_client_image.py`
runs it before it tags an image, and `client_container.py` runs it before
every batch.

    uv run python scripts/client_tool_smoke.py
    uv run python scripts/client_tool_smoke.py --image local-llm-client:test
"""

from __future__ import annotations

import argparse
import dataclasses
import http.server
import json
import logging
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Self

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import logs

logger = logging.getLogger(__name__)

#: The provider and model the stub serves, as OpenCode names them.
PROVIDER = "toolsmoke"
MODEL = "scripted"

#: The last line the in-container half prints. The host half parses it, so a
#: container that died early (no line) is a failure, not a silent pass.
RESULT_PREFIX = "TOOL_SMOKE_RESULT "

#: Seconds for the whole `opencode run`. A healthy run takes a few; the bound
#: is there so a hung client fails the gate instead of holding it.
DEFAULT_TIMEOUT = 120

#: The skill the `skill` step loads. Planted in the throwaway repo.
SKILL_NAME = "tool-smoke"

PROMPT = (
    "Tool self-test (#968). A scripted server drives this session; "
    "run each tool call it returns."
)


@dataclasses.dataclass(frozen=True)
class Step:
    """One scripted tool call, and how to tell that it did its job."""

    tool: str
    args: dict[str, Any]
    #: (workdir, the tool's output) -> why the effect is missing, or None.
    effect: Callable[[pathlib.Path, str], str | None]


def _file_is(path: pathlib.Path, want: str) -> str | None:
    try:
        got = path.read_text()
    except OSError as exc:
        return f"{path.name} was not readable after the call: {exc}"
    if got != want:
        return f"{path.name} holds {got!r}, expected {want!r}"
    return None


def _output_has(needle: str, what: str) -> Callable[[pathlib.Path, str], str | None]:
    def check(_workdir: pathlib.Path, output: str) -> str | None:
        if needle in output:
            return None
        return f"its output does not contain {what} ({needle!r})"

    return check


def plant(workdir: pathlib.Path, token: str) -> None:
    """The files the steps look for, committed as a trial's repo is."""
    (workdir / "deep" / "nested").mkdir(parents=True)
    (workdir / "needle.py").write_text(f"NEEDLE = {token!r}\n")
    (workdir / "edit_me.txt").write_text(f"before {token}\n")
    (workdir / "deep" / "nested" / "planted.marker").write_text("marker\n")
    skill = workdir / ".opencode" / "skills" / SKILL_NAME
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {SKILL_NAME}\n"
        "description: The tool self-test's planted skill (#968).\n---\n\n"
        f"Skill body {token}.\n"
    )


def page_text(token: str) -> str:
    """What the stub serves at PAGE_PATH, for the `webfetch` step."""
    return f"Tool smoke page {token}\n"


#: Where the stub serves the `webfetch` step's page.
PAGE_PATH = "/smoke-page"


def steps(
    workdir: pathlib.Path, token: str, base_url: str = "http://127.0.0.1:1/v1"
) -> list[Step]:
    """One call per tool the trials use, in the order the stub sends them.

    OpenCode 1.18.34 offers bash, edit, glob, grep, read, skill, task,
    todowrite, webfetch and write. `task` starts a subagent, which is a
    second session rather than a tool, so it is left out. There is no `list`.

    Each effect is specific to `token`, so a file left by an earlier run
    cannot pass for this one. `base_url` is the stub's, which serves the
    page `webfetch` reads.
    """
    written = workdir / "written.txt"
    edited = workdir / "edit_me.txt"
    marker = workdir / "bash-wrote.txt"
    return [
        Step(
            "bash",
            {
                "command": f"printf %s {token} > {marker}",
                "description": "Write a marker file",
            },
            lambda _w, _o: _file_is(marker, token),
        ),
        Step(
            "read",
            {"filePath": str(workdir / "needle.py")},
            _output_has(token, "the planted file's text"),
        ),
        Step(
            "write",
            {"filePath": str(written), "content": f"written {token}\n"},
            lambda _w, _o: _file_is(written, f"written {token}\n"),
        ),
        Step(
            "edit",
            {
                "filePath": str(edited),
                "oldString": f"before {token}",
                "newString": f"after {token}",
            },
            lambda _w, _o: _file_is(edited, f"after {token}\n"),
        ),
        Step(
            "grep",
            {"pattern": token, "path": str(workdir), "include": "*.py"},
            _output_has("needle.py", "the planted file"),
        ),
        Step(
            "glob",
            {"pattern": "**/*.marker", "path": str(workdir)},
            _output_has("planted.marker", "the planted file"),
        ),
        Step(
            "todowrite",
            {
                "todos": [
                    {
                        "content": f"smoke {token}",
                        "status": "completed",
                        "priority": "low",
                        "id": "1",
                    }
                ]
            },
            _output_has(token, "the todo"),
        ),
        Step(
            "skill",
            {"name": SKILL_NAME},
            _output_has(token, "the planted skill's body"),
        ),
        Step(
            "webfetch",
            {
                "url": base_url.removesuffix("/v1") + PAGE_PATH,
                "format": "text",
            },
            _output_has(token, "the stub's page"),
        ),
    ]


# --- the stub server --------------------------------------------------------


def tool_results_seen(body: dict[str, Any]) -> int:
    """How many tool results the conversation already carries.

    The stub is stateless: the next step is the count of results so far. A
    failed call still returns a result, so one broken tool cannot stall the
    script and hide the tools after it.
    """
    return sum(1 for m in body.get("messages") or [] if m.get("role") == "tool")


def next_reply(body: dict[str, Any], script: list[Step]) -> dict[str, Any]:
    """`{"call": {...}}` for the next scripted step, or `{"text": ...}`.

    A request that offers no tools is not the agent loop (OpenCode asks for
    a session title that way), so it gets text.
    """
    if not body.get("tools"):
        return {"text": "tool smoke"}
    n = tool_results_seen(body)
    if n >= len(script):
        return {"text": "Every scripted tool call has run."}
    step = script[n]
    return {
        "call": {
            "id": f"call_smoke_{n}",
            "name": step.tool,
            "arguments": json.dumps(step.args),
        }
    }


_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def stream_chunks(reply: dict[str, Any], model: str = MODEL) -> list[dict[str, Any]]:
    """The `chat.completion.chunk` events for one reply, in order."""
    base = {"id": "chatcmpl-smoke", "object": "chat.completion.chunk", "created": 0}
    base["model"] = model
    if "call" in reply:
        call = reply["call"]
        delta: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "index": 0,
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
            ],
        }
        finish = "tool_calls"
    else:
        delta = {"role": "assistant", "content": reply["text"]}
        finish = "stop"
    return [
        {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            "usage": _USAGE,
        },
    ]


def completion(reply: dict[str, Any], model: str = MODEL) -> dict[str, Any]:
    """The same reply as one non-streaming `chat.completion`."""
    message: dict[str, Any] = {"role": "assistant", "content": reply.get("text")}
    finish = "stop"
    if "call" in reply:
        call = reply["call"]
        message["tool_calls"] = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
        ]
        finish = "tool_calls"
    return {
        "id": "chatcmpl-smoke",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _USAGE,
    }


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _StubHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("stub: " + format, *args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._send_json(200, {"object": "list", "data": [{"id": MODEL}]})
        elif self.path == PAGE_PATH:
            data = self.server.page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "body is not JSON"})
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json(404, {"error": f"no route {self.path}"})
            return
        self.server.requests += 1
        offered = {
            (t.get("function") or {}).get("name") for t in body.get("tools") or []
        }
        self.server.offered |= {name for name in offered if name}
        reply = next_reply(body, self.server.script)
        if not body.get("stream"):
            self._send_json(200, completion(reply))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for chunk in stream_chunks(reply):
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


class _StubHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, script: list[Step], page: str) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.script = script
        self.page = page
        self.requests = 0
        self.offered: set[str] = set()


class StubServer:
    """The scripted server on a free loopback port, for a `with` block.

    `script` may be set after the server exists (`httpd.script`): the
    `webfetch` step needs the server's own URL.
    """

    def __init__(self, script: list[Step] | None = None, page: str = "") -> None:
        self.httpd = _StubHTTPServer(script or [], page)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host!s}:{port}/v1"

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --- the transcript checker -------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ToolPart:
    """A tool call's final state, as `opencode run --format json` reports it."""

    tool: str
    status: str
    output: str
    error: str


def tool_parts(transcript: str) -> list[ToolPart]:
    """The last state of each tool call, in the order the calls appeared."""
    by_call: dict[str, ToolPart] = {}
    for line in transcript.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") if isinstance(event, dict) else None
        if not isinstance(part, dict) or part.get("type") != "tool":
            continue
        state = part.get("state") or {}
        key = str(part.get("callID") or part.get("id") or len(by_call))
        by_call[key] = ToolPart(
            tool=str(part.get("tool") or "?"),
            status=str(state.get("status") or "?"),
            output=str(state.get("output") or ""),
            error=str(state.get("error") or ""),
        )
    return list(by_call.values())


def _first_line(text: str, limit: int = 300) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "(no error text)"
    head = lines[0][:limit]
    more = len(lines) - 1
    return f"{head} (+{more} more lines)" if more else head


def check(transcript: str, workdir: pathlib.Path, script: list[Step]) -> list[str]:
    """One line per failed tool: `<tool>: <why>`. Empty means every tool ran.

    A tool fails if it never ran, ended in any status but `completed`, or
    completed without its effect. A call to a tool not in the script fails
    too: OpenCode reports a malformed or unknown call as tool `invalid`.
    """
    parts = tool_parts(transcript)
    failures = []
    remaining = list(parts)
    for step in script:
        part = next((p for p in remaining if p.tool == step.tool), None)
        if part is None:
            failures.append(f"{step.tool}: never ran (no tool part in the transcript)")
            continue
        remaining.remove(part)
        if part.status != "completed":
            failures.append(f"{step.tool}: {part.status}: {_first_line(part.error)}")
            continue
        why = step.effect(workdir, part.output)
        if why:
            failures.append(f"{step.tool}: completed, but {why}")
    for part in remaining:
        failures.append(
            f"{part.tool}: unexpected call, {part.status}: "
            f"{_first_line(part.error or part.output)}"
        )
    return failures


# --- inside the container ---------------------------------------------------


def opencode_config(base_url: str) -> dict[str, Any]:
    """The stub as an OpenCode provider. Merged over the host's config."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Tool self-test stub (#968)",
                "options": {"baseURL": base_url, "apiKey": "local"},
                "models": {MODEL: {"name": MODEL, "tool_call": True}},
            }
        },
    }


def _git(workdir: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=workdir, check=True, capture_output=True, text=True
    )


@dataclasses.dataclass
class Outcome:
    failures: list[str]
    seconds: float
    mechanism: str
    requests: int


def run_inside(timeout: int = DEFAULT_TIMEOUT) -> Outcome:
    """Plant a repo, serve the script, run the client as a trial does, check."""
    import run

    token = uuid.uuid4().hex[:12]
    # Where real trials live (run.py's scratch root), so the sandbox treats
    # this directory as it treats a trial's.
    root = pathlib.Path(tempfile.gettempdir()) / "agent-bench"
    workdir = root / f"tool-smoke-{token}"
    workdir.mkdir(parents=True)
    # Outside /tmp: bwrap gives the client a private /tmp, so a config there
    # would vanish from its view. HOME is the container's own, not mounted.
    config_dir = pathlib.Path.home() / ".cache" / "local-llm" / "tool-smoke"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / f"opencode-{token}.json"
    t0 = time.monotonic()
    try:
        plant(workdir, token)
        _git(workdir, "init", "-q", "-b", "main")
        _git(workdir, "add", "-A")
        _git(
            workdir,
            "-c",
            "user.email=bench@local",
            "-c",
            "user.name=bench",
            "commit",
            "-q",
            "-m",
            "tool smoke: starting state",
        )
        with StubServer(page=page_text(token)) as stub:
            script = steps(workdir, token, stub.base_url)
            stub.httpd.script = script
            config.write_text(json.dumps(opencode_config(stub.base_url)))
            backend = {
                "model": MODEL,
                "opencode_model": f"{PROVIDER}/{MODEL}",
                "base_url": stub.base_url.removesuffix("/v1"),
                "auth_token": "local",
                "context_tokens": 32768,
            }
            argv, _denied, mechanism, _limit = run.client_launch_argv(
                run.opencode_argv,
                {"prompt": PROMPT},
                backend,
                workdir,
                pathlib.Path.home() / "git" / "gmail-archive",
            )
            env = run.agent_env(backend, workdir)
            # OPENCODE_CONFIG survives the trial's environment filter
            # (run.TRIAL_ENV_KEEP); the inline OPENCODE_CONFIG_CONTENT does not.
            env["OPENCODE_CONFIG"] = str(config)
            logger.info("sandbox: %s; token %s; %s", mechanism, token, workdir)
            try:
                proc = run.run(argv, cwd=workdir, env=env, timeout=timeout)
                stdout, stderr = proc.stdout or "", proc.stderr or ""
                exit_note = f"exit {proc.returncode}"
            except subprocess.TimeoutExpired as exc:
                stdout = _text(exc.stdout)
                stderr = _text(exc.stderr)
                exit_note = f"timed out after {timeout} s"
            requests = stub.httpd.requests
            offered = stub.httpd.offered
        logger.info("OpenCode offered: %s", ", ".join(sorted(offered)) or "nothing")
        failures = check(stdout, workdir, script)
        missing = sorted({s.tool for s in script} - offered)
        if offered and missing:
            failures.append(f"OpenCode did not offer: {', '.join(missing)}")
        if failures:
            logger.info("opencode %s after %d request(s)", exit_note, requests)
            for line in stderr.strip().splitlines()[-15:]:
                logger.info("opencode stderr: %s", line)
        return Outcome(failures, round(time.monotonic() - t0, 1), mechanism, requests)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(run.trial_tmp(workdir), ignore_errors=True)
        config.unlink(missing_ok=True)


def _text(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    return raw.decode(errors="replace") if isinstance(raw, bytes) else raw


def inside_main(timeout: int) -> int:
    outcome = run_inside(timeout)
    for failure in outcome.failures:
        logger.error("FAIL  %s", failure)
    if not outcome.failures:
        logger.info("PASS  every tool: %s", ", ".join(TOOLS))
    # The host half reads this line, so it goes to stdout as-is.
    sys.stdout.write(RESULT_PREFIX + json.dumps(dataclasses.asdict(outcome)) + "\n")
    sys.stdout.flush()
    return 1 if outcome.failures else 0


#: The tools the script exercises, in order.
TOOLS = tuple(s.tool for s in steps(pathlib.Path("/"), "token"))


# --- on the host ------------------------------------------------------------


def smoke_argv(
    image: str,
    *,
    home: pathlib.Path,
    mem_limit_gib: float | None,
    timeout: int = DEFAULT_TIMEOUT,
    repo: pathlib.Path = REPO,
) -> list[str]:
    """The `docker run` for the self-test: a trial's container, this command.

    A mount whose host path is absent is left out rather than created: Docker
    would create it root-owned. `client_container.py` refuses a batch with a
    missing mount before this runs, so a batch's self-test has them all; only
    an image build on a bare host runs with fewer.
    """
    import client_container

    mounts = [
        (rel, mode)
        for rel, mode in client_container.MOUNTS
        if rel == "git/local-llm" or (home / rel).exists()
    ]
    skipped = [
        rel for rel, mode in client_container.MOUNTS if (rel, mode) not in mounts
    ]
    if skipped:
        logger.warning("not on this host, so not mounted: %s", ", ".join(skipped))
    return client_container.container_argv(
        image=image,
        home=home,
        command=[
            "uv",
            "run",
            # No project: the self-test needs only the standard library, and a
            # project sync would install the dev group on every run.
            "--no-project",
            "python",
            "scripts/client_tool_smoke.py",
            "--inside",
            "--timeout",
            str(timeout),
        ],
        mem_limit_gib=mem_limit_gib,
        mounts=mounts,
        sources={"git/local-llm": repo},
    )


def parse_result(output: str) -> dict[str, Any] | None:
    """The in-container half's result line, or None if it never printed one."""
    for line in reversed(output.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                got = json.loads(line[len(RESULT_PREFIX) :])
            except json.JSONDecodeError:
                return None
            return got if isinstance(got, dict) else None
    return None


def run_in_image(
    image: str,
    *,
    home: pathlib.Path | None = None,
    mem_limit_gib: float | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """Run the self-test in `image`; one line per failure, empty on a pass."""
    import client_container

    home = home or pathlib.Path.home()
    if mem_limit_gib is None:
        mem_limit_gib = client_container.DEFAULT_MEM_LIMIT_GIB
    argv = smoke_argv(image, home=home, mem_limit_gib=mem_limit_gib, timeout=timeout)
    logger.info("tool self-test: %s (#968)", image)
    t0 = time.monotonic()
    try:
        got = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout + 120,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [f"self-test: the container did not finish in {timeout + 120} s"]
    output = (got.stdout or "") + (got.stderr or "")
    for line in output.splitlines():
        if line.strip() and not line.startswith(RESULT_PREFIX):
            logger.info("  %s", line)
    result = parse_result(got.stdout or "")
    if result is None:
        tail = " | ".join(output.strip().splitlines()[-5:]) or "(no output)"
        return [
            f"self-test: the container exited {got.returncode} without a result: {tail}"
        ]
    failures = [str(f) for f in result.get("failures") or []]
    logger.info(
        "tool self-test %s in %.1f s (%.1f s inside; sandbox %s; %s request(s))",
        "FAILED" if failures else "passed",
        time.monotonic() - t0,
        float(result.get("seconds") or 0),
        result.get("mechanism"),
        result.get("requests"),
    )
    return failures


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--image", default=None, help="default: the pinned client image")
    p.add_argument("--home", type=pathlib.Path, default=pathlib.Path.home())
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument(
        "--inside",
        action="store_true",
        help="the half that runs in the container; the host half starts it",
    )
    args = p.parse_args(argv)
    if args.inside:
        # Plain: the host half relays these lines under its own timestamps.
        logs.configure(fmt=logs.PLAIN)
        return inside_main(args.timeout)
    logs.configure()

    import client_container

    image = args.image or client_container.DEFAULT_IMAGE
    # run_in_image relays the container's own FAIL lines, so they are not
    # repeated here.
    failures = run_in_image(image, home=args.home, timeout=args.timeout)
    if failures:
        logger.error("%s: %d tool(s) failed (#968)", image, len(failures))
        return 1
    logger.info("%s: every tool works", image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
