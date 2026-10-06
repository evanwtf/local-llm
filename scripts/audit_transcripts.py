"""Audit OpenCode trial transcripts for harness faults and tool-call breaks (#969).

#968 ran for two weeks unseen: every OpenCode `grep` and `glob` call failed,
in more than 5,000 calls, because nothing read the transcripts. This script
reads them. It reports, per backend:

- **per tool**: calls, errors, error rate, and the top error messages,
  normalized so one path or one number does not split a message in two;
- **harness faults**, which make the command exit 1:
  - a tool with at least `--min-calls` calls and an error rate of
    `--fault-rate` or more;
  - error text that belongs to the client or its environment, not the model
    (`HARNESS_SIGNATURES`), or an OpenCode session error of the same kind
    (`UnknownError`, an HTTP 5xx from the model server);
  - a trial that ends within `--quick-exit-seconds` with no tool call;
- **model format breaks**: assistant text that holds raw tool-call markup
  (`FORMAT_MARKERS`), and calls OpenCode records as the `invalid` tool.
  Reported, but they do not fail the command: they are a model result;
- **model tool errors**: every other tool error (an `edit` whose `oldString`
  is not in the file, a `read` of a path that does not exist), per trial and
  per 100 calls;
- **sandbox blocks**: calls the trial sandbox refused on purpose. Neither a
  fault nor a model error.

SELECTION. Two ways, and both can be combined:

- `--results LEDGER`: the ledger rows that match `--backend`, `--since`,
  `--until` and `--batch`, each read through its own `client_log` field. Never
  by mtime and never by a constructed filename: two sweeps that share a log
  directory write `<name>.stdout.jsonl` and `<name>.stdout.2.jsonl` (#112),
  and only the row knows which one it wrote. A row whose transcript is missing
  fails the command (exit 2): a dropped row is a silently wrong count.
- `--logs DIR`: every `*.stdout*.jsonl` under DIR, for a retroactive audit.
  Each file takes its backend and start from the ledger row that names it,
  when `--results` is also given, and otherwise from its own filename (the
  run tag's UTC stamp). Never from the mtime: a copy or a restore changes it.

Rows written inside the client container record the container's path
(`/root/bench-logs/...`); the audit reads them from `--home` instead.

    uv run python scripts/audit_transcripts.py \\
        --results hardware/<machine>/results.jsonl --backend <name> \\
        --since 2026-10-06T05:00:00-0400 --json ~/bench-logs/<run>/audit.json
    uv run python scripts/audit_transcripts.py --logs ~/bench-logs --markdown

Exit status: 0 clean (format breaks allowed), 1 a harness fault, 2 the
selection could not be read.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import json
import logging
import pathlib
import re
import sys
import tomllib
import zoneinfo
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import results

import logs

logger = logging.getLogger(__name__)

#: The client container's HOME. `client_container.CONTAINER_HOME` is the
#: source; it is copied here, not imported, so the audit does not load the
#: container wrapper. `test_the_container_home_matches_the_client_wrapper`
#: keeps the two equal.
CONTAINER_HOME = "/root"

#: Where backend names are declared, for reading one out of a filename.
TASKS_TOML = ROOT / "benchmarks" / "agent" / "tasks.toml"

#: Every timestamp the audit prints is in this zone (CLAUDE.md).
NEW_YORK = zoneinfo.ZoneInfo("America/New_York")

#: Exit status when the selection itself cannot be read.
EXIT_UNREADABLE = 2

#: Error text that comes from the client or its environment, never from the
#: model. Each one blocks a batch even at one call: a model cannot make `tar`
#: fail. `tar` is the #968 ripgrep unpack; the others are the shapes a broken
#: image, a sandbox change or a dead server produce.
HARNESS_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tar", re.compile(r"(?m)^tar: ")),
    ("operation-not-permitted", re.compile(r"Operation not permitted")),
    ("eacces", re.compile(r"\bEACCES\b")),
    (
        "missing-binary",
        re.compile(
            r"command not found|spawn \S+ ENOENT|posix_spawn|"
            r"executable file not found"
        ),
    ),
    (
        "connection",
        # "Cannot connect to API" is OpenCode's APIError when the model
        # server is down (a Core i3-7100 client transcript, 2026-09-19).
        re.compile(
            r"ECONNREFUSED|ECONNRESET|[Cc]onnection (refused|reset)|"
            r"Cannot connect to API|Unable to connect"
        ),
    ),
    ("unknown-error", re.compile(r"\bUnknownError\b")),
)

#: Signatures that mean "the network", which only count against the harness
#: when the call went to the model server. `webfetch` reaches outside sites,
#: so a refused connection or a 503 there is the internet, not the client.
NETWORK_SIGNATURES = frozenset({"connection"})
EXTERNAL_TOOLS = frozenset({"webfetch"})

#: OpenCode's text for a call the trial sandbox refused by rule. On purpose,
#: so it is neither a fault nor a model error; it is reported on its own.
SANDBOX_BLOCK = "The user has specified a rule which prevents you"

#: Raw tool-call markup in assistant text: the model wrote its call as prose
#: and the server's parser did not turn it into a call (#940's Ablit).
FORMAT_MARKERS = (
    "<tool_call>",
    "</tool_call>",
    "<arg_key>",
    "</arg_key>",
    "<arg_value>",
    "</arg_value>",
)

#: OpenCode records a call it could not parse as a call to this tool.
INVALID_TOOL = "invalid"

#: Session error names that are client or server faults, not the model.
HARNESS_SESSION_ERRORS = frozenset({"UnknownError"})

#: An HTTP 5xx in a session error's text, when no status code field is set.
HTTP_5XX = re.compile(
    r"\b5\d\d\b|Internal Server Error|Bad Gateway|Service Unavailable|"
    r"Gateway Timeout"
)

#: run.py's run tag ends in a UTC stamp: `...-20261006T090919Z`.
STAMP = re.compile(r"-(\d{8}T\d{6}Z)$")

#: The clients run.py names in a transcript filename.
CLIENTS = ("opencode", "claude", "codex", "aider")
NAME_SHAPE = re.compile(
    r"^(?P<head>.+)-(?P<client>" + "|".join(CLIENTS) + r")-(?P<trial>\d+)(?:-|$)"
)

TOP_MESSAGES = 3


@dataclasses.dataclass(frozen=True)
class Thresholds:
    """The fault rules. Defaults are #969's."""

    min_calls: int = 5
    fault_rate: float = 0.9
    quick_exit_seconds: float = 30.0


