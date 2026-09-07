"""Count tool-call retries from an OpenCode client transcript.

The ds4 server log names a malformed tool call in its own wording
("TOOLS invalid tool call; continuing"). llama.cpp does not emit those lines,
so that instrument cannot compare the two engines. This counter reads the
CLIENT side instead: the OpenCode transcript each trial writes, which both
engines produce.

SCHEMA ASSUMPTION (v1). The real transcripts live on the operator's machine
and are not in this repo, so the parser is written against a documented
assumption and made strict: a shape it does not recognise raises, it never
returns a silent zero. The peer runs this against real transcripts and sends
back the deltas; change the constants and the dataclass below when the real
shape differs.

An OpenCode transcript is a JSONL event stream. Each line is a JSON object
with a `type` field. Tool calls follow a lifecycle:

- `tool_use` opens a call. The model asked to run a tool. Carries `part.id`
  and `part.tool`. Counts as one tool call issued.
- `tool.execution.completed` closes a call that ran. Carries `id`.
- `tool.execution.error` closes a call that failed. Carries `id` and
  `retryable` (bool). `retryable: true` means the client will retry; count it
  as retried. `retryable: false` means the client gave up; count it as a
  terminal failure.

Bookkeeping events carry no tool-call signal and are ignored: `step_start`,
`text`, `step_finish`, `tool.execution.started`, `session.initialized`,
`user.message.created`, `assistant.message.created`,
`assistant.message.updated`, `part.updated`, `message.updated`.

The parser refuses (raises) on: an empty transcript, a line that is not valid
JSON, an event type it does not know, a close event with no open call, and a
transcript that ends with a call still open (truncated). A zero that means
"no retries" and a zero that means "I could not read this" are never the same
value.

Each trial writes one row of JSON to stdout, joinable to results.jsonl on the
transcript stem (the `client_log` basename without the `.stdout.jsonl`
suffix).
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from dataclasses import dataclass
from typing import NoReturn

logger = logging.getLogger(__name__)

#: Event types that open or close a tool call. The parser counts these.
EVENT_TOOL_USE = "tool_use"
EVENT_TOOL_EXECUTION_COMPLETED = "tool.execution.completed"
EVENT_TOOL_EXECUTION_ERROR = "tool.execution.error"
TOOL_EVENT_TYPES = frozenset(
    {EVENT_TOOL_USE, EVENT_TOOL_EXECUTION_COMPLETED, EVENT_TOOL_EXECUTION_ERROR}
)

#: Event types that carry no tool-call signal. The parser ignores them.
BOOKKEEPING_EVENT_TYPES = frozenset(
    {
        "step_start",
        "text",
        "step_finish",
        "tool.execution.started",
        "session.initialized",
        "user.message.created",
        "assistant.message.created",
        "assistant.message.updated",
        "part.updated",
        "message.updated",
    }
)


@dataclass(frozen=True)
class ToolCallEvent:
    """The documented shape of a tool-call event (schema assumption v1).

    The peer runs this against real transcripts and sends back deltas. Change
    these fields when the real shape differs.
    """

    type: str
    id: str
    tool: str
    retryable: bool | None = None


class TranscriptError(Exception):
    """A transcript has a shape the parser does not recognise.

    Raising, not returning a zero, is the point: a zero that means "no
    retries" and a zero that means "I could not read this" must not be the
    same value.
    """


def _read_events(path: pathlib.Path) -> list[dict]:
    """Read a transcript into its tool-call events, refusing bad shapes.

    Raises TranscriptError on an empty file, a line that is not valid JSON, an
    event type the parser does not know, or a line that is not a JSON object.
    Returns the tool-call events; a transcript with only bookkeeping events
    returns an empty list, which is a valid trial that made no tool calls.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise TranscriptError(f"cannot read: {exc}") from exc
    events: list[dict] = []
    saw_line = False
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        saw_line = True
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TranscriptError(f"line {lineno} is not valid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise TranscriptError(f"line {lineno} is not a JSON object")
        etype = event.get("type")
        if not isinstance(etype, str) or not etype:
            raise TranscriptError(f"line {lineno} has no type string")
        if etype in TOOL_EVENT_TYPES:
            events.append(event)
        elif etype in BOOKKEEPING_EVENT_TYPES:
            continue
        else:
            raise TranscriptError(f"line {lineno} has unknown event type {etype!r}")
    if not saw_line:
        raise TranscriptError("transcript is empty")
    return events


def count_transcript(path: pathlib.Path) -> dict[str, int | str]:
    """Count tool-call retries in one transcript.

    Returns a row keyed by the transcript stem, joinable to results.jsonl on
    the `client_log` basename. Raises TranscriptError on a shape the parser
    does not recognise.
    """
    events = _read_events(path)
    issued = 0
    retried = 0
    terminal = 0
    open_calls = 0
    for event in events:
        etype = event["type"]
        if etype == EVENT_TOOL_USE:
            issued += 1
            open_calls += 1
        elif etype == EVENT_TOOL_EXECUTION_COMPLETED:
            if open_calls == 0:
                raise TranscriptError(
                    f"{EVENT_TOOL_EXECUTION_COMPLETED} with no open tool call"
                )
            open_calls -= 1
        elif etype == EVENT_TOOL_EXECUTION_ERROR:
            if open_calls == 0:
                raise TranscriptError(
                    f"{EVENT_TOOL_EXECUTION_ERROR} with no open tool call"
                )
            open_calls -= 1
            retryable = event.get("retryable")
            if not isinstance(retryable, bool):
                raise TranscriptError(
                    f"{EVENT_TOOL_EXECUTION_ERROR} has no retryable bool"
                )
            if retryable:
                retried += 1
            else:
                terminal += 1
    if open_calls > 0:
        raise TranscriptError(
            f"transcript ends with {open_calls} tool call(s) still open (truncated)"
        )
    return {
        "transcript": path.stem,
        "tool_calls_issued": issued,
        "tool_calls_retried": retried,
        "tool_calls_failed_terminal": terminal,
    }


def _expand_inputs(paths: list[str]) -> list[pathlib.Path]:
    """Expand file and directory arguments into transcript paths.

    A directory contributes its `*.stdout.jsonl` files, sorted. A file is used
    as given.
    """
    out: list[pathlib.Path] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_dir():
            out.extend(sorted(path.glob("*.stdout.jsonl")))
        else:
            out.append(path)
    return out


def main(argv: list[str] | None = None) -> NoReturn:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "transcripts",
        nargs="+",
        help="transcript file(s), or directories of *.stdout.jsonl files",
    )
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    failures = 0
    for path in _expand_inputs(args.transcripts):
        try:
            row = count_transcript(path)
        except TranscriptError as exc:
            logger.error("%s: %s", path, exc)
            failures += 1
            continue
        logger.info(json.dumps(row, sort_keys=True))
    if failures:
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
