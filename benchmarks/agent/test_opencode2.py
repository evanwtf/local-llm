"""OpenCode 2.x: argv, the session export, and the final-step tokens (#1086).

The fixtures are trimmed from a real OpenCode 2.0.29 run against Nemotron on
TensorFold 1.0.11 (2026-10-14): three assistant steps, of which the JSON
stream reported two. The third step's `step_finish` is missing, which is
anomalyco/opencode#53207; the session export has all three.
"""

from __future__ import annotations

import json
import subprocess

import pytest
import run

STREAM_2_0_29 = "\n".join(
    json.dumps(e)
    for e in [
        {"type": "step_start", "timestamp": 1792015036032, "sessionID": "ses_T"},
        {
            "type": "tool_use",
            "timestamp": 1792015036391,
            "sessionID": "ses_T",
            "part": {"type": "tool", "tool": "write"},
        },
        {
            "type": "step_finish",
            "timestamp": 1792015036423,
            "sessionID": "ses_T",
            "part": {
                "reason": "tool-calls",
                "tokens": {"input": 36, "output": 104, "reasoning": 0},
            },
        },
        {"type": "step_start", "timestamp": 1792015036675, "sessionID": "ses_T"},
        {
            "type": "tool_use",
            "timestamp": 1792015037016,
            "sessionID": "ses_T",
            "part": {"type": "tool", "tool": "write"},
        },
        {
            "type": "step_finish",
            "timestamp": 1792015037041,
            "sessionID": "ses_T",
            "part": {
                "reason": "tool-calls",
                "tokens": {"input": 134, "output": 95, "reasoning": 0},
            },
        },
        {"type": "step_start", "timestamp": 1792015037143, "sessionID": "ses_T"},
        {
            "type": "text",
            "timestamp": 1792015037458,
            "sessionID": "ses_T",
            "part": {"type": "text", "text": "Done!"},
        },
    ]
)


def export_for(directory: str) -> dict:
    """The session export of the same run, trimmed to what the harness reads."""

    def step(finish: str, inp: int, out: int) -> dict:
        return {
            "type": "assistant",
            "finish": finish,
            "tokens": {
                "input": inp,
                "output": out,
                "reasoning": 0,
                "cache": {"read": 5861, "write": 0},
            },
        }

    return {
        "info": {"id": "ses_T", "location": {"directory": directory}},
        "messages": [
            {"type": "user", "text": "Implement add"},
            step("tool-calls", 36, 104),
            step("tool-calls", 134, 95),
            step("stop", 124, 46),
            {"type": "idle", "outcome": "succeeded"},
        ],
    }


BACKEND = {"model": "x", "opencode_model": "vllm/nemotron"}


# --- argv ---------------------------------------------------------------------


def test_2x_argv_is_standalone_and_has_no_dir(tmp_path) -> None:
    argv = run.opencode_argv({"prompt": "go"}, BACKEND, tmp_path, major=2)
    assert argv[:3] == ["opencode", "run", "--standalone"]
    assert "--dir" not in argv
    assert argv[-1] == "go"


def test_2x_still_refuses_without_a_worktree() -> None:
    """The cwd is now the thing that must not default (#67)."""
    with pytest.raises(SystemExit, match="worktree"):
        run.opencode_argv({"prompt": "go"}, BACKEND, None, major=2)


def test_1x_argv_keeps_dir(tmp_path) -> None:
    argv = run.opencode_argv({"prompt": "go"}, BACKEND, tmp_path, major=1)
    assert argv[argv.index("--dir") + 1] == str(tmp_path)
    assert "--standalone" not in argv


HELP_2_0_29 = """
FLAGS
  --standalone            Run with a private server instead of the background service
  --server string         Connect to a server URL instead of the background service
  --model, -m string      Model to use in the format provider/model#variant
  --format choice         Output format (choices: default, json)
  --auto                  Auto-approve permissions that are not explicitly denied
"""


def test_the_2_0_29_help_satisfies_the_2x_contract() -> None:
    assert run.opencode_cli_gaps(HELP_2_0_29, major=2) == []


def test_the_2_0_29_help_fails_the_1x_contract() -> None:
    """The #263 gate would have refused 2.0.29 under the 1.x argv: no --dir."""
    assert run.opencode_cli_gaps(HELP_2_0_29, major=1) == ["--dir"]


def test_a_2x_help_without_standalone_is_refused() -> None:
    help_text = HELP_2_0_29.replace("--standalone", "--private")
    assert run.opencode_cli_gaps(help_text, major=2) == ["--standalone"]


def test_a_2x_help_without_json_is_refused() -> None:
    help_text = HELP_2_0_29.replace("default, json", "default, ndjson")
    assert run.opencode_cli_gaps(help_text, major=2) == ['--format "json"']


def test_the_gate_uses_the_installed_major(monkeypatch) -> None:
    monkeypatch.setattr(run, "_opencode_run_help", lambda: HELP_2_0_29)
    monkeypatch.setattr(run, "installed_opencode_major", lambda: 2)
    assert run.opencode_cli_gate(["opencode"]) is None
    monkeypatch.setattr(run, "installed_opencode_major", lambda: 1)
    why = run.opencode_cli_gate(["opencode"])
    assert why is not None and "--dir" in why


