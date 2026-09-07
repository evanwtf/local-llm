"""Every timestamp this repo writes is ISO 8601 with an explicit offset.

Three bugs on 2026-09-06 shared one cause: a time compared as a string under
an assumption nobody restated. A results file carried naive local timestamps
while the manifest beside it carried UTC ones, so a readout joining the two
was four hours wrong with no error. A batch cutoff compared `date +%H:%M`
against `09:15` and failed open across midnight -- and `09:15` was the default,
meaning an overnight batch, meaning the default was the broken case. A readout
filter compared a truncated `"...T17:16"` against full timestamps and selected
the right rows only by luck.

The convention is in AGENTS.md. This is what keeps it true.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: `2026-09-06T17:16:48-0400`. The offset is the point: a naive timestamp is
#: indistinguishable from a UTC one and silently hours wrong.
CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}$")

#: Fields that hold a moment in time. Named rather than sniffed: a value that
#: merely looks like a date is not necessarily one, and a rule that guesses
#: produces the false positives that make people disable it.
TIME_FIELDS = frozenset({"started", "finished", "ended", "authored_at"})

DATA_GLOBS = ("benchmarks/agent/*.jsonl", "evidence/*.json")


def _timestamps(obj: object, field: str | None = None):
    """Yield (field, value) for every named time field, at any depth."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _timestamps(value, key)
    elif isinstance(obj, list):
        for value in obj:
            yield from _timestamps(value, field)
    elif isinstance(obj, str) and field in TIME_FIELDS:
        yield field, obj


def _data_files() -> list[pathlib.Path]:
    return sorted(p for glob in DATA_GLOBS for p in ROOT.glob(glob))


def test_there_are_data_files_to_check():
    """A suite that silently checks nothing passes for the wrong reason."""
    assert _data_files(), "no data files matched; the globs have gone stale"


@pytest.mark.parametrize("path", _data_files(), ids=lambda p: p.name)
def test_every_timestamp_carries_an_offset(path: pathlib.Path):
    bad: list[str] = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            # A .json file is one document, not one per line; read it whole.
            doc = json.loads(path.read_text())
        for field, value in _timestamps(doc):
            if not CANONICAL.match(value):
                bad.append(f"{path.name}:{lineno} {field}={value!r}")
        if path.suffix == ".json":
            break
    assert not bad, (
        "timestamps must be ISO 8601 with an explicit offset "
        "(2026-09-06T17:16:48-0400) -- see AGENTS.md:\n  "
        + "\n  ".join(bad[:20])
    )


@pytest.mark.parametrize("path", _data_files(), ids=lambda p: p.name)
def test_every_timestamp_parses(path: pathlib.Path):
    """The regex says it looks right; this says a parser accepts it.

    These are separate assertions because they fail for different reasons, and
    `2026-09-31T00:00:00-0400` matches the shape while naming no date.
    """
    text = path.read_text()
    docs = (
        [json.loads(text)]
        if path.suffix == ".json"
        else [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    )
    for doc in docs:
        for field, value in _timestamps(doc):
            try:
                parsed = datetime.datetime.fromisoformat(value)
            except ValueError as exc:  # pragma: no cover - the assert reports it
                pytest.fail(f"{path.name} {field}={value!r} does not parse: {exc}")
            assert parsed.tzinfo is not None, (
                f"{path.name} {field}={value!r} parsed without a timezone"
            )


def test_no_script_compares_wall_clock_time_as_a_string():
    """`[ "$(date +%H:%M)" \\> "$UNTIL" ]` fails open across midnight.

    At 00:30 against a 09:15 cutoff the comparison is false, so a guard whose
    whole job is to stop the batch never fires. Compare epoch seconds instead,
    resolving the cutoff to an absolute instant once at launch.
    """
    offenders: list[str] = []
    for path in sorted(ROOT.glob("scripts/*.sh")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "date +%H:%M" not in line:
                continue
            # Printing the time is fine; comparing it is not.
            if re.search(r"\\?>|\\?<|\[\s*\"?\$\(date", line) and "echo" not in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "compare times as epoch seconds, not as HH:MM strings -- see #175:\n  "
        + "\n  ".join(offenders)
    )
