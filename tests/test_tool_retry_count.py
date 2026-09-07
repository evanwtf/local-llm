"""Tests for the OpenCode tool-call-outcome counter (#219).

The counter reads the CLIENT side of a trial -- the OpenCode transcript -- so
it can compare llama.cpp against ds4, which the server-log instrument cannot
(only ds4 emits "TOOLS invalid tool call; continuing"). It runs unattended, so
refusing a shape it does not recognise is its whole job: a zero that means
"no errors" and a zero that means "I could not read this" must not be the
same value. The tests weight the negative cases accordingly.

Schema assumption v2 (from the real transcripts): a `tool_use` event is
self-contained, carrying the outcome in `part.state.status` and the tool in
`part.tool`. There is no open/close pairing and no retry marker. The fixtures
here are synthetic, built to that assumption.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from tool_retry_count import (
    NotOpenCodeError,
    ToolCall,
    TranscriptError,
    _expand_inputs,
    count_transcript,
    infer_retries,
    main,
    read_tool_calls,
)


def make_transcript(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def write_transcript(
    tmp_path: pathlib.Path, body: str, name: str = "trial"
) -> pathlib.Path:
    path = tmp_path / f"{name}.stdout.jsonl"
    path.write_text(body)
    return path


def tool_use(tool: str, status: str, call_id: str, input_: object = None) -> dict:
    return {
        "type": "tool_use",
        "timestamp": 1_788_805_673_687,
        "part": {
            "type": "tool",
            "tool": tool,
            "callID": call_id,
            "state": {"status": status, "input": input_, "output": ""},
            "id": f"prt_{call_id}",
        },
    }


def test_empty_transcript_raises(tmp_path) -> None:
    """An empty file is a capture failure, not a trial with no tool calls."""
    path = write_transcript(tmp_path, "")
    with pytest.raises(TranscriptError, match="empty"):
        count_transcript(path)


def test_truncated_transcript_raises(tmp_path) -> None:
    """A line cut mid-object is a truncated capture."""
    path = write_transcript(
        tmp_path,
        make_transcript([tool_use("bash", "completed", "a")])
        + '{"type": "tool_use", "part": {"tool": "read"\n',  # cut mid-object
    )
    with pytest.raises(TranscriptError, match="not valid JSON"):
        count_transcript(path)


def test_not_an_opencode_transcript_raises_distinctly(tmp_path) -> None:
    """A Codex transcript is refused as not-OpenCode, not as corruption."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "thread.started", "id": "t1"},
                {"type": "turn.started", "id": "t1"},
            ]
        ),
    )
    with pytest.raises(NotOpenCodeError, match="not an OpenCode type"):
        count_transcript(path)


def test_bare_json_scalar_raises(tmp_path) -> None:
    """A line that parses to a non-object is refused, not AttributeError."""
    path = write_transcript(
        tmp_path,
        make_transcript([tool_use("bash", "completed", "a")]) + '"just a string"\n',
    )
    with pytest.raises(TranscriptError, match="not a JSON object"):
        count_transcript(path)


def test_not_json_at_all_raises(tmp_path) -> None:
    """A file that is not JSON is refused as corruption."""
    path = write_transcript(tmp_path, "this is not json\nneither is this\n")
    with pytest.raises(TranscriptError, match="not valid JSON"):
        count_transcript(path)


