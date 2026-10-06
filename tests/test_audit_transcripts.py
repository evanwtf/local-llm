"""Tests for the post-run transcript audit (#969).

#968 ran for two weeks unseen: every OpenCode `grep` and `glob` call failed,
in more than 5,000 calls, and nothing read the transcripts. The audit reads
them. Its job is to refuse a batch with a harness fault, so the tests weight
the cases that must fail, and the case that must NOT fail (a model that
writes raw tool-call markup is a model result, not a broken client).

The fixtures are synthetic and small. Their event shape copies the live
corpus: a tool call is a `tool_use` event whose `part.type` is `tool`, with
`part.state.status` and `part.state.error`; assistant text is a `text` event
whose `part.type` is `text`; a session failure is a top-level `error` event.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import audit_transcripts as audit

#: The #968 error, verbatim from a Core i3-7100 client transcript (two lines
#: of the many `tar` prints).
RIPGREP_ERROR = (
    "tar: ripgrep-14.1.1-x86_64-unknown-linux-musl/doc/CHANGELOG.md: Cannot "
    "change ownership to uid 1000, gid 1000: Operation not permitted\n"
    "tar: ripgrep-14.1.1-x86_64-unknown-linux-musl/doc/FAQ.md: Cannot change "
    "ownership to uid 1000, gid 1000: Operation not permitted"
)

T0 = 1_790_409_961_000  # ms, a real-shaped OpenCode timestamp


def tool(
    name: str,
    status: str = "completed",
    error: str | None = None,
    at: int = 0,
) -> dict[str, Any]:
    state: dict[str, Any] = {"status": status, "input": {}}
    if error is not None:
        state["error"] = error
    return {
        "type": "tool_use",
        "timestamp": T0 + at * 1000,
        "sessionID": "ses_x",
        "part": {"type": "tool", "tool": name, "callID": f"c{at}", "state": state},
    }


def text(body: str, at: int = 0) -> dict[str, Any]:
    return {
        "type": "text",
        "timestamp": T0 + at * 1000,
        "sessionID": "ses_x",
        "part": {"type": "text", "text": body},
    }


def step(at: int) -> dict[str, Any]:
    return {
        "type": "step_start",
        "timestamp": T0 + at * 1000,
        "part": {"type": "step-start"},
    }


def session_error(name: str, message: str, at: int = 0, **data: Any) -> dict[str, Any]:
    return {
        "type": "error",
        "timestamp": T0 + at * 1000,
        "sessionID": "ses_x",
        "error": {"name": name, "data": {"message": message, **data}},
    }


def stem(
    task: str = "replay-web-auth",
    backend: str = "glm53arm",
    trial: int = 1,
    stamp: str = "20261005T120000Z",
) -> str:
    return f"{task}-{backend}-opencode-{trial}-1.18.34-f239e51-{stamp}"


def write(
    directory: pathlib.Path,
    events: list[dict[str, Any]],
    name: str | None = None,
) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name or stem()}.stdout.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def clean_events() -> list[dict[str, Any]]:
    return [
        step(0),
        tool("read", at=5),
        tool("edit", at=20),
        tool("bash", at=60),
        text("All tests pass.", at=90),
    ]


def report_for(paths: list[pathlib.Path], **kw: Any) -> dict[str, Any]:
    trials = [audit.load_trial(p, backend="b") for p in paths]
    return audit.audit_group("b", trials, audit.Thresholds(**kw))


# --- the four cases #969 names ------------------------------------------------


def test_the_968_ripgrep_error_is_a_harness_fault(tmp_path: pathlib.Path) -> None:
    events = [step(0)] + [
        tool(name, "error", RIPGREP_ERROR, at=i + 1)
        for i, name in enumerate(["grep", "glob"] * 5)
    ]
    events.append(tool("bash", at=40))
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is True
    assert set(report["harness_fault_tools"]) == {"grep", "glob"}
    assert report["harness_signatures"]["tar"] == 10
    # The ripgrep failures are the client's, never the model's.
    assert report["model_tool_errors"] == 0


def test_one_ripgrep_error_is_a_fault_even_below_the_call_floor(
    tmp_path: pathlib.Path,
) -> None:
    """The signature alone is enough: a client error is never the model's."""
    events = [step(0), tool("glob", "error", RIPGREP_ERROR, at=1), tool("bash", at=50)]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is True
    assert report["harness_fault_tools"] == []
    assert report["harness_signatures"] == {"tar": 1}


