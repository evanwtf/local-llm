"""One place that answers "which code produced this line?".

Rows in results.jsonl have carried provenance since `273c499`. Nothing else
did: log lines, tool output and test runs were all unattributable, so any
figure pasted into an issue or a commit message could not be traced back to the
code that computed it. This project has published three sets of numbers that
measured its own bugs; an unattributable number is how that survives review.

Every entry point calls `configure()` instead of `logging.basicConfig`, which
stamps the harness commit into each line:

    2026-09-01 07:12:03 INFO [a1b2c3d] preflight: 0.0 GiB held by model servers

A `-dirty` suffix means the working tree had uncommitted changes, so the line
was produced by code that exists nowhere but this machine.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
UNKNOWN = "nogit"


def _git(*args: str, cwd: pathlib.Path = HERE) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


# Files a run legitimately appends to. A benchmark writes results.jsonl, so
# treating that as "dirty" would flag every run after the first and the flag
# would stop meaning anything. `-dirty` must mean the CODE is uncommitted.
DATA_SUFFIXES = (".jsonl", ".log")


def _code_is_dirty(cwd: pathlib.Path) -> bool:
    status = _git("status", "--porcelain", cwd=cwd)
    if not status:
        return False
    for line in status.splitlines():
        path = line[3:].strip().strip('"')
        # A rename is "old -> new"; judge the destination.
        path = path.split(" -> ")[-1]
        if not path.endswith(DATA_SUFFIXES):
            return True
    return False


@functools.cache
def head(cwd: pathlib.Path = HERE) -> str:
    """Short HEAD, with `-dirty` when uncommitted CODE is present.

    Data files a run appends to (`.jsonl`, `.log`) do not count: the first
    trial of any batch modifies results.jsonl, and a flag that fires on every
    run is a flag nobody reads.

    Cached: read once per process, so a commit made mid-run cannot make two
    lines from the same run disagree.
    """
    sha = _git("rev-parse", "--short=7", "HEAD", cwd=cwd)
    if sha is None:
        return UNKNOWN
    return f"{sha}-dirty" if _code_is_dirty(cwd) else sha


class _Stamp(logging.Filter):
    """Attach the harness commit to every record, including library ones."""

    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value

    def filter(self, record: logging.LogRecord) -> bool:
        record.harness = self.value
        return True


def configure(
    level: int = logging.INFO,
    *,
    stream=None,
    show_name: bool = False,
) -> str:
    """Configure root logging so every line names the code that wrote it.

    Returns the stamp, so a caller can print it somewhere else too.
    """
    stamp = head()
    name = "%(name)s " if show_name else ""
    logging.basicConfig(
        level=level,
        stream=stream or sys.stdout,
        format=f"%(asctime)s {name}%(levelname)s [%(harness)s] %(message)s",
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_Stamp(stamp))
    return stamp


def fingerprint(path: pathlib.Path) -> str:
    """Identify a data file by content, not by mtime or by path.

    Generated documents record this rather than the HEAD commit: a table is a
    function of the data, so stamping it with a commit that changes on every
    unrelated edit would churn the file and train people to ignore the diff.
    """
    if not path.exists():
        return "absent"
    raw = path.read_bytes()
    rows = raw.count(b"\n")
    return f"{rows} rows, sha256 {hashlib.sha256(raw).hexdigest()[:12]}"
