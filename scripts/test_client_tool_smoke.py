"""The OpenCode tool self-test must fail loudly on a broken tool. #968

From 2026-09-22 every grep and glob call in the client image failed, and
nothing noticed for two weeks. These tests pin the two halves that decide
whether the self-test would notice: the transcript checker (a tool error, a
missing effect, a tool that never ran) and the stub's scripted sequence. The
one test that needs Docker is marked `integration` and skips without it.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import urllib.request

import client_container
import client_tool_smoke as mod
import pytest

TOKEN = "abc123def456"

#: OpenCode's real error text from a 2026-10-05 transcript, trimmed.
TAR_ERROR = (
    "tar: ripgrep-15.1.0-x86_64-unknown-linux-musl/doc/CHANGELOG.md: Cannot "
    "change ownership to uid 1001, gid 1001: Operation not permitted\n"
    "tar: ripgrep-15.1.0-x86_64-unknown-linux-musl/doc/FAQ.md: Cannot change "
    "ownership to uid 1001, gid 1001: Operation not permitted\n"
)


def _event(tool: str, status: str, output: str = "", error: str = "", n: int = 0):
    state = {"status": status, "input": {}}
    if status == "completed":
        state["output"] = output
    else:
        state["error"] = error
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1791238661670 + n,
            "part": {
                "type": "tool",
                "tool": tool,
                "callID": f"call_{n}",
                "state": state,
            },
        }
    )


def _healthy_outputs(workdir: pathlib.Path) -> dict[str, str]:
    return {
        "bash": "",
        "read": f"<content>\n1: NEEDLE = '{TOKEN}'\n</content>",
        "write": "Wrote file successfully.",
        "edit": "Edit applied successfully.",
        "grep": f"Found 1 matches\n{workdir}/needle.py:\n  Line 1: NEEDLE",
        "glob": f"{workdir}/deep/nested/planted.marker",
        "todowrite": json.dumps([{"content": f"smoke {TOKEN}"}]),
        "skill": f'<skill_content name="tool-smoke">Skill body {TOKEN}.',
        "webfetch": mod.page_text(TOKEN),
    }


def _apply_effects(workdir: pathlib.Path) -> None:
    """What a working client leaves on disk after the script."""
    (workdir / "bash-wrote.txt").write_text(TOKEN)
    (workdir / "written.txt").write_text(f"written {TOKEN}\n")
    (workdir / "edit_me.txt").write_text(f"after {TOKEN}\n")


def _transcript(workdir: pathlib.Path, broken: dict[str, str] | None = None) -> str:
    broken = broken or {}
    outputs = _healthy_outputs(workdir)
    lines = ['{"type": "step_start", "timestamp": 1}']
    for n, tool in enumerate(mod.TOOLS):
        if tool in broken:
            lines.append(_event(tool, "error", error=broken[tool], n=n))
        else:
            lines.append(_event(tool, "completed", output=outputs[tool], n=n))
    return "\n".join(lines) + "\n"


@pytest.fixture
def workdir(tmp_path):
    mod.plant(tmp_path, TOKEN)
    return tmp_path


def test_the_script_covers_every_tool_the_trials_use():
    """The 2026-09-22..10-06 audit: these are the tools trials called."""
    assert set(mod.TOOLS) >= {
        "bash",
        "read",
        "write",
        "edit",
        "grep",
        "glob",
        "todowrite",
    }
    assert len(mod.TOOLS) == len(set(mod.TOOLS)), "one call per tool"


def test_an_all_completed_transcript_with_every_effect_passes(workdir):
    _apply_effects(workdir)
    script = mod.steps(workdir, TOKEN)
    assert mod.check(_transcript(workdir), workdir, script) == []


def test_the_ripgrep_failure_fails_and_names_grep_and_glob(workdir):
    """The #968 transcript: grep and glob end in `error` with tar's text."""
    _apply_effects(workdir)
    script = mod.steps(workdir, TOKEN)
    text = _transcript(workdir, broken={"grep": TAR_ERROR, "glob": TAR_ERROR})
    failures = mod.check(text, workdir, script)
    assert [f.split(":")[0] for f in failures] == ["grep", "glob"]
    for failure in failures:
        assert "Cannot change ownership" in failure
        assert "(+1 more lines)" in failure


def test_a_completed_call_whose_file_never_changed_fails(workdir):
    """`completed` is OpenCode's word. The file on disk is the evidence."""
    _apply_effects(workdir)
    (workdir / "written.txt").unlink()
    (workdir / "edit_me.txt").write_text(f"before {TOKEN}\n")
    script = mod.steps(workdir, TOKEN)
    failures = mod.check(_transcript(workdir), workdir, script)
    assert len(failures) == 2
    assert failures[0].startswith("write: completed, but written.txt")
    assert failures[1].startswith("edit: completed, but edit_me.txt holds")