def test_raw_tool_call_markup_is_a_format_break_not_a_fault(
    tmp_path: pathlib.Path,
) -> None:
    events = clean_events()
    events.append(
        text(
            "Done.<arg_key>filePath</arg_key><arg_value>src/a.py</arg_value>"
            "</tool_call>",
            at=95,
        )
    )
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is False
    assert report["format_break_trials"] == 1
    assert report["format_break_markup"]["</arg_value>"] == 1
    assert report["format_break_markup"]["</tool_call>"] == 1


def test_a_clean_transcript_passes(tmp_path: pathlib.Path) -> None:
    report = report_for([write(tmp_path, clean_events())])
    assert report["harness_fault"] is False
    assert report["faults"] == []
    assert report["format_break_trials"] == 0
    assert report["model_tool_errors"] == 0
    assert report["tool_calls"] == 3


def test_the_client_log_join_ignores_a_similar_transcript_from_another_batch(
    tmp_path: pathlib.Path,
) -> None:
    """The row names its file; a #112 collision sibling must not be read."""
    logs_dir = tmp_path / "bench-logs"
    ours = write(logs_dir, clean_events())
    # Same trial name, another sweep: run.py's #112 guard wrote `.stdout.2`.
    theirs = logs_dir / f"{stem()}.stdout.2.jsonl"
    theirs.write_text(json.dumps(tool("grep", "error", RIPGREP_ERROR, at=1)) + "\n")
    # And the same filename in another batch's directory.
    write(tmp_path / "other-batch", [tool("glob", "error", RIPGREP_ERROR, at=1)])
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "task": "replay-web-auth",
                "backend": "glm53arm",
                "client": "opencode",
                "started": "2026-10-05T12:00:00+0000",
                "client_log": str(ours),
            }
        )
        + "\n"
    )
    out = tmp_path / "audit.json"
    code = audit.main(["--results", str(ledger), "--json", str(out)])
    assert code == 0
    summary = json.loads(out.read_text())
    assert [t["path"] for t in summary["trials"]] == [str(ours)]


# --- harness faults ------------------------------------------------------------


def test_a_tool_that_always_fails_is_a_fault_at_the_call_floor(
    tmp_path: pathlib.Path,
) -> None:
    events = [step(0)] + [
        tool("webfetch", "error", "some new failure", at=i + 1) for i in range(5)
    ]
    events.append(tool("bash", at=60))
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault_tools"] == ["webfetch"]
    assert report["harness_fault"] is True


def test_below_the_call_floor_a_failing_tool_is_not_a_fault(
    tmp_path: pathlib.Path,
) -> None:
    events = [step(0)] + [
        tool("webfetch", "error", "some new failure", at=i + 1) for i in range(4)
    ]
    events.append(tool("bash", at=60))
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault_tools"] == []
    assert report["harness_fault"] is False


def test_the_rate_floor_is_ninety_percent(tmp_path: pathlib.Path) -> None:
    """9 errors in 10 calls is a fault; 8 in 10 is not."""

    def run(errors: int) -> list[str]:
        events = [step(0)]
        events += [tool("read", "error", "odd", at=i + 1) for i in range(errors)]
        events += [tool("read", at=20 + i) for i in range(10 - errors)]
        events.append(tool("bash", at=60))
        d = tmp_path / str(errors)
        report = report_for([write(d, events)])
        return list(report["harness_fault_tools"])

    assert run(9) == ["read"]
    assert run(8) == []


