"""Which scaffolding-strip arm a shim served, recorded so a row can name it (#78).

ds4_qwen_tool_shim.py strips recovered tool-call scaffolding from returned
content (#112 remedy 2), and `SHIM_NO_STRIP=1` turns that off: the arm-defining
switch of the strip-toggle A/B. The 120 rows that A/B published are separable
only through a hand-kept manifest of run times
(results-112-strip-ab-manifest.jsonl), because nothing on a row says which arm
produced it -- two arms that differ in one env var looked identical in the
data. A backend's server identity is half of "what produced this row"; its
arm-defining switches are the other half, and this module carries the one we
have.

Same pattern as ds4_route, applied to the shim: the shim writes a record when
it starts, and the harness reads it back when a row is written. The rule is
copied deliberately -- **never guess.** A record whose pid no longer owns the
port is `unrecorded`, a fact, and must never resolve to a stamp, because a shim
restarted between trials is the normal case, not the exception.

A port with no record is not "unrecorded": it means no strip-shim fronts that
backend, so the row gets no `strip` key at all. That distinction is the point
-- absence of a shim and presence of an unverifiable one must not read the
same.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from collections.abc import Callable

import ds4_route  # listening_pid lives there: one lsof reader, two records

UNRECORDED = "unrecorded"

DEFAULT_RECORD_DIR = pathlib.Path(
    os.environ.get(
        "SHIM_STRIP_RECORD_DIR", pathlib.Path.home() / ".ds4" / "strip-records"
    )
)


def record_path_for(
    port: int, *, record_dir: pathlib.Path = DEFAULT_RECORD_DIR
) -> pathlib.Path:
    """One record file per port, so two shims can never overwrite each other."""
    return pathlib.Path(record_dir) / f"{int(port)}.json"


def write_record(
    *,
    strip: bool,
    port: int,
    pid: int,
    record_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Write the arm record for the shim just started on `port`."""
    path = record_path_for(port, record_dir=record_dir or DEFAULT_RECORD_DIR)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "strip": bool(strip),
        "port": int(port),
        "pid": int(pid),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    # Write-then-rename: a reader must never see half a record, and a crash
    # mid-write must leave the previous one intact rather than a truncated file.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)
    return path


def read_record(path: pathlib.Path) -> dict | None:
    """The record, or None. Absent, unreadable and corrupt are all None.

    This is provenance: it must not be able to take a trial down.
    """
    try:
        got = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return got if isinstance(got, dict) else None


def strip_for(
    port: int,
    *,
    record_dir: pathlib.Path = DEFAULT_RECORD_DIR,
    live_pid: Callable[[int], int | None] = ds4_route.listening_pid,
) -> str | None:
    """The strip arm serving `port` now: "on", "off", "unrecorded", or None.

    None means no record exists for this port, so no strip-shim fronts the
    backend. "unrecorded" means one does and its arm could not be verified --
    the record is stale, corrupt, or names a dead pid. The two must stay
    distinct: a row through no shim and a row through an unverifiable shim are
    different facts, and a caller that cannot tell them apart will stamp one of
    them wrongly.

    The record is only believed when the process it names is the process
    actually listening on the port it names. Without that check, a record left
    by the previous arm's shim would stamp this arm's rows with the other
    arm's setting -- the exact defect the A/B ran on.
    """
    path = record_path_for(port, record_dir=record_dir)
    if not path.exists():
        return None
    record = read_record(path)
    if record is None or not isinstance(record.get("strip"), bool):
        return UNRECORDED
    pid = live_pid(port)
    if pid is None or int(record.get("pid", -1)) != int(pid):
        return UNRECORDED
    return "on" if record["strip"] else "off"


__all__ = [
    "DEFAULT_RECORD_DIR",
    "UNRECORDED",
    "read_record",
    "record_path_for",
    "strip_for",
    "write_record",
]