@dataclasses.dataclass(frozen=True)
class Call:
    """One tool call and how its error, if any, is classified."""

    tool: str
    status: str
    #: `ok`, `harness`, `sandbox`, `model`, or `format` (an `invalid` call).
    kind: str
    #: The harness signature that matched, or None.
    signature: str | None = None
    #: The error's first line, normalized; None when the call succeeded.
    message: str | None = None


@dataclasses.dataclass(frozen=True)
class SessionError:
    name: str
    message: str
    #: The harness signature that matched, or None for a non-harness error.
    signature: str | None


@dataclasses.dataclass
class Trial:
    path: pathlib.Path
    backend: str
    started: datetime.datetime | None
    #: True when a ledger row named this transcript.
    joined: bool = False
    partial: bool = False
    calls: list[Call] = dataclasses.field(default_factory=list)
    #: Marker -> count across the assistant text.
    markup: dict[str, int] = dataclasses.field(default_factory=dict)
    session_errors: list[SessionError] = dataclasses.field(default_factory=list)
    #: Seconds between the first and last event, or the row's wall time.
    duration_s: float = 0.0
    bad_lines: int = 0


class NotOpenCodeError(Exception):
    """The file is not an OpenCode JSONL stream (aider writes plain text)."""


@dataclasses.dataclass(frozen=True)
class Selection:
    backends: frozenset[str] = frozenset()
    since: datetime.datetime | None = None
    until: datetime.datetime | None = None
    batch: str | None = None

    def admits(self, backend: str, started: datetime.datetime | None) -> bool:
        if self.backends and backend not in self.backends:
            return False
        if self.since is not None and (started is None or started < self.since):
            return False
        return not (
            self.until is not None and (started is None or started >= self.until)
        )


# --- reading one transcript ------------------------------------------------------


def normalize_message(text: str) -> str:
    """The first line, with paths and numbers folded, for counting alike."""
    line = text.strip().splitlines()[0] if text.strip() else ""
    line = re.sub(r"(?<![\w<])/[^\s:'\"),]+", "<path>", line)
    line = re.sub(r"\d+", "N", line)
    return line[:160]