@pytest.mark.parametrize(
    "error",
    [
        "EACCES: permission denied, open '/root/.local/share/opencode/x'",
        "/bin/sh: 1: rg: command not found",
        "spawn rg ENOENT",
        "connect ECONNREFUSED 127.0.0.1:8030",
        "read ECONNRESET",
        "Connection reset by peer",
        "UnknownError: Unexpected server error",
    ],
)
def test_client_and_environment_signatures_are_harness_faults(
    tmp_path: pathlib.Path, error: str
) -> None:
    events = [step(0), tool("bash", "error", error, at=1), tool("read", at=50)]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is True, error
    assert report["model_tool_errors"] == 0


def test_a_webfetch_connection_error_is_the_internet_not_the_harness(
    tmp_path: pathlib.Path,
) -> None:
    """webfetch reaches outside sites, so its network errors are not ours."""
    events = [
        step(0),
        tool(
            "webfetch", "error", "getaddrinfo example.com: connect ECONNREFUSED", at=1
        ),
        tool("webfetch", "error", "StatusCode: non 2xx status code (503 GET x)", at=2),
        tool("bash", at=50),
    ]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is False
    assert report["model_tool_errors"] == 2


def test_an_unknown_error_event_is_a_harness_fault(tmp_path: pathlib.Path) -> None:
    events = clean_events() + [
        session_error(
            "UnknownError", "Unexpected server error. Check server logs.", at=99
        )
    ]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is True
    assert report["session_errors"] == {"UnknownError": 1}


def test_a_model_server_that_cannot_be_reached_is_a_harness_fault(
    tmp_path: pathlib.Path,
) -> None:
    events = clean_events() + [
        session_error(
            "APIError",
            "Cannot connect to API: Unable to connect. Is the computer able to "
            "access the url?",
            at=99,
            isRetryable=True,
        )
    ]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is True
    assert report["harness_signatures"] == {"connection": 1}


def test_a_5xx_from_the_model_server_is_a_harness_fault(
    tmp_path: pathlib.Path,
) -> None:
    events = clean_events() + [
        session_error("APIError", "Internal Server Error", at=99, statusCode=500)
    ]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is True


def test_a_context_overflow_is_reported_but_is_not_a_harness_fault(
    tmp_path: pathlib.Path,
) -> None:
    events = clean_events() + [
        session_error(
            "ContextOverflowError",
            "Bad Request: The input (758407 tokens) is longer than the model's "
            "context length (262144 tokens).",
            at=99,
        )
    ]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is False
    assert report["session_errors"] == {"ContextOverflowError": 1}


def test_a_trial_that_ends_in_seconds_with_no_tool_call_is_a_fault(
    tmp_path: pathlib.Path,
) -> None:
    events = [step(0), text("I cannot help.", at=3)]
    report = report_for([write(tmp_path, events)])
    assert report["harness_fault"] is True
    assert report["quick_exits"] == 1


def test_an_empty_transcript_is_a_quick_exit(tmp_path: pathlib.Path) -> None:
    path = tmp_path / f"{stem()}.stdout.jsonl"
    path.write_text("")
    report = report_for([path])
    assert report["quick_exits"] == 1
    assert report["harness_fault"] is True


def test_a_long_trial_with_no_tool_call_is_not_a_quick_exit(
    tmp_path: pathlib.Path,
) -> None:
    events = [step(0), text("thinking out loud", at=300)]
    report = report_for([write(tmp_path, events)])
    assert report["quick_exits"] == 0


# --- format breaks and model errors ---------------------------------------------


def test_an_invalid_tool_call_is_a_format_break_not_a_fault(
    tmp_path: pathlib.Path,
) -> None:
    events = clean_events() + [tool("invalid", at=95)]
    report = report_for([write(tmp_path, events)])
    assert report["invalid_calls"] == 1
    assert report["format_break_trials"] == 1
    assert report["harness_fault"] is False


