"""What is already running, and what is it holding?

Run this before a benchmark batch. It answers one question the harness could
not previously ask: **is there an inference server up that this run does not
want?**

The case that prompted it. A GLM `llama-server` was left running after an
earlier session, holding 77.6 GiB of a 112 GiB Metal budget, serving nothing.
Start a benchmark against a different backend in that state and one of two
things happens, both bad:

  * the new model does not fit and fails to load, which is loud and cheap; or
  * it does fit, and the two contend for memory and bandwidth for hours. That
    is quiet and expensive -- the whole batch is a timing measurement of a
    machine that was doing something else. METHODOLOGY already records an hour
    lost to a 96 GB download overlapping a batch. A resident 77.6 GiB model is
    the same mistake with no download to notice.

The check is advisory. It warns and never refuses: the operator may be running
two servers deliberately, and a benchmark harness that will not start because it
disapproves of the process table is worse than one that says what it sees.

Standalone:

    uv run python benchmarks/agent/preflight.py

`run.py` calls it before the first trial with the selected backends' ports, so
a stale server is named at the top of the log rather than inferred from the
numbers a week later.
"""
from __future__ import annotations

import dataclasses
import logging
import subprocess
import sys
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Substrings identifying a process that serves a model. Matched against the
# command line, so `./build/bin/llama-server` and a bare `ollama serve` both
# land. Deliberately not the shim: it holds no weights and its memory is noise.
INFERENCE = ("llama-server", "ollama", "ds4-server", "mtplx")

# The Metal budget after issue #30's sysctl. Not read from the system: the
# sysctl reports 0 whether or not a limit is in force, so a probe is the only
# honest source and this is a stated assumption instead.
DEFAULT_CEILING_GIB = 112.0

KIB_PER_GIB = 1048576

# Below this, an unexpected server is an idle daemon rather than loaded weights.
# Ollama in particular runs persistently at a couple of GiB with nothing
# resident, and it is usually up on purpose. Warning about it on every run would
# make the warning routine, and a routine warning is one nobody reads. Anything
# holding this much has a model in memory and is worth a sentence.
SIGNIFICANT_GIB = 8.0


@dataclasses.dataclass(frozen=True)
class Proc:
    pid: int
    rss_gib: float
    command: str
    port: int | None = None

    @property
    def short(self) -> str:
        return self.command.split()[0].rsplit("/", 1)[-1]


def parse_ps(text: str) -> list[Proc]:
    """Read `ps -eo pid,rss,command`, keeping only model servers.

    The command column contains spaces, so the split is bounded at 2. RSS is
    KiB on macOS.
    """
    procs = []
    for line in text.splitlines()[1:]:          # skip the header
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, rss, command = parts
        if not any(marker in command for marker in INFERENCE):
            continue
        try:
            procs.append(Proc(int(pid), int(rss) / KIB_PER_GIB, command.strip()))
        except ValueError:
            continue
    return procs


def parse_lsof(text: str) -> dict[int, int]:
    """Read `lsof -nP -iTCP -sTCP:LISTEN` into {port: pid}.

    The NAME column is `127.0.0.1:8030` or `*:49152`, so the port is what
    follows the last colon either way.
    """
    ports: dict[int, int] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or "(LISTEN)" not in line:
            continue
        for field in parts:
            if ":" not in field:
                continue
            try:
                port = int(field.rsplit(":", 1)[1])
                pid = int(parts[1])
            except ValueError:
                continue
            ports[port] = pid
            break
    return ports


def backend_ports(backends: dict[str, dict]) -> set[int]:
    """Every port the selected backends legitimately occupy.

    Includes `props_url`: a backend behind the Claude Code shim names the real
    server there, and both ends of that pair are ours.
    """
    ports = set()
    for backend in backends.values():
        for key in ("base_url", "props_url"):
            url = backend.get(key)
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.port:
                ports.add(parsed.port)
    return ports


@dataclasses.dataclass(frozen=True)
class Report:
    stale: list[Proc]
    unmatched: list[Proc]
    total_gib: float
    headroom_gib: float

    def warnings(self) -> list[str]:
        out = []
        for p in self.stale:
            out.append(
                f"{p.short} (pid {p.pid}) is listening on :{p.port} and holding "
                f"{p.rss_gib:.1f} GiB, but no selected backend uses that port. "
                f"Stop it, or this batch measures a contended machine."
            )
        for p in self.unmatched:
            out.append(
                f"{p.short} (pid {p.pid}) is holding {p.rss_gib:.1f} GiB and is "
                f"not listening yet -- still loading, or wedged."
            )
        return out


def check(ps_text: str, lsof_text: str, expected_ports: set[int],
          ceiling_gib: float = DEFAULT_CEILING_GIB) -> Report:
    """Compare what is running against what this run expects to use."""
    listeners = parse_lsof(lsof_text)
    by_pid = {pid: port for port, pid in listeners.items()}
    stale, unmatched, total = [], [], 0.0
    for proc in parse_ps(ps_text):
        total += proc.rss_gib
        port = by_pid.get(proc.pid)
        if port is None:
            unmatched.append(proc)
        elif port not in expected_ports and proc.rss_gib >= SIGNIFICANT_GIB:
            stale.append(dataclasses.replace(proc, port=port))
    return Report(stale, unmatched, total, ceiling_gib - total)


def _capture(argv: list[str]) -> str:
    got = subprocess.run(argv, capture_output=True, text=True, check=False,
                         stdin=subprocess.DEVNULL, timeout=30)
    return got.stdout


def inspect(backends: dict[str, dict] | None = None) -> Report:
    """Run the check against this machine right now."""
    return check(
        _capture(["ps", "-eo", "pid,rss,command"]),
        _capture(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]),
        backend_ports(backends or {}),
    )


def log_report(report: Report) -> None:
    """Say what was found, at a level matching how much it matters."""
    logger.info("preflight: %.1f GiB held by model servers, %.1f GiB headroom "
                "under a %.0f GiB ceiling",
                report.total_gib, report.headroom_gib, DEFAULT_CEILING_GIB)
    for warning in report.warnings():
        logger.warning("preflight: %s", warning)


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    report = inspect()
    log_report(report)
    if not report.stale and not report.unmatched:
        logger.info("preflight: nothing else is serving a model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