def classify_tool_error(tool: str, error: str) -> tuple[str, str | None]:
    """(kind, signature) for one failed call's error text."""
    if SANDBOX_BLOCK in error:
        return "sandbox", None
    for name, pattern in HARNESS_SIGNATURES:
        if name in NETWORK_SIGNATURES and tool in EXTERNAL_TOOLS:
            continue
        if pattern.search(error):
            return "harness", name
    return "model", None


def classify_session_error(error: Mapping[str, Any]) -> SessionError:
    name = str(error.get("name") or "<unnamed>")
    data = error.get("data")
    data = data if isinstance(data, dict) else {}
    message = str(data.get("message") or "")
    status = data.get("statusCode")
    signature: str | None = None
    if name in HARNESS_SESSION_ERRORS:
        signature = "unknown-error"
    elif (isinstance(status, int) and status >= 500) or (
        # A context overflow quotes token counts that can look like a 5xx.
        name != "ContextOverflowError" and HTTP_5XX.search(message)
    ):
        signature = "http-5xx"
    else:
        for sig, pattern in HARNESS_SIGNATURES:
            if pattern.search(message):
                signature = sig
                break
    return SessionError(
        name=name, message=normalize_message(message), signature=signature
    )


def _call(part: Mapping[str, Any]) -> Call | None:
    tool = part.get("tool")
    state = part.get("state")
    if not isinstance(tool, str) or not isinstance(state, dict):
        return None
    status = state.get("status")
    if status not in ("completed", "error"):
        return None  # pending/running: not an outcome
    if tool == INVALID_TOOL:
        return Call(tool=tool, status=str(status), kind="format")
    if status == "completed":
        return Call(tool=tool, status="completed", kind="ok")
    error = state.get("error")
    error = error if isinstance(error, str) else ""
    kind, signature = classify_tool_error(tool, error)
    return Call(
        tool=tool,
        status="error",
        kind=kind,
        signature=signature,
        message=normalize_message(error) or "<no error text>",
    )