# --- the final step (#53207) ----------------------------------------------------


def test_the_stream_alone_undercounts_the_2x_run() -> None:
    row = run.opencode_parse(STREAM_2_0_29)
    assert row["num_turns"] == 2
    assert row["output_tokens"] == 199
    assert "token_source" not in row


def test_the_export_supplies_the_missing_final_step() -> None:
    row = run.opencode_parse(STREAM_2_0_29, export=export_for("/w"))
    assert row["num_turns"] == 3
    assert row["output_tokens"] == 104 + 95 + 46
    assert row["input_tokens"] == 134
    assert row["token_source"] == "export"


def test_a_complete_stream_keeps_its_own_counts() -> None:
    export = export_for("/w")
    export["messages"] = export["messages"][:3]  # two steps, as the stream has
    row = run.opencode_parse(STREAM_2_0_29, export=export)
    assert row["num_turns"] == 2
    assert row["token_source"] == "stream"


def test_ttft_still_comes_from_the_stream() -> None:
    row = run.opencode_parse(
        STREAM_2_0_29, launched_ms=1792015035000, export=export_for("/w")
    )
    assert row["first_content_ms"] == 1792015036391 - 1792015035000


def test_a_text_only_2x_run_parses_from_the_export() -> None:
    """2.0.29 printed no step_finish at all for a one-step reply."""
    stream = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "step_start", "timestamp": 1, "sessionID": "ses_T"},
            {"type": "text", "timestamp": 2, "sessionID": "ses_T", "part": {}},
        ]
    )
    with pytest.raises(json.JSONDecodeError):
        run.opencode_parse(stream)
    export = export_for("/w")
    export["messages"] = [export["messages"][3]]
    assert run.opencode_parse(stream, export=export)["num_turns"] == 1


# --- the export and the worktree guard -----------------------------------------


def test_the_session_id_is_read_from_the_stream() -> None:
    assert run.opencode_session_id("noise\n" + STREAM_2_0_29) == "ses_T"
    assert run.opencode_session_id("") is None


def test_a_session_in_another_directory_is_a_harness_fault(tmp_path) -> None:
    why = run.opencode_session_check(export_for("/somewhere/else"), tmp_path)
    assert why is not None and "not the trial worktree" in why


def test_a_session_in_the_worktree_passes(tmp_path) -> None:
    assert run.opencode_session_check(export_for(str(tmp_path)), tmp_path) is None


def test_a_missing_export_proves_nothing(tmp_path) -> None:
    assert run.opencode_session_check(None, tmp_path) is None


def test_the_export_call_is_standalone(monkeypatch, tmp_path) -> None:
    """A call without --standalone starts a daemon that outlives the trial."""
    seen: dict = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["cwd"] = argv, kw.get("cwd")
        return subprocess.CompletedProcess(argv, 0, json.dumps(export_for("/w")), "")

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    got = run.opencode_session_export(STREAM_2_0_29, tmp_path)
    assert seen["argv"] == ["opencode", "session", "export", "--standalone", "ses_T"]
    assert seen["cwd"] == tmp_path
    assert got is not None and len(run.opencode_export_steps(got)) == 3


def test_a_failed_export_is_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        run.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"),
    )
    assert run.opencode_session_export(STREAM_2_0_29, tmp_path) is None


# --- #1149: a move the model made itself is its own failure --------------------


def _tool_event(tool: str, inp: dict) -> str:
    """One OpenCode 2.x `tool_use` event line, trimmed from #1149's transcript."""
    part = {
        "type": "tool",
        "tool": tool,
        "state": {"status": "completed", "input": inp},
    }
    return json.dumps({"type": "tool_use", "part": part})


#: The two calls replay-web-auth #1 made on 2026-10-18 (#1149), through the
#: code-mode `execute` tool, before every edit went to the new worktree.
MOVE_STREAM = "\n".join(
    [
        _tool_event("read", {"path": "/w/app.py"}),
        _tool_event(
            "execute", {"code": 'tools.opencode.worktree_create({"name": "x"})'}
        ),
        _tool_event("execute", {"code": 'tools.opencode.session_move({"to": "x"})'}),
        _tool_event("edit", {"path": "/root/.local/share/opencode/worktree/9b/x/a.py"}),
    ]
)


def test_a_model_that_moved_its_session_is_named() -> None:
    why = run.opencode_model_moved(MOVE_STREAM)
    assert why is not None
    assert "worktree_create" in why and "session_move" in why


def test_a_stream_without_move_calls_names_nothing() -> None:
    stream = _tool_event("execute", {"code": "tools.opencode.read({})"})
    assert run.opencode_model_moved(stream) is None
    assert run.opencode_model_moved("") is None


def test_a_direct_move_tool_counts_too() -> None:
    assert (
        run.opencode_model_moved(_tool_event("session_move", {"to": "x"})) is not None
    )


def test_noise_lines_do_not_break_the_scan() -> None:
    assert run.opencode_model_moved("not json\n" + MOVE_STREAM) is not None