def test_a_grep_that_completes_without_the_planted_file_fails(workdir):
    _apply_effects(workdir)
    script = mod.steps(workdir, TOKEN)
    text = _transcript(workdir).replace(f"{workdir}/needle.py", "No files found")
    failures = mod.check(text, workdir, script)
    assert len(failures) == 1 and failures[0].startswith("grep: completed, but")


def test_a_tool_that_never_ran_fails(workdir):
    _apply_effects(workdir)
    script = mod.steps(workdir, TOKEN)
    text = "\n".join(
        ln for ln in _transcript(workdir).splitlines() if '"tool": "glob"' not in ln
    )
    assert mod.check(text, workdir, script) == [
        "glob: never ran (no tool part in the transcript)"
    ]


def test_an_empty_transcript_fails_every_tool(workdir):
    script = mod.steps(workdir, TOKEN)
    failures = mod.check("", workdir, script)
    assert [f.split(":")[0] for f in failures] == list(mod.TOOLS)


def test_an_unexpected_call_fails(workdir):
    """OpenCode reports an unknown or malformed call as tool `invalid`."""
    _apply_effects(workdir)
    script = mod.steps(workdir, TOKEN)
    text = _transcript(workdir) + _event(
        "invalid", "completed", output="The arguments provided were invalid", n=99
    )
    failures = mod.check(text, workdir, script)
    assert len(failures) == 1 and failures[0].startswith("invalid: unexpected call")


def test_the_last_state_of_a_call_wins():
    running = json.dumps(
        {
            "part": {
                "type": "tool",
                "tool": "grep",
                "callID": "c1",
                "state": {"status": "running"},
            }
        }
    )
    done = json.dumps(
        {
            "part": {
                "type": "tool",
                "tool": "grep",
                "callID": "c1",
                "state": {"status": "completed", "output": "x"},
            }
        }
    )
    parts = mod.tool_parts(f"{running}\nnot json\n{done}\n")
    assert parts == [mod.ToolPart("grep", "completed", "x", "")]


# --- the stub's scripted sequence -------------------------------------------


def _body(n_results: int, tools: bool = True) -> dict:
    messages: list[dict[str, str | None]] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    for i in range(n_results):
        messages.append({"role": "assistant", "content": None})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r"})
    body: dict = {"model": mod.MODEL, "messages": messages, "stream": True}
    if tools:
        body["tools"] = [
            {"type": "function", "function": {"name": t}} for t in mod.TOOLS
        ]
    return body


def test_the_stub_sends_one_scripted_call_per_tool_result_then_stops(tmp_path):
    script = mod.steps(tmp_path, TOKEN)
    sent = []
    for n in range(len(script) + 1):
        reply = mod.next_reply(_body(n), script)
        if "call" not in reply:
            break
        sent.append(reply["call"]["name"])
        assert json.loads(reply["call"]["arguments"]) == script[n].args
    assert sent == list(mod.TOOLS)
    assert "text" in mod.next_reply(_body(len(script)), script)


def test_a_failed_call_still_advances_the_script(tmp_path):
    """A failing grep returns a tool result too, so glob still gets its turn."""
    script = mod.steps(tmp_path, TOKEN)
    grep = mod.TOOLS.index("grep")
    reply = mod.next_reply(_body(grep + 1), script)
    assert reply["call"]["name"] == mod.TOOLS[grep + 1]


def test_a_request_without_tools_gets_text(tmp_path):
    """OpenCode asks for a session title with no tools offered."""
    script = mod.steps(tmp_path, TOKEN)
    assert "text" in mod.next_reply(_body(0, tools=False), script)


def test_a_streamed_call_is_one_tool_call_delta_then_a_finish():
    reply = {"call": {"id": "call_1", "name": "grep", "arguments": '{"pattern": "x"}'}}
    first, last = mod.stream_chunks(reply)
    call = first["choices"][0]["delta"]["tool_calls"][0]
    assert call == {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "grep", "arguments": '{"pattern": "x"}'},
    }
    assert first["choices"][0]["finish_reason"] is None
    assert last["choices"][0]["finish_reason"] == "tool_calls"
    assert last["usage"]["total_tokens"] > 0


def test_streamed_text_finishes_with_stop():
    first, last = mod.stream_chunks({"text": "done"})
    assert first["choices"][0]["delta"]["content"] == "done"
    assert last["choices"][0]["finish_reason"] == "stop"