def load_trial(
    path: pathlib.Path,
    *,
    backend: str,
    started: datetime.datetime | None = None,
    joined: bool = False,
    wall_seconds: float | None = None,
) -> Trial:
    """Read one transcript. Raises NotOpenCodeError on a non-JSONL file."""
    trial = Trial(
        path=path,
        backend=backend,
        started=started,
        joined=joined,
        partial="partial" in path.name.split("."),
    )
    first: int | None = None
    last: int | None = None
    with path.open(errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                if lineno == 1:
                    raise NotOpenCodeError(f"line 1 is not JSON: {exc}") from exc
                trial.bad_lines += 1  # a truncated tail on a killed trial
                continue
            if not isinstance(event, dict):
                trial.bad_lines += 1
                continue
            stamp = event.get("timestamp")
            if isinstance(stamp, int):
                first = stamp if first is None else min(first, stamp)
                last = stamp if last is None else max(last, stamp)
            if event.get("type") == "error":
                err = event.get("error")
                if isinstance(err, dict):
                    trial.session_errors.append(classify_session_error(err))
                continue
            part = event.get("part")
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool":
                call = _call(part)
                if call is not None:
                    trial.calls.append(call)
            elif part.get("type") == "text":
                body = part.get("text")
                if isinstance(body, str):
                    for marker in FORMAT_MARKERS:
                        hits = body.count(marker)
                        if hits:
                            trial.markup[marker] = trial.markup.get(marker, 0) + hits
    if wall_seconds is not None:
        trial.duration_s = float(wall_seconds)
    elif first is not None and last is not None:
        trial.duration_s = (last - first) / 1000.0
    return trial


# --- one backend's report ----------------------------------------------------------


def _iso(when: datetime.datetime | None) -> str | None:
    if when is None:
        return None
    return when.astimezone(NEW_YORK).strftime("%Y-%m-%dT%H:%M:%S%z")


def audit_group(
    backend: str, trials: Sequence[Trial], thresholds: Thresholds
) -> dict[str, Any]:
    """Everything #969 asks for, for one backend's trials, as plain data."""
    per_tool: dict[str, dict[str, Any]] = {}
    messages: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    signatures: collections.Counter[str] = collections.Counter()
    signature_examples: dict[str, str] = {}
    session_errors: collections.Counter[str] = collections.Counter()
    markup: collections.Counter[str] = collections.Counter()
    quick_exits: list[str] = []
    format_break_trials = 0
    model_errors = 0
    sandbox = 0
    invalid = 0
    for trial in trials:
        for call in trial.calls:
            row = per_tool.setdefault(call.tool, {"calls": 0, "errors": 0})
            row["calls"] += 1
            if call.status == "error":
                row["errors"] += 1
                messages[call.tool][call.message or "<no error text>"] += 1
            if call.kind == "harness" and call.signature:
                signatures[call.signature] += 1
                signature_examples.setdefault(
                    call.signature, f"{call.tool}: {call.message}"
                )
            elif call.kind == "model":
                model_errors += 1
            elif call.kind == "sandbox":
                sandbox += 1
            elif call.kind == "format":
                invalid += 1
        for err in trial.session_errors:
            session_errors[err.name] += 1
            if err.signature:
                signatures[err.signature] += 1
                signature_examples.setdefault(
                    err.signature, f"session {err.name}: {err.message}"
                )
        markup.update(trial.markup)
        if trial.markup or any(c.kind == "format" for c in trial.calls):
            format_break_trials += 1
        if (
            not trial.calls
            and not trial.partial
            and trial.duration_s < thresholds.quick_exit_seconds
        ):
            quick_exits.append(trial.path.name)

    fault_tools = sorted(
        tool
        for tool, row in per_tool.items()
        if tool != INVALID_TOOL
        and row["calls"] >= thresholds.min_calls
        and row["errors"] / row["calls"] >= thresholds.fault_rate
    )
    faults: list[str] = []
    for tool in fault_tools:
        row = per_tool[tool]
        top = messages[tool].most_common(1)
        faults.append(
            f"{tool}: {row['errors']} of {row['calls']} calls failed"
            + (f" ({top[0][0]})" if top else "")
        )
    for sig, count in sorted(signatures.items()):
        faults.append(f"{sig} x{count}: {signature_examples[sig]}")
    if quick_exits:
        faults.append(
            f"{len(quick_exits)} trial(s) ended in under "
            f"{thresholds.quick_exit_seconds:g} s with no tool call"
        )

    for tool, row in per_tool.items():
        row["error_rate"] = round(row["errors"] / row["calls"], 4)
        row["top_errors"] = [
            {"message": m, "count": n}
            for m, n in messages[tool].most_common(TOP_MESSAGES)
        ]
    calls = sum(row["calls"] for row in per_tool.values())
    dated = [t.started for t in trials if t.started is not None]
    n = len(trials)
    return {
        "backend": backend,
        "trials": n,
        "partial_trials": sum(1 for t in trials if t.partial),
        "joined_trials": sum(1 for t in trials if t.joined),
        "first_started": _iso(min(dated)) if dated else None,
        "last_started": _iso(max(dated)) if dated else None,
        "tool_calls": calls,
        "tool_errors": sum(row["errors"] for row in per_tool.values()),
        "per_tool": dict(sorted(per_tool.items())),
        "harness_fault": bool(faults),
        "faults": faults,
        "harness_fault_tools": fault_tools,
        "harness_signatures": dict(sorted(signatures.items())),
        "quick_exits": len(quick_exits),
        "quick_exit_trials": quick_exits,
        "session_errors": dict(sorted(session_errors.items())),
        "format_break_trials": format_break_trials,
        "format_break_markup": dict(sorted(markup.items())),
        "invalid_calls": invalid,
        "model_tool_errors": model_errors,
        "model_tool_errors_per_trial": round(model_errors / n, 2) if n else 0.0,
        "model_tool_errors_per_100_calls": (
            round(100 * model_errors / calls, 2) if calls else 0.0
        ),
        "sandbox_blocks": sandbox,
        "unparsed_lines": sum(t.bad_lines for t in trials),
    }


# --- selection ------------------------------------------------------------------------


def parse_when(text: str) -> datetime.datetime:
    """An ISO 8601 instant; one without an offset is New York local time."""
    when = datetime.datetime.fromisoformat(text)
    if when.tzinfo is None:
        when = when.replace(tzinfo=NEW_YORK)
    return when


def _row_started(row: Mapping[str, Any]) -> datetime.datetime | None:
    raw = row.get("started")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return parse_when(raw)
    except ValueError:
        return None


def _stamp_started(name: str) -> datetime.datetime | None:
    base = name.split(".stdout", 1)[0]
    match = STAMP.search(base)
    if not match:
        return None
    return datetime.datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
        tzinfo=datetime.UTC
    )