def test_normal_transcript_counts(tmp_path) -> None:
    """Issued, errored and distinct tools are counted separately."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                tool_use("bash", "completed", "a"),
                tool_use("bash", "error", "b"),
                tool_use("read", "completed", "c"),
                tool_use("read", "error", "d"),
                tool_use("read", "completed", "e"),
            ]
        ),
    )
    row = count_transcript(path)
    assert row["issued"] == 5
    assert row["errored"] == 2
    assert row["distinct_tools"] == 2


def test_only_bookkeeping_returns_zero(tmp_path) -> None:
    """A trial that made no tool calls is a valid zero, not a refusal."""
    path = write_transcript(
        tmp_path,
        make_transcript(
            [
                {"type": "step_start", "timestamp": 1_000_000},
                {"type": "text", "timestamp": 1_002_400, "part": {"text": "hi"}},
                {
                    "type": "step_finish",
                    "timestamp": 1_002_500,
                    "part": {"tokens": {"input": 100, "output": 10}},
                },
            ]
        ),
    )
    row = count_transcript(path)
    assert row["issued"] == 0
    assert row["errored"] == 0
    assert row["distinct_tools"] == 0


def test_stem_is_preserved_verbatim(tmp_path) -> None:
    """The join key is the full stem, including the unexplained .N infix."""
    path = write_transcript(
        tmp_path,
        make_transcript([tool_use("bash", "completed", "a")]),
        name="mbox-strip-envelope-qwen38fnds4kimat-opencode-1.stdout.2",
    )
    row = count_transcript(path)
    assert (
        row["transcript"] == "mbox-strip-envelope-qwen38fnds4kimat-opencode-1.stdout.2"
    )


def test_infer_retries_counts_same_tool_and_input_after_error() -> None:
    """An errored call followed by the same tool and input is a retry."""
    calls = [
        ToolCall(tool="bash", status="error", input={"cmd": "ls"}),
        ToolCall(tool="bash", status="completed", input={"cmd": "ls"}),
    ]
    assert infer_retries(calls) == 1


def test_infer_retries_ignores_different_input() -> None:
    """A later call with a different input is not a retry of the error."""
    calls = [
        ToolCall(tool="bash", status="error", input={"cmd": "ls"}),
        ToolCall(tool="bash", status="completed", input={"cmd": "pwd"}),
    ]
    assert infer_retries(calls) == 0


def test_infer_retries_handles_nesting() -> None:
    """A retry nested among other calls is still found, not lost."""
    calls = [
        ToolCall(tool="bash", status="error", input={"cmd": "ls"}),
        ToolCall(tool="read", status="completed", input={"path": "a"}),
        ToolCall(tool="bash", status="completed", input={"cmd": "ls"}),
    ]
    assert infer_retries(calls) == 1


def test_infer_retries_does_not_double_count() -> None:
    """One error retried once counts once, even with two later matches."""
    calls = [
        ToolCall(tool="bash", status="error", input={"cmd": "ls"}),
        ToolCall(tool="bash", status="completed", input={"cmd": "ls"}),
        ToolCall(tool="bash", status="completed", input={"cmd": "ls"}),
    ]
    assert infer_retries(calls) == 1


def test_read_tool_calls_returns_parsed_calls(tmp_path) -> None:
    """The parser exposes the parsed calls for the retry heuristic."""
    path = write_transcript(
        tmp_path,
        make_transcript([tool_use("bash", "error", "a", {"cmd": "ls"})]),
    )
    calls = read_tool_calls(path)
    assert len(calls) == 1
    assert calls[0].tool == "bash"
    assert calls[0].status == "error"
    assert calls[0].input == {"cmd": "ls"}


def test_expand_inputs_globs_all_four_shapes(tmp_path) -> None:
    """A directory contributes every transcript shape, not just the first.

    A glob of only `*.stdout.jsonl` would silently drop the collision-guard
    (`.2`, `.3`) and partial shapes. This is the regression the peer caught:
    the old glob saw 30 of 69 files in the live slot-2 directory.
    """
    body = make_transcript([tool_use("bash", "completed", "a")])
    for name in ("foo.stdout", "foo.stdout.2", "foo.stdout.3", "foo.stdout.partial"):
        (tmp_path / f"{name}.jsonl").write_text(body)
    paths = _expand_inputs([str(tmp_path)])
    assert [p.name for p in paths] == [
        "foo.stdout.2.jsonl",
        "foo.stdout.3.jsonl",
        "foo.stdout.jsonl",
        "foo.stdout.partial.jsonl",
    ]


def test_partial_transcript_is_marked(tmp_path) -> None:
    """A partial transcript admits it, so a caller cannot average it in."""
    path = tmp_path / "foo.stdout.partial.jsonl"
    path.write_text(make_transcript([tool_use("bash", "completed", "a")]))
    row = count_transcript(path)
    assert row["transcript"] == "foo.stdout.partial"
    assert row["partial"] is True


def test_complete_transcript_is_not_marked(tmp_path) -> None:
    """A complete transcript carries no partial field."""
    path = write_transcript(
        tmp_path, make_transcript([tool_use("bash", "completed", "a")])
    )
    row = count_transcript(path)
    assert "partial" not in row


def test_directory_yields_row_per_non_partial_shape(tmp_path) -> None:
    """A directory with all four shapes yields a row for each, partial marked.

    The fixture uses the exact filenames the corpus produces, so a glob that
    only matches the first shape cannot pass this test.
    """
    d = tmp_path / "shapes"
    d.mkdir()
    body = make_transcript([tool_use("bash", "completed", "a")])
    for name in ("foo.stdout", "foo.stdout.2", "foo.stdout.3", "foo.stdout.partial"):
        (d / f"{name}.jsonl").write_text(body)
    out = tmp_path / "rows.jsonl"
    with pytest.raises(SystemExit) as exc:
        main([str(d), "--out", str(out)])
    assert exc.value.code == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["transcript"] for r in rows] == [
        "foo.stdout.2",
        "foo.stdout.3",
        "foo.stdout",
        "foo.stdout.partial",
    ]
    for r in rows:
        if r["transcript"] == "foo.stdout.partial":
            assert r["partial"] is True
        else:
            assert "partial" not in r