def test_model_tool_errors_are_counted_per_trial_and_per_100_calls(
    tmp_path: pathlib.Path,
) -> None:
    events = [step(0)]
    events += [tool("bash", at=i + 1) for i in range(18)]
    events.append(
        tool(
            "edit",
            "error",
            "Could not find oldString in the file. It must match exactly.",
            at=30,
        )
    )
    events.append(tool("read", "error", "File not found: /root/x/y.py", at=31))
    report = report_for([write(tmp_path / "a", events), write(tmp_path / "b", events)])
    assert report["tool_calls"] == 40
    assert report["model_tool_errors"] == 4
    assert report["model_tool_errors_per_trial"] == 2.0
    assert report["model_tool_errors_per_100_calls"] == 10.0
    assert report["harness_fault"] is False


def test_a_sandbox_rule_block_is_neither_a_fault_nor_a_model_error(
    tmp_path: pathlib.Path,
) -> None:
    blocked = (
        "The user has specified a rule which prevents you from using this "
        "specific tool call. Here are some of the relevant rules"
    )
    events = [step(0), tool("bash", "error", blocked, at=1), tool("read", at=50)]
    report = report_for([write(tmp_path, events)])
    assert report["sandbox_blocks"] == 1
    assert report["model_tool_errors"] == 0
    assert report["harness_fault"] is False


def test_messages_are_normalized_for_the_top_list() -> None:
    a = audit.normalize_message("File not found: /root/git/a/src/x.py")
    b = audit.normalize_message("File not found: /root/git/b/tests/test_y.py")
    assert a == b == "File not found: <path>"
    first_line = audit.normalize_message(RIPGREP_ERROR)
    assert "\n" not in first_line
    assert audit.normalize_message("Offset 400 is out of range (12 lines)") == (
        "Offset N is out of range (N lines)"
    )


# --- selection ------------------------------------------------------------------


def test_a_container_path_is_read_from_the_host_home(tmp_path: pathlib.Path) -> None:
    """Cluster rows record `/root/bench-logs/...`, the container's path."""
    host_file = write(tmp_path / "bench-logs", clean_events())
    container_path = f"{audit.CONTAINER_HOME}/bench-logs/{host_file.name}"
    assert audit.resolve_client_log(container_path, home=tmp_path) == host_file


def test_the_container_home_matches_the_client_wrapper() -> None:
    import client_container

    assert audit.CONTAINER_HOME == client_container.CONTAINER_HOME


def test_a_missing_transcript_fails_closed(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "backend": "b",
                "client": "opencode",
                "started": "2026-10-05T12:00:00+0000",
                "client_log": str(tmp_path / "gone.stdout.jsonl"),
            }
        )
        + "\n"
    )
    assert audit.main(["--results", str(ledger)]) == 2


def test_a_container_path_missing_on_the_host_fails_closed(
    tmp_path: pathlib.Path,
) -> None:
    """The host's /root is unreadable: that is a missing file, not a crash."""
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "backend": "b",
                "client": "opencode",
                "started": "2026-10-05T12:00:00+0000",
                "client_log": f"{audit.CONTAINER_HOME}/bench-logs/gone.stdout.jsonl",
            }
        )
        + "\n"
    )
    assert audit.main(["--results", str(ledger), "--home", str(tmp_path)]) == 2


def test_ledger_rows_are_filtered_by_backend_and_since(tmp_path: pathlib.Path) -> None:
    logs_dir = tmp_path / "bench-logs"
    keep = write(logs_dir, clean_events(), name=stem(backend="a", trial=1))
    old = write(logs_dir, clean_events(), name=stem(backend="a", trial=2))
    other = write(logs_dir, clean_events(), name=stem(backend="z", trial=1))
    rows = [
        {
            "backend": "a",
            "started": "2026-10-05T12:00:00+0000",
            "client_log": str(keep),
        },
        {"backend": "a", "started": "2026-10-01T12:00:00+0000", "client_log": str(old)},
        {
            "backend": "z",
            "started": "2026-10-05T12:00:00+0000",
            "client_log": str(other),
        },
    ]
    ledger = tmp_path / "results.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    selection = audit.Selection(
        backends=frozenset({"a"}),
        since=audit.parse_when("2026-10-05T00:00:00-0400"),
    )
    picked = audit.select_from_ledgers([ledger], selection, home=tmp_path)
    assert [t.path for t in picked] == [keep]