def known_backends(path: pathlib.Path = TASKS_TOML) -> frozenset[str]:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return frozenset()
    backend = data.get("backend")
    return frozenset(backend) if isinstance(backend, dict) else frozenset()


def backend_from_name(name: str, known: frozenset[str]) -> str:
    """The backend in `<task>-<backend>-<client>-<trial>-<run tag>`.

    Tasks and two backends hold hyphens, so the longest declared backend that
    ends the head wins; an undeclared one is the head's last segment.
    """
    base = name.split(".stdout", 1)[0]
    match = NAME_SHAPE.match(base)
    if not match:
        return "<unknown>"
    head = match.group("head")
    fits = [b for b in known if head.endswith(f"-{b}")]
    if fits:
        return max(fits, key=len)
    return head.rsplit("-", 1)[-1]


def resolve_client_log(raw: str, *, home: pathlib.Path) -> pathlib.Path:
    """The host path of a row's `client_log`.

    A row written inside the client container records the container's HOME;
    the same file sits under the host user's home, mounted at the same
    relative path.
    """
    prefix = f"{CONTAINER_HOME}/"
    if raw.startswith(prefix):
        # Look on the host first: stat() on the container path itself can
        # raise, because the host's /root is not readable by the host user.
        host = home / raw[len(prefix) :]
        if host.exists():
            return host
    return pathlib.Path(raw).expanduser()


def _exists(path: pathlib.Path) -> bool:
    """exists() that says False, not raises, on a path the user cannot stat."""
    try:
        return path.exists()
    except OSError:
        return False