def test_a_non_streamed_call_carries_the_same_tool_call():
    reply = {"call": {"id": "call_1", "name": "glob", "arguments": "{}"}}
    got = mod.completion(reply)
    assert got["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "glob"
    assert got["choices"][0]["finish_reason"] == "tool_calls"


def test_the_stub_serves_the_script_over_http(tmp_path):
    """The real server, on loopback: SSE frames, then [DONE]."""
    script = mod.steps(tmp_path, TOKEN)
    with mod.StubServer(script) as stub:
        req = urllib.request.Request(
            stub.base_url + "/chat/completions",
            data=json.dumps(_body(0)).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
        assert stub.httpd.requests == 1
        assert stub.httpd.offered == set(mod.TOOLS)
    frames = [ln[len("data: ") :] for ln in raw.splitlines() if ln.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    first = json.loads(frames[0])
    assert first["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "bash"


def test_the_stub_serves_the_page_webfetch_reads(tmp_path):
    with mod.StubServer(page=mod.page_text(TOKEN)) as stub:
        script = mod.steps(tmp_path, TOKEN, stub.base_url)
        url = next(s.args["url"] for s in script if s.tool == "webfetch")
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode()
    assert TOKEN in body


def test_the_provider_config_points_at_the_stub():
    cfg = mod.opencode_config("http://127.0.0.1:1234/v1")
    provider = cfg["provider"][mod.PROVIDER]
    assert provider["options"]["baseURL"] == "http://127.0.0.1:1234/v1"
    assert mod.MODEL in provider["models"]


# --- the container half -----------------------------------------------------


def test_the_self_test_runs_in_a_trials_container(tmp_path):
    """Same privileges, memory limit, user and mount targets as a batch."""
    for rel, _ in client_container.MOUNTS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "worktree"
    argv = mod.smoke_argv("img", home=tmp_path, mem_limit_gib=12.0, repo=repo)
    trial = client_container.docker_argv(
        image="img",
        home=tmp_path,
        server="srv",
        facts=tmp_path / "facts.json",
        command=["--backend", "b"],
    )
    image_at = argv.index("img")
    assert argv[:image_at][: trial.index("-v")] == trial[: trial.index("-v")]
    joined = " ".join(argv)
    assert " ".join(client_container.PRIVILEGES) in joined
    assert "--memory 12288m --memory-swap 12288m" in joined
    assert f"-v {repo}:/root/git/local-llm" in joined
    assert "HOME=/root" in joined
    assert argv[image_at + 1 :][:3] == ["uv", "run", "--no-project"]
    assert "--inside" in argv


def test_a_mount_missing_on_the_host_is_left_out_not_created(tmp_path):
    """Docker would create it root-owned on the host."""
    argv = mod.smoke_argv("img", home=tmp_path, mem_limit_gib=None, repo=tmp_path)
    joined = " ".join(argv)
    assert "/root/bench-logs" not in joined
    assert ":/root/git/local-llm" in joined


def test_the_result_line_is_found_after_log_lines():
    out = "log\n" + mod.RESULT_PREFIX + json.dumps({"failures": ["grep: x"]}) + "\n"
    assert mod.parse_result(out) == {"failures": ["grep: x"]}
    assert mod.parse_result("log only\n") is None


def test_a_container_that_reports_nothing_is_a_failure(monkeypatch, tmp_path):
    def fake(argv, **_):
        return subprocess.CompletedProcess(argv, 125, "", "docker: no such image")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    failures = mod.run_in_image("img", home=tmp_path)
    assert len(failures) == 1
    assert "exited 125 without a result" in failures[0]
    assert "no such image" in failures[0]


def test_the_containers_failures_are_returned(monkeypatch, tmp_path):
    result = {"failures": ["grep: error: tar"], "seconds": 4.2, "mechanism": "bwrap"}

    def fake(argv, **_):
        return subprocess.CompletedProcess(
            argv, 1, mod.RESULT_PREFIX + json.dumps(result) + "\n", ""
        )

    monkeypatch.setattr(mod.subprocess, "run", fake)
    assert mod.run_in_image("img", home=tmp_path) == ["grep: error: tar"]


def _docker_image(tag: str) -> bool:
    if not shutil.which("docker"):
        return False
    got = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, check=False
    )
    return got.returncode == 0


@pytest.mark.integration
def test_the_pinned_image_passes_the_self_test():
    """Real Docker, real OpenCode, the trial sandbox. Seconds, no GPU."""
    image = client_container.DEFAULT_IMAGE
    if not _docker_image(image):
        pytest.skip(f"docker or {image} is not available here")
    assert mod.run_in_image(image) == []