def test_since_reads_the_filename_stamp_and_never_the_mtime(
    tmp_path: pathlib.Path,
) -> None:
    new = write(tmp_path, clean_events(), name=stem(stamp="20261005T120000Z"))
    old = write(tmp_path, clean_events(), name=stem(trial=2, stamp="20260920T120000Z"))
    # Make the mtimes lie in the opposite direction.
    os.utime(new, (1_600_000_000, 1_600_000_000))
    os.utime(old, (1_900_000_000, 1_900_000_000))
    selection = audit.Selection(since=audit.parse_when("2026-10-01T00:00:00-0400"))
    picked = audit.select_from_dirs(
        [tmp_path], selection, ledger_rows={}, home=tmp_path
    )
    assert [t.path for t in picked] == [new]


def test_the_backend_comes_from_the_filename_when_no_row_names_it() -> None:
    known = frozenset({"glm53arm", "qwen38fnmlxserve-git", "git"})
    assert audit.backend_from_name(stem(backend="glm53arm"), known) == "glm53arm"
    # A hyphenated backend wins over its own tail.
    assert (
        audit.backend_from_name(stem(backend="qwen38fnmlxserve-git"), known)
        == "qwen38fnmlxserve-git"
    )
    # An unknown backend is still the segment before the client.
    assert audit.backend_from_name(stem(backend="newarm"), known) == "newarm"


def test_a_directory_scan_prefers_the_ledger_row_for_backend_and_start(
    tmp_path: pathlib.Path,
) -> None:
    path = write(tmp_path, clean_events(), name=stem(backend="fromname"))
    row = {"backend": "fromrow", "started": "2026-10-02T08:00:00+0000"}
    picked = audit.select_from_dirs(
        [tmp_path],
        audit.Selection(),
        ledger_rows={path.resolve(): row},
        home=tmp_path,
    )
    assert picked[0].backend == "fromrow"
    assert picked[0].joined is True


def test_a_non_opencode_file_is_skipped(tmp_path: pathlib.Path) -> None:
    aider = tmp_path / f"{stem()}.stdout.jsonl"
    aider.write_text("Aider v0.86\nModel: x\n")
    good = write(tmp_path, clean_events(), name=stem(trial=2))
    picked = audit.select_from_dirs(
        [tmp_path], audit.Selection(), ledger_rows={}, home=tmp_path
    )
    assert [t.path for t in picked] == [good]


# --- the command ------------------------------------------------------------------


def test_main_exits_one_on_a_harness_fault_and_zero_on_a_format_break(
    tmp_path: pathlib.Path,
) -> None:
    faulty = tmp_path / "faulty"
    write(
        faulty,
        [step(0), tool("grep", "error", RIPGREP_ERROR, at=1), tool("bash", at=50)],
    )
    assert audit.main(["--logs", str(faulty)]) == 1

    broken = tmp_path / "broken"
    write(broken, clean_events() + [text("</arg_value></tool_call>", at=95)])
    assert audit.main(["--logs", str(broken)]) == 0


def test_main_writes_a_json_summary_per_backend(tmp_path: pathlib.Path) -> None:
    write(tmp_path / "logs", clean_events(), name=stem(backend="a"))
    write(tmp_path / "logs", clean_events(), name=stem(backend="b"))
    out = tmp_path / "audit.json"
    assert audit.main(["--logs", str(tmp_path / "logs"), "--json", str(out)]) == 0
    summary = json.loads(out.read_text())
    assert sorted(summary["backends"]) == ["a", "b"]
    assert summary["harness_fault"] is False
    assert summary["thresholds"]["min_calls"] == 5


def test_main_renders_a_markdown_table(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    for i in range(3):
        write(tmp_path, clean_events(), name=stem(backend="a", trial=i + 1))
    caplog.set_level("INFO")
    assert audit.main(["--logs", str(tmp_path), "--markdown", "--min-trials", "2"]) == 0
    table = [r.getMessage() for r in caplog.records if r.getMessage().startswith("|")]
    assert table[0].startswith("| backend |")
    assert any(line.startswith("| a | 3 |") for line in table)