def _ledger_rows(ledgers: Iterable[pathlib.Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ledger in ledgers:
        rows.extend(results.load(ledger))
    return rows


class MissingTranscriptError(Exception):
    pass


def select_from_ledgers(
    ledgers: Sequence[pathlib.Path],
    selection: Selection,
    *,
    home: pathlib.Path,
) -> list[Trial]:
    """Trials named by the matching rows' own `client_log` (#112, #244)."""
    trials: list[Trial] = []
    seen: set[pathlib.Path] = set()
    missing: list[str] = []
    for row in _ledger_rows(ledgers):
        backend = str(row.get("backend") or "<unknown>")
        started = _row_started(row)
        if not selection.admits(backend, started):
            continue
        if selection.batch is not None and row.get("batch") != selection.batch:
            continue
        raw = row.get("client_log")
        if not isinstance(raw, str) or not raw:
            logger.warning(
                "%s/%s started %s has no client_log; not audited",
                row.get("task"),
                backend,
                row.get("started"),
            )
            continue
        path = resolve_client_log(raw, home=home)
        if not _exists(path):
            missing.append(raw)
            continue
        if path in seen:
            continue
        seen.add(path)
        wall = row.get("wall_seconds")
        try:
            trials.append(
                load_trial(
                    path,
                    backend=backend,
                    started=started,
                    joined=True,
                    wall_seconds=float(wall) if isinstance(wall, int | float) else None,
                )
            )
        except NotOpenCodeError as exc:
            logger.info("%s: not an OpenCode transcript (%s)", path, exc)
    if missing:
        raise MissingTranscriptError(
            f"{len(missing)} row(s) name a transcript that is not there, "
            f"first {missing[0]}"
        )
    return trials


def ledger_index(
    ledgers: Sequence[pathlib.Path], *, home: pathlib.Path
) -> dict[pathlib.Path, dict[str, Any]]:
    """Each row keyed by the resolved host path of its `client_log`."""
    index: dict[pathlib.Path, dict[str, Any]] = {}
    for row in _ledger_rows(ledgers):
        raw = row.get("client_log")
        if isinstance(raw, str) and raw:
            index[resolve_client_log(raw, home=home).resolve()] = row
    return index


def select_from_dirs(
    dirs: Sequence[pathlib.Path],
    selection: Selection,
    *,
    ledger_rows: Mapping[pathlib.Path, Mapping[str, Any]],
    home: pathlib.Path,
) -> list[Trial]:
    """Every transcript under `dirs`, with its row's facts when one names it."""
    known = known_backends()
    trials: list[Trial] = []
    undated = 0
    for directory in dirs:
        for path in sorted(directory.rglob("*.stdout*.jsonl")):
            row = ledger_rows.get(path.resolve())
            if row is not None:
                backend = str(row.get("backend") or "<unknown>")
                started = _row_started(row) or _stamp_started(path.name)
            else:
                backend = backend_from_name(path.name, known)
                started = _stamp_started(path.name)
            if started is None:
                undated += 1
            if not selection.admits(backend, started):
                continue
            wall = row.get("wall_seconds") if row is not None else None
            try:
                trials.append(
                    load_trial(
                        path,
                        backend=backend,
                        started=started,
                        joined=row is not None,
                        wall_seconds=(
                            float(wall) if isinstance(wall, int | float) else None
                        ),
                    )
                )
            except NotOpenCodeError as exc:
                logger.info("%s: not an OpenCode transcript (%s)", path, exc)
    if undated and (selection.since or selection.until):
        logger.warning(
            "%d transcript(s) carry no start time and were left out by the date filter",
            undated,
        )
    return trials


# --- output ---------------------------------------------------------------------------


def summarize(trials: Sequence[Trial], thresholds: Thresholds) -> dict[str, Any]:
    groups: dict[str, list[Trial]] = collections.defaultdict(list)
    for trial in trials:
        groups[trial.backend].append(trial)
    backends = {
        name: audit_group(name, group, thresholds)
        for name, group in sorted(groups.items())
    }
    return {
        "thresholds": dataclasses.asdict(thresholds),
        "harness_fault": any(b["harness_fault"] for b in backends.values()),
        "backends": backends,
        "trials": [
            {
                "path": str(t.path),
                "backend": t.backend,
                "started": _iso(t.started),
                "joined": t.joined,
                "partial": t.partial,
                "tool_calls": len(t.calls),
                "tool_errors": sum(1 for c in t.calls if c.status == "error"),
                "harness_errors": sum(1 for c in t.calls if c.kind == "harness"),
                "model_tool_errors": sum(1 for c in t.calls if c.kind == "model"),
                "format_markup": t.markup,
                "invalid_calls": sum(1 for c in t.calls if c.kind == "format"),
                "session_errors": [e.name for e in t.session_errors],
                "duration_s": round(t.duration_s, 1),
            }
            for t in trials
        ],
    }


def human_block(summary: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for name, b in summary["backends"].items():
        span = (
            f"{(b['first_started'] or '?')[:10]} to {(b['last_started'] or '?')[:10]}"
        )
        lines.append(
            f"{name}: {b['trials']} trials ({span}), {b['tool_calls']} tool calls, "
            f"{b['tool_errors']} errors"
        )
        for fault in b["faults"]:
            lines.append(f"  HARNESS FAULT {fault}")
        if b["format_break_trials"]:
            lines.append(
                f"  format breaks: {b['format_break_trials']} trial(s); markup "
                f"{b['format_break_markup']}; {b['invalid_calls']} invalid call(s)"
            )
        lines.append(
            f"  model tool errors: {b['model_tool_errors']} "
            f"({b['model_tool_errors_per_trial']} per trial, "
            f"{b['model_tool_errors_per_100_calls']} per 100 calls); "
            f"sandbox blocks: {b['sandbox_blocks']}"
        )
        tools = ", ".join(
            f"{tool} {row['errors']}/{row['calls']}"
            for tool, row in sorted(
                b["per_tool"].items(), key=lambda kv: -int(kv[1]["calls"])
            )
        )
        if tools:
            lines.append(f"  per tool (errors/calls): {tools}")
    verdict = "HARNESS FAULT" if summary["harness_fault"] else "clean"
    lines.append(f"audit: {len(summary['trials'])} trials, {verdict}")
    return lines


def _fault_cell(b: Mapping[str, Any]) -> str:
    parts = [
        f"{tool} ({b['per_tool'][tool]['errors']}/{b['per_tool'][tool]['calls']})"
        for tool in b["harness_fault_tools"]
    ]
    parts += [f"{sig} x{n}" for sig, n in b["harness_signatures"].items()]
    if b["quick_exits"]:
        parts.append(f"quick exit x{b['quick_exits']}")
    return "; ".join(parts) or "none"


def markdown_table(summary: Mapping[str, Any], min_trials: int) -> list[str]:
    """One row per backend with at least `min_trials`; one line for the rest."""
    header = (
        "| backend | trials | dates | tool calls | harness faults "
        "| format breaks (trials) | model tool errors / 100 calls |"
    )
    lines = [header, "|---|---|---|---|---|---|---|"]
    rest: list[Mapping[str, Any]] = []
    for name, b in summary["backends"].items():
        if b["trials"] < min_trials:
            rest.append(b)
            continue
        dates = (
            f"{(b['first_started'] or '?')[:10]} to {(b['last_started'] or '?')[:10]}"
        )
        breaks = f"{b['format_break_trials']}"
        if b["invalid_calls"]:
            breaks += f" ({b['invalid_calls']} invalid)"
        lines.append(
            f"| {name} | {b['trials']} | {dates} | {b['tool_calls']} "
            f"| {_fault_cell(b)} | {breaks} | {b['model_tool_errors_per_100_calls']} |"
        )
    if rest:
        trials = sum(int(b["trials"]) for b in rest)
        faulty = sum(1 for b in rest if b["harness_fault"])
        broken = sum(int(b["format_break_trials"]) for b in rest)
        lines.append("")
        lines.append(
            f"Below {min_trials} trials: {len(rest)} backends, {trials} trials; "
            f"{faulty} with a harness fault, {broken} trials with a format break."
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    first = (__doc__ or "").splitlines()[0]
    p = argparse.ArgumentParser(description=first)
    p.add_argument(
        "--results",
        action="append",
        type=pathlib.Path,
        default=[],
        metavar="LEDGER",
        help="a results.jsonl; its rows' client_log fields select the "
        "transcripts (repeatable)",
    )
    p.add_argument(
        "--logs",
        action="append",
        type=pathlib.Path,
        default=[],
        metavar="DIR",
        help="audit every *.stdout*.jsonl under DIR instead (repeatable); "
        "--results then only supplies each file's backend and start",
    )
    p.add_argument("--backend", action="append", default=[], help="repeatable")
    p.add_argument(
        "--since",
        type=parse_when,
        help="ISO 8601 start, inclusive (local if no offset)",
    )
    p.add_argument("--until", type=parse_when, help="ISO 8601 end, exclusive")
    p.add_argument("--batch", help="ledger rows whose batch field equals this")
    p.add_argument(
        "--home",
        type=pathlib.Path,
        default=pathlib.Path.home(),
        help=f"host home for a container path under {CONTAINER_HOME}/ (default: yours)",
    )
    p.add_argument("--min-calls", type=int, default=Thresholds.min_calls)
    p.add_argument("--fault-rate", type=float, default=Thresholds.fault_rate)
    p.add_argument(
        "--quick-exit-seconds", type=float, default=Thresholds.quick_exit_seconds
    )
    p.add_argument(
        "--json", type=pathlib.Path, metavar="PATH", help="write the summary"
    )
    p.add_argument(
        "--markdown", action="store_true", help="also log a table, one row per backend"
    )
    p.add_argument(
        "--min-trials",
        type=int,
        default=10,
        help="backends below this go in the table's summary line (default 10)",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    if not args.results and not args.logs:
        p.error("give --results, --logs, or both")
    selection = Selection(
        backends=frozenset(args.backend),
        since=args.since,
        until=args.until,
        batch=args.batch,
    )
    thresholds = Thresholds(
        min_calls=args.min_calls,
        fault_rate=args.fault_rate,
        quick_exit_seconds=args.quick_exit_seconds,
    )
    try:
        if args.logs:
            rows = ledger_index(args.results, home=args.home) if args.results else {}
            trials = select_from_dirs(
                args.logs, selection, ledger_rows=rows, home=args.home
            )
        else:
            trials = select_from_ledgers(args.results, selection, home=args.home)
    except (MissingTranscriptError, OSError) as exc:
        logger.error("cannot read the selection: %s", exc)
        return EXIT_UNREADABLE
    if not trials:
        logger.error("no transcripts matched the selection")
        return EXIT_UNREADABLE

    summary = summarize(trials, thresholds)
    for line in human_block(summary):
        logger.info("%s", line)
    if args.markdown:
        for line in markdown_table(summary, args.min_trials):
            logger.info("%s", line)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        # Read it back: a summary the gate cannot parse is no summary.
        json.loads(args.json.read_text())
        logger.info("wrote %s", args.json)
    return 1 if summary["harness_fault"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
