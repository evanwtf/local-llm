"""Every log line this repo writes carries an ISO 8601 timestamp, or none.

Python's default `asctime` is `2026-09-09 07:00:12,481`. Nothing else in this
tree writes a time that way: `results-*.jsonl` and the manifests both carry
`2026-09-09T07:00:12-0400`, enforced by `tests/test_iso8601_timestamps.py`,
because comparing a naive local timestamp against a `Z` one is a four-hour
error with no error message (`scripts/backfill_iso8601.py` exists to undo
exactly that).

Log lines get joined to rows by hand -- "the server said X at 07:00:12, which
row was that?" -- so they get the same shape, offset included. `logs.DATEFMT`
is the one definition and this file is what keeps it the only one.
"""

from __future__ import annotations

import datetime
import io
import logging
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import backfill_iso8601
import provenance

import logs

#: A call, not a mention: `test_provenance.py` names the string in order to
#: forbid it, and this docstring does too.
CALL = re.compile(r"logging\.basicConfig\(")

#: The two modules allowed to make the call. Everything else asks them.
OWNERS = {"scripts/lib/logs.py", "benchmarks/agent/provenance.py"}

#: And the file that spells the call out in order to forbid it, exactly as
#: this one does. `benchmarks/agent/test_provenance.py` runs the narrower
#: check that entry points in that package use provenance.configure(), which
#: stamps the harness commit as well as the time.
NAMES_IT = {"benchmarks/agent/test_provenance.py"}


def tracked() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    # git ls-files reads the INDEX, so a new file counts once it is staged --
    # which is what makes this runnable in a pre-commit hook (#251).
    return [ROOT / f for f in out]


def test_only_two_modules_configure_logging() -> None:
    offenders = sorted(
        str(p.relative_to(ROOT))
        for p in tracked()
        if str(p.relative_to(ROOT)) not in OWNERS | NAMES_IT
        and CALL.search(p.read_text())
    )
    assert not offenders, (
        f"{offenders} call logging.basicConfig directly. Two copies of a "
        "format string are two copies that drift; call logs.configure() (or "
        "provenance.configure() inside benchmarks/agent) instead."
    )


def test_the_datefmt_is_the_ledgers_own_shape() -> None:
    """One instant format for a row and for the line that describes it."""
    now = datetime.datetime(2026, 9, 9, 7, 0, 12, 481000).astimezone()
    assert backfill_iso8601.CANONICAL.match(now.strftime(logs.DATEFMT))


def emitted(fmt: str) -> str:
    buf = io.StringIO()
    logs.configure(stream=buf, fmt=fmt, force=True)
    logging.getLogger("t").info("hello")
    logging.shutdown()
    return buf.getvalue().rstrip("\n")


def test_a_real_line_starts_with_a_parseable_timestamp() -> None:
    """Assert against a line the logger wrote, not against the format string.

    A format constant can be right while the handler never uses it -- that is
    how `test_ds4_route.py` came to assert for months against a line ds4 has
    never printed.
    """
    stamp = emitted(logs.FORMAT).split()[0]
    assert backfill_iso8601.CANONICAL.match(stamp), stamp
    parsed = datetime.datetime.strptime(stamp, logs.DATEFMT)  # noqa: DTZ007
    assert parsed.tzinfo is not None, "the whole point of the format is the offset"


def test_the_default_asctime_is_what_this_is_avoiding() -> None:
    """Name the wrong answer, so the test says what it is protecting."""
    default = logging.Formatter().formatTime(
        logging.LogRecord("t", logging.INFO, "f", 1, "m", None, None)
    )
    assert " " in default and "T" not in default
    assert not backfill_iso8601.CANONICAL.match(default)


def test_a_report_renderer_carries_no_timestamp_at_all() -> None:
    """The narrow exemption, stated so it cannot widen by accident.

    Several scripts write a markdown table through the logger, because
    CLAUDE.md forbids print. A timestamp on every row makes the table
    unpastable, which is the whole output of the script. PLAIN has no
    asctime, so there is no timestamp to get wrong -- it is not a second
    date format.
    """
    assert "%(asctime)s" not in logs.PLAIN
    assert emitted(logs.PLAIN) == "hello"


def test_the_harness_stamp_uses_the_same_clock() -> None:
    """provenance adds fields to the line; it does not get its own dialect."""
    buf = io.StringIO()
    provenance.configure(stream=buf)
    logging.getLogger("t").info("hello")
    stamp = buf.getvalue().split()[0]
    assert backfill_iso8601.CANONICAL.match(stamp), stamp


def test_logging_goes_to_stdout_by_default() -> None:
    """`cmd > out.txt` must not produce an empty file (CLAUDE.md)."""
    logs.configure(force=True)
    handler = logging.getLogger().handlers[0]
    assert getattr(handler, "stream", None) is sys.stdout
