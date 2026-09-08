"""Failure-mode classification of the #39 MTP-arm deaths (#39).

The #39 pass gap is 21/30 (MTP) vs 28/30 (control). These tests pin the
classifier that labels what a death failed on: a malformed tool call the
harness could not parse (reason=unknown then stop), a stop with no final
answer, or something else. The numbers (9 MTP deaths, 2 control) are
re-derived from the transcripts, never inherited.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mtp_failure_modes as mfm


def _transcript(path: pathlib.Path, lines: list[dict]) -> pathlib.Path:
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return path


def _step_finish(reason: str) -> dict:
    return {"type": "step_finish", "part": {"reason": reason}}


def _text(s: str) -> dict:
    return {"type": "text", "part": {"text": s}}


def _tool(name: str) -> dict:
    return {
        "type": "tool_use",
        "part": {"tool": name, "state": {"status": "completed"}},
    }


def test_parse_transcript_counts_steps_tools_reasons_and_last_text(
    tmp_path: pathlib.Path,
) -> None:
    p = _transcript(
        tmp_path / "t.jsonl",
        [
            {"type": "step_start"},
            _text("let me look"),
            _tool("read"),
            _step_finish("tool-calls"),
            _text("final answer"),
            _step_finish("stop"),
        ],
    )
    n_steps, tools, reasons, last_text = mfm.parse_transcript(p)
    assert n_steps == 1
    assert tools == ["read"]
    assert reasons == ["tool-calls", "stop"]
    assert last_text == "final answer"


def test_malformed_tool_call_is_unknown_then_stop_with_tool_call_text() -> None:
    # The #39 signature: a final unknown step (unparseable tool call) then stop,
    # with a <tool_call> block in the last text.
    assert (
        mfm.classify(("tool-calls", "unknown", "stop"), '<tool_call>\n{"name": "read">')
        == mfm.MALFORMED
    )
    assert mfm.classify(("unknown", "stop"), "<tool_call>broken") == mfm.MALFORMED


def test_stop_with_no_final_answer_is_stopped_no_answer() -> None:
    # Real tool calls then stop, no final text: the control's second death.
    assert mfm.classify(("tool-calls", "tool-calls", "stop"), None) == mfm.STOPPED
    assert mfm.classify(("tool-calls", "stop"), "<tool_call>") == mfm.STOPPED


def test_a_final_answer_is_not_a_malformed_tool_call() -> None:
    # A trial that produced a real final answer is not the malformed mode.
    assert mfm.classify(("tool-calls", "stop"), "the answer is 42") == mfm.OTHER


def test_load_trials_reads_the_death_table(tmp_path: pathlib.Path) -> None:
    # One MTP death and one control death, each a single transcript.
    for sweep, task, backend in [
        ("new-sweep1", "mbox-scan", "qwen38fnds4mtp7shim"),
        ("old-sweep2", "storage-put-and-sweep", "qwen38fnds4shim"),
    ]:
        d = tmp_path / sweep
        d.mkdir(parents=True, exist_ok=True)
        _transcript(
            d / f"{task}-{backend}-opencode-1.stdout.jsonl",
            [_text("<tool_call>broken"), _step_finish("unknown"), _step_finish("stop")],
        )
    trials = mfm.load_trials(tmp_path)
    assert len(trials) == 2
    assert trials[0].mode == mfm.MALFORMED
    assert trials[1].mode == mfm.MALFORMED
