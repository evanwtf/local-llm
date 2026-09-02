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


@functools.cache
def machine_slug() -> str:
    """A compact machine token, e.g. `M5-Max-128GB` or `Ryzen9-7900X-RTX3080Ti`.

    Goes on every log line. A line reading `[abc1234]` could have come from
    either machine; a run on DeepSeek on the MacBook must never be mistakable
    for one on ornith on the Linux box with a 3080 Ti.
    """
    try:
        import sys as _sys

        _sys.path.insert(0, str(REPO / "scripts"))
        import hardware_id

        facts, platform = hardware_id.facts_for_this_machine()
        return hardware_id.short_slug(facts, platform) or "unknown-machine"
    except Exception:  # noqa: BLE001 -- a stamp must never take a run down
        return "unknown-machine"


class _Stamp(logging.Filter):
    """Attach the harness commit and the machine to every record."""

    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value

    def filter(self, record: logging.LogRecord) -> bool:
        record.harness = self.value
        record.machine = machine_slug()
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
        format=f"%(asctime)s {name}%(levelname)s [%(harness)s@%(machine)s] %(message)s",
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


# --- the banner every script prints -----------------------------------------
#
# A script's output is evidence. Six weeks on, a pasted table cannot be
# re-derived unless it says which code, which machine, which engine builds and
# which moment produced it -- and this project has twice published numbers that
# measured something other than what the reader assumed.
#
# `configure()` stamps the harness commit onto every LINE. This stamps the
# context onto every RUN, once, at the top.


@functools.cache
def _cmd(*argv: str) -> str | None:
    """First line of a command's stdout, or None. Never raises, never blocks."""
    try:
        r = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip().splitlines()
    return out[0].strip() if out and r.returncode == 0 else None


@functools.cache
def engine_versions() -> dict[str, str]:
    """Which engine builds are installed right now. Best effort, cached.

    Absent engines are omitted rather than reported as unknown: a machine
    without ds4 is not a machine with a broken ds4.
    """
    got: dict[str, str] = {}
    for name, root in (
        ("llama.cpp", pathlib.Path.home() / "git/llama.cpp"),
        ("ds4", pathlib.Path.home() / "git/ds4"),
    ):
        if (root / ".git").exists():
            sha = _git("rev-parse", "--short", "HEAD", cwd=root)
            if sha:
                got[name] = sha
    ollama = _cmd("ollama", "--version")
    if ollama:
        got["ollama"] = ollama.replace("ollama version is ", "")
    opencode = _cmd("opencode", "--version")
    if opencode:
        got["opencode"] = opencode
    return got


def banner(log: logging.Logger, *, engines: bool = True, extra: str = "") -> None:
    """Log who, what, where and when, once, at the start of a run.

    `engines=False` for tools that touch no model -- reading X posts does not
    need llama.cpp's commit, and the subprocess calls are not free.
    """
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    local = now.astimezone()
    log.info(
        "%s (%s) | harness %s%s",
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        local.strftime("%H:%M:%S %Z"),
        head(),
        f" | {extra}" if extra else "",
    )

    try:  # preflight imports this module, so import it lazily to avoid a cycle
        import preflight

        facts = preflight.machine_facts()
    except Exception:  # noqa: BLE001 -- a banner must never take a run down
        facts = {}
    if facts:
        bits = [
            f"{facts.get('arch', '?')}",
            facts.get("cpu", "?"),
            f"{facts.get('cpu_count', '?')} cores",
            f"{facts.get('memory_gib', '?')} GiB",
        ]
        if facts.get("gpu") and facts["gpu"] != facts.get("cpu"):
            bits.append(facts["gpu"])
        if facts.get("metal_ceiling_gib"):
            raised = " raised" if facts.get("metal_ceiling_raised") else " stock"
            bits.append(f"Metal ceiling {facts['metal_ceiling_gib']} GiB{raised}")
        bits.append(f"confinement {facts.get('confinement', '?')}")
        log.info("  machine: %s", ", ".join(str(b) for b in bits))

    if engines:
        got = engine_versions()
        if got:
            log.info(
                "  stack:   %s", " | ".join(f"{k} {v}" for k, v in sorted(got.items()))
            )


# --- keeping the output ------------------------------------------------------

REPO = HERE.parent.parent


def log_path(script: str, *, machine_specific: bool = True) -> pathlib.Path:
    """Where this script's output is kept, so it can be read again later.

    Machine-specific output lives under the machine's own directory (#85):
    a preflight or a benchmark report only means anything next to the hardware
    that produced it. Sweeps do not -- what the field looked like on a given
    day is the same fact on either machine, so they are shared.

    One file per run, named for the UTC minute, so a day's runs sort and
    nothing is overwritten.
    """
    import datetime as _dt

    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = machine_slug()
    if machine_specific:
        try:
            import sys as _sys

            _sys.path.insert(0, str(REPO / "scripts"))
            import hardware_id

            facts, platform = hardware_id.facts_for_this_machine()
            machine = hardware_id.directory_name(facts, platform)
        except Exception:  # noqa: BLE001 -- never take a run down over a filename
            machine = "unknown-machine"
        base = REPO / "hardware" / machine / "logs"
    else:
        base = REPO / "logs" / "sweeps"
    # The slug is in the name as well as the directory: a file copied out of
    # its directory must still say which machine wrote it.
    return base / f"{script}-{slug}-{stamp}.log"


def tee(script: str, *, machine_specific: bool = True) -> pathlib.Path:
    """Send this run's log to a file as well as stdout, and return the path.

    Call after `configure()`. The file gets the same stamped format, so a log
    read six weeks later still names the commit that wrote each line.
    """
    path = log_path(script, machine_specific=machine_specific)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    root = logging.getLogger()
    fmt = root.handlers[0].formatter if root.handlers else None
    if fmt:
        handler.setFormatter(fmt)
    handler.addFilter(_Stamp(head()))
    root.addHandler(handler)
    return path
