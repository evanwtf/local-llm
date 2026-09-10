"""One timestamp shape for every log line this repo writes.

Python's default `asctime` is `2026-09-09 07:00:12,481`. That is not ISO
8601 and it is not what anything else here writes:

    default asctime   2026-09-09 07:00:12,481
    the ledger        2026-09-09T07:00:12-0400
    a manifest row    2026-09-09T07:00:12-0400

A space where ISO 8601 wants `T`, a comma where it wants a dot, and -- the
part that actually costs something -- **no offset**. A line with no offset
cannot be placed on a clock without knowing which machine wrote it and what
its zone was at the time, and this project already paid for that once:
`scripts/backfill_iso8601.py` exists because naive local timestamps in
`results-*.jsonl` were being compared against `Z` timestamps in the
manifests, which is a four-hour error with no error message.

Log lines are joined to rows by hand all the time -- "the server said X at
07:00:12, which row was that?" -- so they get the ledger's shape, offset and
all. `DATEFMT` here and `CANONICAL` in `backfill_iso8601.py` describe the
same instant format; `tests/test_logging_format.py` asserts a line written
with this config parses under the ledger's own regex.

Setting `datefmt` drops the milliseconds. That is deliberate: `,481` is what
makes the default invalid, nothing in this repo has ever read a sub-second
field off a log line, and a driver logs at second granularity anyway.

    import logs
    logs.configure()                       # a driver: stamped lines
    logs.configure(fmt=logs.PLAIN)         # a report: the table, nothing else

**A report renderer keeps `PLAIN`.** Several scripts here write a markdown
table through the logger, because CLAUDE.md forbids `print`. Prefixing every
row of that table with a timestamp makes the output unpastable, which is the
whole point of the script. `PLAIN` carries no timestamp, so there is no
timestamp to format wrongly -- these are the only lines exempt, and the
guard test knows about them.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

#: ISO 8601 with an explicit offset, matching what the ledger writes.
DATEFMT = "%Y-%m-%dT%H:%M:%S%z"

#: The default for anything that does work: when, who, how bad, what.
FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

#: For a script whose output IS the message -- a rendered table, a report.
PLAIN = "%(message)s"


def configure(
    level: int = logging.INFO,
    *,
    stream: TextIO | None = None,
    fmt: str = FORMAT,
    force: bool = False,
) -> None:
    """Set up root logging on stdout, with ISO 8601 timestamps.

    stdout, not the stderr default: a CLI's output belongs there, and with
    the default `cmd > out.txt` writes an empty file.

    `force` defaults to **False**, which is `basicConfig`'s own default and
    what every call site here relied on before this module existed. It is not
    a nicety: `force=True` tears down the root logger's existing handlers,
    and under pytest one of those is `caplog`'s. Defaulting it to True made
    50 tests fail at once -- each one asserting on a message the script had
    written correctly, to a handler that had just been removed. A test that
    needs to read a line back passes `force=True` deliberately.
    """
    logging.basicConfig(
        level=level,
        stream=stream or sys.stdout,
        format=fmt,
        datefmt=DATEFMT,
        force=force,
    )
