"""Which Metal route a ds4-server took, recorded so a row can name it (#149).

ds4 has two Metal kernel routes. The fast one uses the Metal 4 tensor API and
is ~21% quicker; the reference one is bit-exact and slower. **The fast route
flips the first sampled token on long prompts**, reproduced here to three
significant figures against ivanfioravanti's numbers, and one of the failing
cases is a code audit -- the shape of the whole agent suite.

Two facts make this worth a module rather than a comment:

- On any device whose name contains M5 the fast route **enables itself**. An
  unset `DS4_METAL_ENABLE_TENSOR` does not mean the reference kernels ran, so
  the environment cannot be read as the answer.
- `scripts/ds4_serve.py` already asserts the route from the server's own log
  before it lets a run start. That assertion was thrown away as soon as it
  passed, so `results.jsonl` holds fast-route and reference-route rows that
  are indistinguishable. Both #138 arms were confirmed same-route **by hand**.

So the route is written down when the server starts and read when a row is
written. The one rule: **never guess.** A record from yesterday's server, a
server somebody started outside the harness, a log that has not printed its
route line yet -- each of those is `unrecorded`, which is a fact. A field that
confidently names the wrong route is worse than an absent one, because nobody
goes back and checks it.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# One owner for the marker strings. ds4_serve.py refuses to start a server
# whose log does not carry the expected one; this module reads the same lines
# to say which one it was. Two copies would drift, and a drifted marker fails
# open -- the route reads "unrecorded" and nobody is told why.
from ds4_serve import ANY_MARKER, MARKERS

UNRECORDED = "unrecorded"

DEFAULT_RECORD = pathlib.Path(
    os.environ.get("DS4_ROUTE_RECORD", pathlib.Path.home() / ".ds4" / "route.json")
)


def route_from_log(text: str) -> str | None:
    """The route a server log names, or None if it does not name exactly one.

    Both markers mention the tensor API -- `fast` names the route it took,
    `vanilla` names the route it declined -- so presence alone cannot tell them
    apart, and a log holding both (a restart appending to one file) is refused
    rather than resolved by order.
    """
    present = [mode for mode, marker in MARKERS.items() if marker in text]
    return present[0] if len(present) == 1 else None


def write_record(
    path: pathlib.Path,
    *,
    mode: str,
    port: int,
    pid: int,
    log: str | pathlib.Path,
) -> None:
    """Write the route record for the server just confirmed on `port`."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "port": int(port),
        "pid": int(pid),
        "log": str(log),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    # Write-then-rename: a reader must never see half a record, and a crash
    # mid-write must leave the previous one intact rather than a truncated file.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


def read_record(path: pathlib.Path) -> dict | None:
    """The record, or None. Absent, unreadable and corrupt are all None.

    This is provenance: it must not be able to take a trial down.
    """
    try:
        got = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return got if isinstance(got, dict) else None


def record_from_log(
    log: pathlib.Path,
    *,
    port: int,
    pid: int,
    record_path: pathlib.Path = DEFAULT_RECORD,
) -> bool:
    """Read the route out of a server log and record it. False if it cannot.

    The entry point for the shell runners, which start `./ds4-server` directly
    and have a log path rather than a mode.
    """
    try:
        text = pathlib.Path(log).read_text(errors="replace")
    except OSError:
        return False
    mode = route_from_log(text)
    if mode is None:
        return False
    write_record(record_path, mode=mode, port=port, pid=pid, log=log)
    return True


def listening_pid(port: int) -> int | None:
    """The pid listening on `port` right now, via lsof. None if nothing is."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out[1:]:
        parts = line.split()
        if len(parts) > 1 and parts[1].isdigit():
            return int(parts[1])
    return None


def route_for(
    port: int,
    *,
    record_path: pathlib.Path = DEFAULT_RECORD,
    live_pid: Callable[[int], int | None] = listening_pid,
) -> str:
    """The route serving `port` now: "fast", "vanilla", or "unrecorded".

    The record is only believed when the process it names is the process
    actually listening on the port it names. That check is the whole point:
    without it, a record left by yesterday's server would stamp today's rows
    with a route they never ran.
    """
    record = read_record(record_path)
    if not record or record.get("mode") not in MARKERS:
        return UNRECORDED
    if int(record.get("port", -1)) != int(port):
        return UNRECORDED
    pid = live_pid(port)
    if pid is None or int(record.get("pid", -1)) != int(pid):
        return UNRECORDED
    return str(record["mode"])


__all__ = [
    "ANY_MARKER",
    "MARKERS",
    "UNRECORDED",
    "read_record",
    "record_from_log",
    "route_for",
    "route_from_log",
    "write_record",
]
