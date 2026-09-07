"""Count tool-call outcomes from an OpenCode client transcript.

The ds4 server log names a malformed tool call in its own wording
("TOOLS invalid tool call; continuing"). llama.cpp does not emit those lines,
so that instrument cannot compare the two engines. This counter reads the
CLIENT side instead: the OpenCode transcript each trial writes, which both
engines produce.

SCHEMA ASSUMPTION (v2, from the real transcripts). An OpenCode transcript is a
JSONL event stream. Across the live corpus exactly four event types occur:
`step_start`, `step_finish`, `tool_use`, `text`. A `tool_use` event is
SELF-CONTAINED: one event per tool call, carrying the outcome in
`part.state.status` ('completed' or 'error') and the tool name in
`part.tool`. There is no open/close pairing and no retry marker.

The parser counts three honest columns per trial:

- `issued` -- the number of `tool_use` events.
- `errored` -- the number of `tool_use` events with
  `part.state.status == 'error'`. Raw and unheuristic.
- `distinct_tools` -- the number of distinct `part.tool` values.

Whether the agent RETRIED after an error is not marked in the record. It is
inferable only from what follows -- a later `tool_use` with the same tool and
the same input -- or not at all. That inference is a heuristic, not a
measurement. It lives in `infer_retries`, behind its own name and its own
tests, and is opt-in via `--infer-retries`. The raw `errored` count never
depends on it.

The parser refuses (raises) on shapes it does not recognise, and the refusal
is never a silent zero:

- `NotOpenCodeError` -- the file is a valid transcript but not an OpenCode
  one (another client, e.g. Codex, uses a different vocabulary). Expected,
  not corruption; the caller selects or skips by client.
- `TranscriptError` -- corruption: an empty file, a line that is not valid
  JSON, a line that is not a JSON object (a bare scalar), or a `tool_use`
  event missing its outcome.

The join key is the transcript stem, preserved verbatim. Filenames changed
shape over time (`<task>-<backend>-<client>-<trial>.stdout[.N].jsonl` vs the
older no-client form), and the `.N` infix is not yet explained, so the stem is
treated as opaque and never parsed.
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

#: The four event types an OpenCode transcript uses. Any other type means the
#: file is not an OpenCode transcript (another client, e.g. Codex).
OPENCODE_EVENT_TYPES = frozenset({"step_start", "step_finish", "tool_use", "text"})

#: Event types that carry no tool-call signal. Ignored for counting.
BOOKKEEPING_EVENT_TYPES = frozenset({"step_start", "step_finish", "text"})

EVENT_TOOL_USE = "tool_use"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class ToolCall:
    """One tool call, read from a self-contained `tool_use` event.

    Schema assumption v2: the event carries the outcome in `part.state.status`
    and the tool name in `part.tool`. `input` is the tool-specific argument
    object, used only by the retry heuristic.
    """

    tool: str
    status: str
    input: object


class TranscriptError(Exception):
    """A transcript has a shape the parser does not recognise (corruption).

    Raising, not returning a zero, is the point: a zero that means "no
    errors" and a zero that means "I could not read this" must not be the
    same value.
    """


class NotOpenCodeError(TranscriptError):
    """The file is a valid transcript but not an OpenCode one (e.g. Codex).

    Expected, not corruption. The caller selects or skips by client without
    the refusal looking like a defect.
    """


def read_tool_calls(path: pathlib.Path) -> list[ToolCall]:
    """Read a transcript into its tool calls, refusing bad shapes.

    Raises NotOpenCodeError when the file is not an OpenCode transcript.
    Raises TranscriptError on corruption: an empty file, a line that is not
    valid JSON, a line that is not a JSON object, or a `tool_use` event
    missing its outcome.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise TranscriptError(f"cannot read: {exc}") from exc
    calls: list[ToolCall] = []
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
        if etype not in OPENCODE_EVENT_TYPES:
            raise NotOpenCodeError(
                f"line {lineno} has event type {etype!r}, not an OpenCode type"
            )
        if etype == EVENT_TOOL_USE:
            calls.append(_parse_tool_call(event, lineno))
    if not saw_line:
        raise TranscriptError("transcript is empty")
    return calls


def _parse_tool_call(event: dict, lineno: int) -> ToolCall:
    part = event.get("part")
    if not isinstance(part, dict):
        raise TranscriptError(f"line {lineno} tool_use has no part object")
    tool = part.get("tool")
    if not isinstance(tool, str) or not tool:
        raise TranscriptError(f"line {lineno} tool_use has no part.tool string")
    state = part.get("state")
    if not isinstance(state, dict):
        raise TranscriptError(f"line {lineno} tool_use has no part.state object")
    status = state.get("status")
    if not isinstance(status, str) or not status:
        raise TranscriptError(f"line {lineno} tool_use has no part.state.status string")
    return ToolCall(tool=tool, status=status, input=state.get("input"))


def row_from_calls(path: pathlib.Path, calls: list[ToolCall]) -> dict[str, int | str]:
    """Build the per-trial row from the parsed tool calls."""
    return {
        "transcript": path.stem,
        "issued": len(calls),
        "errored": sum(1 for c in calls if c.status == STATUS_ERROR),
        "distinct_tools": len({c.tool for c in calls}),
    }


def count_transcript(path: pathlib.Path) -> dict[str, int | str]:
    """Count tool-call outcomes in one transcript.

    Returns a row keyed by the transcript stem, joinable to results.jsonl on
    the `client_log` basename. Raises on a shape the parser does not
    recognise.
    """
    return row_from_calls(path, read_tool_calls(path))


def infer_retries(calls: list[ToolCall]) -> int:
    """Estimate how many errored tool calls were retried.

    HEURISTIC, not a measurement. The transcript does not mark a retry. This
    counts an errored call as retried when a LATER call has the same tool and
    the same input. Keep the raw `errored` count separate; this is a guess.
    """
    retried = 0
    for i, call in enumerate(calls):
        if call.status != STATUS_ERROR:
            continue
        for later in calls[i + 1 :]:
            if later.tool == call.tool and later.input == call.input:
                retried += 1
                break
    return retried


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
    p.add_argument(
        "--infer-retries",
        action="store_true",
        help="add a retried column from the retry heuristic (a guess, not a "
        "measurement)",
    )
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    failures = 0
    for path in _expand_inputs(args.transcripts):
        try:
            calls = read_tool_calls(path)
        except NotOpenCodeError as exc:
            logger.info("%s: not an OpenCode transcript (%s)", path, exc)
            continue
        except TranscriptError as exc:
            logger.error("%s: %s", path, exc)
            failures += 1
            continue
        row = row_from_calls(path, calls)
        if args.infer_retries:
            row["retried"] = infer_retries(calls)
        logger.info(json.dumps(row, sort_keys=True))
    if failures:
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
