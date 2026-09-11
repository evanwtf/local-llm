"""Count ds4-server prefill-failure 500s in a server log, per trial (#266).

ds4-server returns HTTP 500 with a body like::

    {"error":{"message":"metal Qwen prefill failed at position 16729", ...}}

and the client retries. The retry usually succeeds, so the task completes and
the row reads ``passed: true`` -- a reader sees a slightly slow arm, not a
failing one. But every such 500 is a re-prefill the control arm never does, so
it is a confound in every MTP-versus-control wall-time and drafting-share
comparison this project holds (#266, and the drafting-share denominator #235
reads). Counting the failures on the row makes the retry visible at read-out
instead of in a log nobody opens.

Only ds4 MTP arms have ever produced this line; a control arm counts zero. The
match is plain text, independent of the draft counters, so a control arm that
runs with no draft counters at all is still measured -- which is the whole
point, since the comparison is against exactly that arm.

The per-trial windowing mirrors `mtp_timing.read_since`: a trial is credited
only with the failures inside its own byte slice of a log that spans the whole
sweep, and a shrunk log (rotation, or a new server on the same path) rereads
from the start rather than seeking past the end.
"""

from __future__ import annotations

import dataclasses
import logging
import pathlib
import re

logger = logging.getLogger(__name__)

#: ds4-server's own wording for the 500, and deliberately the same pattern the
#: #266 report counts 184 failures with: `prefill failed at position [0-9]*`.
#: Matching it means this agrees with the issue's authoritative number rather
#: than a stricter pattern that would undercount it. The digits are required so
#: a log line that merely says "prefill failed" for another reason is not
#: counted; the phrase is ds4-server's error string and appears only in the 500.
_PATTERN = re.compile(r"prefill failed at position \d+")


def count(text: str) -> int:
    """How many prefill-failure 500s appear in `text`."""
    return len(_PATTERN.findall(text))


@dataclasses.dataclass(frozen=True)
class Reading:
    """Failures seen in a slice of a log, and where that slice ended."""

    failures: int
    offset: int


def read_since(path: pathlib.Path, offset: int = 0) -> Reading:
    """Count prefill failures in the bytes appended to `path` since `offset`.

    A missing or unreadable log is zero failures at the same offset, never an
    error: this must never take a run down. The whole read is guarded, not only
    the `stat` -- a log can be rotated away between `stat` and `open`, and an
    unguarded `open` there would raise in the middle of writing a trial's row. A
    log that shrank resets to 0 rather than seeking past the end, because a
    negative slice would read as "no failures", the one answer that must not be
    manufactured.
    """
    try:
        size = path.stat().st_size
        if size < offset:
            logger.warning(
                "%s shrank (%d < %d) -- rereading from the start", path, size, offset
            )
            offset = 0
        with path.open("r", errors="replace") as handle:
            handle.seek(offset)
            text = handle.read()
            return Reading(failures=count(text), offset=handle.tell())
    except OSError as exc:
        logger.warning("could not read %s for prefill failures: %s", path, exc)
        return Reading(failures=0, offset=offset)


class Probe:
    """Per-trial prefill-failure count, read from the server log (#266).

    Constructed alongside `DraftProbe` and sampled once per trial. Like the
    draft probe it starts at the current end of the log, so a server's startup
    chatter and the smoke gate's own generation are never credited to trial 1.

    `None` from `sample()` means no server log was given -- unknown, not zero --
    the same distinction `mtp_timing` makes for the draft counters. A control
    arm that was given a server log samples `0`, which is a measured fact and
    the number the comparison needs.
    """

    def __init__(self, path: str | pathlib.Path | None):
        self.path = pathlib.Path(path).expanduser() if path else None
        self.offset = read_since(self.path).offset if self.path else 0

    def sample(self) -> int | None:
        if not self.path:
            return None
        reading = read_since(self.path, self.offset)
        self.offset = reading.offset
        return reading.failures
