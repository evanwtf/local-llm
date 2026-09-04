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

import argparse
import dataclasses
import json
import logging
import os
import pathlib
import platform
import subprocess
import sys
import time
from urllib.parse import urlparse

import opencode_config
import provenance
import staleness

logger = logging.getLogger(__name__)

# Substrings identifying a process that serves a model. Matched against the
# command line, so `./build/bin/llama-server` and a bare `ollama serve` both
# land. Deliberately not the shim: it holds no weights and its memory is noise.
INFERENCE = ("llama-server", "ollama", "ds4-server", "mtplx")

# The tool shim's script name. The shim is not an inference process, but it
# knows where the real server lives: its --upstream names the port that a
# shim-backed run depends on (#132).
SHIM_SCRIPT = "ds4_qwen_tool_shim.py"

# This repository's CI, checked at preflight so a red main is learned at the
# start of a session instead of seventeen hours later (#129). CI is the only
# observer of breakage that needs a host other than this machine -- the local
# suite stays green through it.
CI_REPO = "evanwtf/local-llm"
CI_BRANCH = "main"
# Conclusions that count as red. Everything without a verdict --
# in progress, cancelled -- is not evidence either way.
CI_RED = frozenset({"failure", "timed_out", "startup_failure"})
CI_NO_VERDICT = frozenset({None, "", "in_progress", "cancelled"})

# The stock Metal working set on this machine, measured with a Metal probe
# before #30's sysctl was applied. Used when no override is in force.
STOCK_CEILING_GIB = 107.52

# Kept as the name other code imports; it is now the *stock* value and the real
# ceiling is computed per run by `ceiling_gib`.
DEFAULT_CEILING_GIB = STOCK_CEILING_GIB

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


def _first_match(text: str, prefix: str) -> str | None:
    """The value after the first line beginning with `prefix`."""
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.split(":", 1)[1].strip() if ":" in line else None
    return None


def total_memory_gib() -> float | None:
    """Physical RAM, portably. None when it cannot be read.

    macOS reports bytes from a sysctl; Linux reports kB in /proc/meminfo. Both
    are cheap enough to read on every run, which is the point: the machine is a
    variable and it has to be on the row, not in a README.
    """
    if sys.platform == "darwin":
        raw = _capture(["sysctl", "-n", "hw.memsize"]).strip()
        try:
            return round(int(raw) / 1024**3, 1)
        except ValueError:
            return None
    meminfo = pathlib.Path("/proc/meminfo")
    if meminfo.exists():
        kb = _first_match(meminfo.read_text(), "MemTotal")
        if kb:
            try:
                return round(int(kb.split()[0]) / 1024**2, 1)
            except (ValueError, IndexError):
                return None
    return None


def cpu_model() -> str | None:
    """The CPU's own name for itself."""
    if sys.platform == "darwin":
        return _capture(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or None
    cpuinfo = pathlib.Path("/proc/cpuinfo")
    if cpuinfo.exists():
        return _first_match(cpuinfo.read_text(), "model name")
    return None


def gpu_description() -> str | None:
    """What will actually run the model.

    On Apple Silicon the GPU is the SoC, so the chip name is the honest answer
    and `cpu_model()` already has it. On a discrete-GPU box the VRAM is the
    binding constraint and it is nothing like the system RAM -- 12 GiB against
    30 GiB on the machine that motivated this -- so reporting only RAM would
    describe the wrong limit.
    """
    if sys.platform == "darwin":
        return cpu_model()
    try:
        raw = _capture(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader",
            ]
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return raw.splitlines()[0].strip() if raw else None


def machine_facts() -> dict[str, object]:
    """Interrogate the machine on every run, rather than assuming last time's.

    Every field here was previously either hardcoded, macOS-only, or absent.
    `machine` came from a Darwin sysctl and was simply missing on Linux; the
    Metal ceiling was invented off Darwin (#81). A benchmark whose rows cannot
    say what hardware produced them cannot be compared across machines -- and
    #20 adds a second machine deliberately, so this stops being hypothetical.
    """
    facts: dict[str, object] = {
        "arch": platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu_model(),
        "cpu_count": os.cpu_count(),
        "memory_gib": total_memory_gib(),
        "gpu": gpu_description(),
        "confinement": confinement(),
    }
    ceiling, raised = metal_ceiling()
    if ceiling is not None:
        facts["metal_ceiling_gib"] = ceiling
        facts["metal_ceiling_raised"] = raised
    return {k: v for k, v in facts.items() if v is not None}


def has_metal() -> bool:
    """Whether a Metal ceiling is a meaningful thing to report here."""
    return sys.platform == "darwin"


def confinement() -> str:
    """What actually confined the agent, for the row to record.

    `workspace_escapes` and `source_repo_intact` are what make a pass mean
    something, and `sandbox-exec` is macOS-only. Without this, a Linux row and
    a macOS row look identical in results.jsonl while carrying different
    guarantees (#81).
    """
    return "sandbox-exec" if sys.platform == "darwin" else "none"


def parse_wired_limit(text: str) -> float | None:
    """GiB from `sysctl iogpu.wired_limit_mb`, or None when no override is set.

    The sysctl reports the override in MB and **0 when there is none** -- which
    means "device default", not "no memory". Returning 0.0 GiB would be a lie
    that reads as a machine with no GPU budget at all.
    """
    if not text or ":" not in text:
        return None
    try:
        mb = int(text.split(":", 1)[1].strip())
    except ValueError:
        return None
    return mb / 1024 if mb > 0 else None


def ceiling_gib(text: str) -> float:
    """The Metal ceiling actually in force: the override, or the stock value."""
    got = parse_wired_limit(text)
    return got if got is not None else STOCK_CEILING_GIB


def parse_ps(text: str) -> list[Proc]:
    """Read `ps -eo pid,rss,command`, keeping only model servers.

    The command column contains spaces, so the split is bounded at 2. RSS is
    KiB on macOS.
    """
    procs = []
    for line in text.splitlines()[1:]:  # skip the header
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, rss, command = parts
        # Match the executable, not the whole command line. A shell running a
        # script that merely mentions llama-server has the marker in its
        # arguments, and matching those made this tool report the shell that
        # invoked it. That is the same self-match NEXT.md records for
        # `pgrep -f run.py`, and it is worth not rediscovering twice.
        binary = command.split()[0] if command.split() else ""
        if not any(marker in binary for marker in INFERENCE):
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


def _argv_value(command: str, flag: str) -> str | None:
    """The value of `flag` in a command line: after it, or joined by `=`.

    Neither `--port 8101` nor `--upstream=http://…` is privileged, so both
    spellings parse. None when the flag is absent -- which is also the guard
    against the self-match trap: a line that merely mentions the shim script
    without both flags parses to nothing and shields nothing.
    """
    tokens = command.split()
    for i, token in enumerate(tokens):
        if token == flag:
            return tokens[i + 1] if i + 1 < len(tokens) else None
        if token.startswith(flag + "="):
            return token[len(flag) + 1 :]
    return None


def shim_upstream_ports(ps_text: str, selected_ports: set[int]) -> set[int]:
    """Ports held by the upstream of a selected backend's shim.

    A backend behind the tool shim names only the shim in `base_url`, so
    `backend_ports()` expects :8101 -- while the model lives in the ds4-server
    upstream on :8000, holding real memory. Warning about that server is right
    about the ports and wrong about the conclusion: stopping it would kill the
    run's only backend (#132). The running argv is the one place both ports are
    named truthfully, so the association is read from it and cannot drift from
    `tasks.toml`.

    Only a shim whose own --port is selected is honoured: a shim left over
    from a different run must not shield its upstream.
    """
    ports: set[int] = set()
    for line in ps_text.splitlines():
        if SHIM_SCRIPT not in line:
            continue
        shim_port = _argv_value(line, "--port")
        upstream = _argv_value(line, "--upstream")
        if shim_port is None or upstream is None:
            continue
        try:
            if int(shim_port) not in selected_ports:
                continue
            parsed = urlparse(upstream)
        except ValueError:
            continue
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


def check(
    ps_text: str,
    lsof_text: str,
    expected_ports: set[int] | None,
    ceiling_gib: float = DEFAULT_CEILING_GIB,
) -> Report:
    """Compare what is running against what this run expects to use.

    `expected_ports=None` means "no run is being planned" -- the standalone
    case, where the tool is being asked what is up rather than whether it
    conflicts. Nothing is stale then, because nothing was selected. Passing an
    empty set instead would mark every running server stale and warn on a
    perfectly healthy machine, which is how a warning becomes noise.
    """
    listeners = parse_lsof(lsof_text)
    if expected_ports is not None:
        # A backend behind the tool shim names only the shim's port in its
        # spec; the shim's argv names the real server behind it. Both are
        # expected, or the run's only backend gets called stale (#132).
        expected_ports = expected_ports | shim_upstream_ports(ps_text, expected_ports)
    by_pid = {pid: port for port, pid in listeners.items()}
    stale, unmatched, total = [], [], 0.0
    for proc in parse_ps(ps_text):
        total += proc.rss_gib
        port = by_pid.get(proc.pid)
        if port is None:
            # A server still loading holds real memory; a process holding
            # nothing is not worth a sentence either way.
            if proc.rss_gib >= SIGNIFICANT_GIB:
                unmatched.append(proc)
        elif (
            expected_ports is not None
            and port not in expected_ports
            and proc.rss_gib >= SIGNIFICANT_GIB
        ):
            stale.append(dataclasses.replace(proc, port=port))
    return Report(stale, unmatched, total, ceiling_gib - total)


def _capture(argv: list[str]) -> str:
    got = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return got.stdout


def metal_ceiling() -> tuple[float, bool]:
    """(ceiling in GiB, whether an override is in force) for this machine now.

    This is the machine fact that silently decides whether a large model loads,
    and it was **not persisted until 2026-09-01**, when
    scripts/install-metal-ceiling.sh made it survive a reboot (#30). It
    explained an entire
    disagreement with upstream: ds4 plans a 108.01 GiB working set that fits
    under a raised 112 GiB ceiling and fails under the 107.52 GiB default, so
    the same binary and model "reproduced" for one machine and not another
    (antirez/ds4#890). Neither side checked it, because nothing reported it.
    """
    if not has_metal():
        # #81: on Linux the sysctl is absent, the fallback returned the macOS
        # 128 GiB-host default, and preflight printed "107.5 GiB headroom under
        # a 107.52 GiB Metal ceiling (stock)" on a 30 GiB box with no GPU
        # budget of that kind at all. A fabricated number is worse than an
        # absent one: it is the confident kind of wrong, on the machine fact
        # this project treats as load-bearing.
        return (None, False)
    raw = _capture(["sysctl", "iogpu.wired_limit_mb"])
    override = parse_wired_limit(raw)
    return (
        override if override is not None else STOCK_CEILING_GIB,
        override is not None,
    )


TENSOR_LINE = "has tensor"


def parse_metal_tensor(text: str) -> bool | None:
    """Whether llama.cpp's Metal backend has the tensor API on. None if unknown.

    On an M5 this decides whether prefill uses the Neural Accelerators or falls
    back to general-purpose shader ALUs. ggml-org/llama.cpp#27461 shipped a
    build where the probe failed on **every** M5: the tensor API compiled
    against a Metal language version that did not expose its headers, so
    `has_tensor` was cleared during device init and prefill quietly ran on the
    wrong units. The only symptom is one warning line and then normal output.

    We build with GGML_METAL_EMBED_LIBRARY=ON, which is why we are unaffected --
    and that is a build flag, not a law. #27461 also added a guard that clears
    `has_tensor` when the library comes from a pre-compiled metallib, so a
    future change to how we build could switch the M5's matmul units off with no
    error and no failing test. This is the check that would say so.
    """
    for line in text.splitlines():
        if TENSOR_LINE in line and "=" in line:
            return line.rsplit("=", 1)[1].strip() == "true"
    return None


def metal_tensor_api(llamacpp_root: pathlib.Path | None = None) -> bool | None:
    """Ask the local llama.cpp build whether the tensor API is live.

    `--list-devices` is the cheapest binary that initialises the Metal device;
    `--version` does not, so it reports nothing useful here.
    """
    root = (
        llamacpp_root
        or pathlib.Path(os.environ.get("LLAMACPP_ROOT", "~/git/llama.cpp")).expanduser()
    )
    for build in ("build2", "build"):
        binary = root / build / "bin" / "llama-bench"
        if binary.exists():
            try:
                # ggml logs device init to stderr, not stdout, so _capture()
                # alone reads an empty string and reports "unknown" for a
                # perfectly healthy build. Merge the streams.
                got = subprocess.run(
                    [str(binary), "--list-devices"],
                    capture_output=True,
                    text=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    timeout=60,
                )
                return parse_metal_tensor(got.stdout + got.stderr)
            except (OSError, subprocess.SubprocessError):
                return None
    return None


def inspect(backends: dict[str, dict] | None = None) -> Report:
    """Run the check against this machine right now.

    `backends=None` reports without judging: see `check`.
    """
    return check(
        _capture(["ps", "-eo", "pid,rss,command"]),
        _capture(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]),
        None if backends is None else backend_ports(backends),
        ceiling_gib=metal_ceiling()[0] or DEFAULT_CEILING_GIB,
    )


def log_report(report: Report) -> None:
    """Say what was found, at a level matching how much it matters."""
    ceiling, raised = metal_ceiling()
    if ceiling is None:
        logger.info(
            "preflight: %.1f GiB held by model servers; no Metal ceiling on "
            "this platform (confinement: %s)",
            report.total_gib,
            confinement(),
        )
    else:
        logger.info(
            "preflight: %.1f GiB held by model servers, %.1f GiB headroom "
            "under a %.2f GiB Metal ceiling%s",
            report.total_gib,
            ceiling - report.total_gib,
            ceiling,
            " (RAISED by sysctl, persisted by scripts/install-metal-ceiling.sh)"
            if raised
            else " (stock)",
        )
    for warning in report.warnings():
        logger.warning("preflight: %s", warning)

    # #78. On an M5 this decides whether prefill uses the Neural Accelerators.
    # Reported every run because the failure mode is silence: llama.cpp#27461
    # shipped with the probe broken on every M5 and the only symptom was one
    # warning line during device init.
    tensor = metal_tensor_api()
    if tensor is False:
        logger.warning(
            "preflight: llama.cpp reports 'has tensor = false' -- prefill will "
            "run on general-purpose ALUs, not the M5 Neural Accelerators. "
            "Check GGML_METAL_EMBED_LIBRARY (llama.cpp#27461)"
        )
    elif tensor is None:
        logger.debug("preflight: could not read the Metal tensor API state")
    else:
        logger.info("preflight: llama.cpp Metal tensor API is on")


# Source builds this project measures through. Checked offline against
# whatever refs the local clone has already fetched.
BUILDS = {
    "llama.cpp": pathlib.Path.home() / "git/llama.cpp",
    "llama.cpp-glm52pr": pathlib.Path.home() / "git/llama.cpp-glm52pr",
    "llama.cpp-glm53": pathlib.Path.home() / "git/llama.cpp-glm53",
    "ds4": pathlib.Path.home() / "git/ds4",
}

# A fetch older than this makes "0 commits behind" meaningless.
STALE_FETCH_DAYS = 2.0

# The reference implementation for this hardware. antirez ships models here
# first, often on a preview branch -- GLM-5.3-Flash landed on one while this
# project was benchmarking the model on an unsupported stack (#38). A new branch
# on this remote is a signal worth surfacing before a run, not after.
SHERPA = "ds4"

# Repos whose GitHub notifications bear on this project. Everything else is
# noise here -- 41 of 41 notifications on this account were CI failures from
# unrelated repos, and the one that mattered (a mention on a ds4 PR citing our
# measurement) was buried under them and already marked read by email.
WATCHED_REPOS = {"antirez/ds4", "ggml-org/llama.cpp", "evanwtf/local-llm"}


def log_versions(offline: bool = False) -> None:
    """Report drift in the tools and builds a batch is about to measure through.

    Advisory, like the rest of preflight. These move several times a day, and a
    batch started today can be measuring a stack upstream replaced last week --
    which is fine, because every row records its own versions, but it should be
    a decision rather than a surprise.
    """
    have = staleness.installed_versions()
    latest = staleness.latest_versions(offline=offline)
    for name in sorted(have):
        state = staleness.compare(have[name], latest.get(name))
        line = (
            f"{name}: installed {have[name] or 'not found'}, "
            f"latest {latest.get(name) or 'unknown'}"
        )
        if state == "behind":
            logger.warning("preflight: %s  <- BEHIND", line)
            if name == "ollama":
                warn_if_ollama_upgrade_changes_the_sampler(have[name], latest.get(name))
        elif state == "unknown":
            logger.info("preflight: %s (could not compare)", line)
        else:
            logger.info("preflight: %s (%s)", line, state)

    for note in staleness.interesting_notifications(
        staleness.fetch_notifications(), WATCHED_REPOS
    ):
        line = f"{note['repo']} [{note['reason']}] {note['type']}: {note['title']}"
        if note["reason"] in ("mention", "review_requested", "assign"):
            logger.warning("preflight: %s  <- addressed to you", line)
        else:
            logger.info("preflight: %s", line)

    sherpa = BUILDS.get(SHERPA)
    if sherpa is not None:
        for branch in staleness.new_remote_branches(sherpa):
            logger.warning(
                "preflight: %s has a recent branch %r you are not on "
                "-- antirez ships models on preview branches; check "
                "before concluding one does not run here",
                SHERPA,
                branch,
            )

    for name, path in BUILDS.items():
        got = staleness.git_drift(path)
        if not got:
            continue
        age = got["fetched_days_ago"]
        stale_fetch = age is not None and age > STALE_FETCH_DAYS
        note = ""
        if got["dirty"]:
            note += " [UNCOMMITTED CHANGES]"
        if stale_fetch:
            note += f" [last fetch {age:.0f} days ago -- run git fetch]"
        # `stale` comes from describe_drift, which knows a PR branch diverging
        # from master is not staleness. Warning on a correct state is how a
        # check becomes noise nobody reads.
        if got.get("stale"):
            logger.warning(
                "preflight: %s at %s is %s%s",
                name,
                got["head"],
                got.get("note", "behind"),
                note,
            )
        else:
            logger.info(
                "preflight: %s at %s -- %s%s",
                name,
                got["head"],
                got.get("note", "current"),
                note,
            )


def ci_streak(conclusions: list[str | None]) -> int:
    """Consecutive red conclusions, newest first -- the order `gh run list` gives.

    A run without a verdict (in progress, cancelled) neither extends nor
    breaks the streak: the reds behind it are still real, and a deliberate
    stop is not a red. Any other conclusion -- success, and anything this
    code has never heard of -- ends the streak. An unknown conclusion must
    not be read as 'no verdict', or a conclusion GitHub later invents could
    silence the warning.
    """
    streak = 0
    for conclusion in conclusions:
        if conclusion in CI_RED:
            streak += 1
        elif conclusion in CI_NO_VERDICT:
            continue
        else:
            break
    return streak


# ollama#16471 shipped in 0.33.3 and changed sampler precedence: model-authored
# GGUF and generation_config defaults now outrank Ollama's built-ins (#84).
# run.py holds the same constant for stamping rows; this one exists so
# preflight can say what crossing the line costs.
OLLAMA_SAMPLER_CHANGE = (0, 33, 3)


def _version_tuple(text: str | None) -> tuple[int, ...] | None:
    """Leading dotted integers of a version string, or None."""
    if not text:
        return None
    cleaned = text.strip().lstrip("v").split("-")[0]
    parts: list[int] = []
    for chunk in cleaned.split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts) or None


def warn_if_ollama_upgrade_changes_the_sampler(
    installed: str | None, latest: str | None
) -> None:
    """Say what an Ollama upgrade past 0.33.3 would do to the sampler (#84).

    Preflight already reports version drift, and for every other tool BEHIND
    means "you should probably upgrade". For Ollama across this one boundary
    it does not: #36 measured a sampler default nobody chose halving a pass
    rate -- top_p 0.95 gave 20/21 and 0.90 gave 7/15 on the same task, model,
    engine and client. ollama#16471 changes which of those a row gets. So an
    unqualified BEHIND on this line nudges toward the single action that
    silently invalidates comparability with every row already held.

    Naming the boundary does not decide it. Upgrading is fine; upgrading
    mid-series and pooling the rows is not.
    """
    before = _version_tuple(installed)
    after = _version_tuple(latest)
    if before is None or after is None:
        return
    if before >= OLLAMA_SAMPLER_CHANGE or after < OLLAMA_SAMPLER_CHANGE:
        return
    logger.warning(
        "preflight: that ollama upgrade crosses %s, where ollama#16471 makes "
        "model-authored GGUF sampler defaults outrank ollama's built-ins "
        "(#84). Every row this repo holds was taken under the old precedence. "
        "Upgrading is fine; upgrading mid-series and pooling the rows is not",
        ".".join(str(n) for n in OLLAMA_SAMPLER_CHANGE),
    )


# #133. A claim that this machine is busy measuring. `preflight` sees the
# process table; it cannot see intent, and the restart-between-trials protocol
# spends minutes with the server deliberately down. In that window a process
# scan truthfully reports "all clear" while the machine is committed to a
# multi-hour A/B, and a second run started there ruins both.
LOCK_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / ".run-lock.json"


def _pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists. Signal 0 checks, never kills."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists and belongs to somebody else. Alive is the safe reading.
        return True
    return True


def read_lock(path: pathlib.Path = LOCK_PATH) -> dict[str, object] | None:
    """The lock currently held, or None. A corrupt lock reads as held.

    An unparseable file is not evidence that nobody is running -- it is
    evidence that something went wrong while claiming the machine, which is
    exactly when a second run must not start.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return {"corrupt": True}
    try:
        got = json.loads(text)
    except ValueError:
        return {"corrupt": True}
    return got if isinstance(got, dict) else {"corrupt": True}


def lock_state(
    lock: dict[str, object] | None, hostname: str, pid: int
) -> tuple[str, str]:
    """Classify a lock: (state, one-line explanation).

    States: `free`, `ours`, `held`, `stale`, `foreign`, `corrupt`.

    `stale` is never stolen here. A dead lock is reported with everything it
    recorded, because that is what turns "something crashed" into "the 03:00
    arm A died", and taking it silently would throw that away.
    """
    if lock is None:
        return "free", "no lock held"
    if lock.get("corrupt"):
        return "corrupt", "the lock file is unreadable; assuming the machine is busy"
    host = lock.get("hostname")
    if host != hostname:
        return "foreign", (
            f"lock belongs to {host!r}, not this machine ({hostname!r}) -- "
            "a lock is a claim on one machine and must not travel"
        )
    holder = lock.get("pid")
    if not isinstance(holder, int):
        return "corrupt", "the lock records no usable pid"
    if holder == pid:
        return "ours", "this process already holds the lock"
    if _pid_alive(holder):
        what = lock.get("what") or "unspecified work"
        return "held", (
            f"pid {holder} is running {what} since {lock.get('started', 'unknown')}"
        )
    return "stale", (
        f"pid {holder} is gone. It recorded: {json.dumps(lock, sort_keys=True)}"
    )


def acquire_lock(
    what: str,
    path: pathlib.Path = LOCK_PATH,
    hostname: str | None = None,
    pid: int | None = None,
) -> tuple[bool, str]:
    """Claim the machine for `what`. Returns (acquired, message).

    Refuses -- hard -- when another live process on this machine holds it, or
    when the lock is corrupt or foreign. That is the one place this module is
    not advisory, and the asymmetry is deliberate: process detection is
    inferential and a resident server may be intentional, so it warns. A lock
    is an explicit declaration by a session that said what it was doing, so
    there is no ambiguity to be generous about.

    A stale lock is reported and NOT taken. Recovering from it is a decision
    with a name on it, not a side effect of the next run starting.
    """
    hostname = hostname or platform.node()
    pid = pid or os.getpid()
    state, why = lock_state(read_lock(path), hostname, pid)
    if state in ("held", "corrupt", "foreign"):
        return False, f"cannot take the run lock: {why}"
    if state == "stale":
        return False, (
            f"a stale run lock is in the way: {why}\n"
            f"Nothing is running. Remove {path} to proceed -- deliberately not "
            "automatic, so a crashed run is noticed rather than paved over."
        )
    claim = {
        "hostname": hostname,
        "pid": pid,
        "what": what,
        "started": _now_iso(),
        "cwd": str(pathlib.Path.cwd()),
    }
    try:
        # O_EXCL so two processes racing here cannot both win.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False, "cannot take the run lock: another process took it just now"
    except OSError as exc:
        return False, f"cannot take the run lock: {exc}"
    with os.fdopen(fd, "w") as fh:
        json.dump(claim, fh, indent=2, sort_keys=True)
    return True, f"run lock taken for {what!r} (pid {pid})"


def release_lock(
    path: pathlib.Path = LOCK_PATH,
    hostname: str | None = None,
    pid: int | None = None,
) -> tuple[bool, str]:
    """Drop our own lock. Never removes somebody else's."""
    hostname = hostname or platform.node()
    pid = pid or os.getpid()
    state, why = lock_state(read_lock(path), hostname, pid)
    if state == "free":
        return True, "no run lock to release"
    if state != "ours":
        return False, f"refusing to release a lock that is not ours: {why}"
    try:
        path.unlink()
    except OSError as exc:
        return False, f"could not release the run lock: {exc}"
    return True, "run lock released"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def check_client_versions(offline: bool = False) -> bool:
    """Record every client's version and name any that moved (#131).

    **This never refuses.** It used to: an installed client that differed
    from its pin returned True and `main` exited 1. On 2026-09-04 the
    operator removed the pinning and kept the recording, because this laptop
    is a daily driver first -- pinning the agent clients holds a developer's
    own tools back to serve a measurement, and a guard that gets overridden
    every time teaches people to type the override without reading it.

    What replaces it is not weaker, it is later: `client_version` is on every
    row and `results.py` refuses to write one without it, so a comparison can
    be split after the fact and the published tables caveat themselves.
    Prevention became recovery, deliberately.

    Returns False always, so the caller has nothing to decide. The bool is
    kept rather than dropped because test_preflight patches this name.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    try:
        import client_versions
    except ImportError:
        return False
    recorded = client_versions.load_recorded()
    if not recorded:
        return False
    for name, how in sorted(client_versions.autoupdate_status().items()):
        logger.info("preflight: %s self-update -- %s", name, how)
    installed = staleness.installed_versions()
    moved = client_versions.moved_since(installed, recorded)
    absent = [m for m in moved if m[2] == "not found"]
    changed = [m for m in moved if m[2] != "not found"]
    for name, want, _ in absent:
        logger.info(
            "preflight: %s recorded at %s but not installed here -- "
            "it cannot take a row (#131)",
            name,
            want,
        )
    for name, want, got in changed:
        # Not a warning and not an error: this line is the whole product of
        # not pinning, and it has to be findable in a log.
        logger.info(
            "preflight: SERIES BOUNDARY -- %s moved %s -> %s since "
            "client-versions.toml was written. Rows from here are a new "
            "series; client_version is on each one, so the split is "
            "recoverable (#131). Update client-versions.toml when convenient.",
            name,
            want,
            got,
        )
    if not moved:
        logger.info(
            "preflight: %d client versions match what is recorded", len(recorded)
        )
    _log_clients_behind(installed, offline=offline)
    return False


def _log_clients_behind(
    installed: dict[str, str | None], offline: bool = False
) -> None:
    """Warn when a client is older than its released version (#131).

    The operator's rule for this machine is to run the current version of
    everything -- it is a daily driver, and comparability is recovered from
    `client_version` on the row rather than bought by holding tools back. So
    the check that used to ask "has it drifted from the pin?" now asks the
    opposite question: **is it behind?**

    It warns and prints the upgrade command. It does not upgrade: that would
    move the version mid-batch, which is the failure #131 is about, and it is
    the operator's machine.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    try:
        import client_versions
    except ImportError:
        return
    latest = staleness.latest_versions(offline=offline)
    behind = client_versions.behind_latest(installed, latest)
    if not behind:
        logger.info("preflight: every client that could be checked is current")
        return
    for name, got, want in behind:
        logger.warning(
            "preflight: %s is %s, latest is %s -- this machine runs the current "
            "version of everything (#131). Upgrade with `%s`, and note that "
            "doing it mid-batch starts a new series.",
            name,
            got,
            want,
            client_versions.UPGRADE_COMMAND.get(name, f"upgrade {name}"),
        )


def log_ci_status(offline: bool = False) -> None:
    """Say whether this repository's CI on main is red (#129).

    Both red streaks this repo has had were found by a person going looking:
    the second ran 20 runs over 17 hours before anyone noticed, and the fix
    then took seven minutes. Preflight runs before every session, so this
    puts the check on the path that is always taken. Advisory like everything
    else here: it warns and never refuses, and a gh that is absent, failing,
    or answering garbage is an info line, not an error.
    """
    if offline:
        logger.info("preflight: CI status not checked (--offline)")
        return
    try:
        text = _capture(
            [
                "gh",
                "run",
                "list",
                "--repo",
                CI_REPO,
                "--branch",
                CI_BRANCH,
                "--limit",
                "5",
                "--json",
                "conclusion",
            ]
        )
        runs = json.loads(text)
        conclusions = [run.get("conclusion") for run in runs]
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        # Nothing here may break preflight, but a silent check is worse than
        # an honest one: say it could not tell.
        logger.info("preflight: could not read CI status (%s)", exc)
        return
    streak = ci_streak(conclusions)
    where = f"CI on {CI_REPO} {CI_BRANCH}"
    if streak >= 2:
        logger.warning(
            "preflight: %s is RED for the last %d runs -- "
            "gh run list --repo %s to see them. The local suite stays green "
            "through breakage only this host can see, so decide before "
            "building on this tree",
            where,
            streak,
            CI_REPO,
        )
    elif streak == 1:
        logger.info(
            "preflight: %s: 1 of the last %d runs is red -- could be a flake; "
            "look if it repeats",
            where,
            len(conclusions),
        )
    else:
        logger.info(
            "preflight: %s: no red in the last %d runs", where, len(conclusions)
        )


def main() -> int:
    # #54: a run killed mid-batch leaves the real repositories moved aside at
    # <name>-real with the export standing in their place. Restore before
    # anything else looks at them, so a crashed run never becomes a silent
    # wrong baseline.
    try:
        import run as _run

        restored = _run.restore_targets()
        if restored:
            logger.warning(
                "restored %s from a previous run that did not finish",
                ", ".join(restored),
            )
    except Exception as exc:  # noqa: BLE001 -- preflight must never hard-fail
        logger.error("could not check for stashed repositories: %s", exc)

    provenance.configure()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--offline",
        action="store_true",
        help="skip the network; use cached upstream versions",
    )
    p.add_argument(
        "--no-versions", action="store_true", help="report running servers only"
    )
    # #133. Acquisition and the memory check are one call on purpose: two
    # guards you can invoke separately are two guards somebody invokes zero of.
    p.add_argument(
        "--allow-client-drift",
        action="store_true",
        help="accepted and ignored: clients are recorded, not pinned, so "
        "nothing refuses on a client version any more (#131)",
    )
    p.add_argument(
        "--acquire-lock",
        metavar="WHAT",
        help="claim this machine for WHAT and exit non-zero if it is taken",
    )
    p.add_argument(
        "--release-lock", action="store_true", help="drop this process's run lock"
    )
    # preflight exits immediately, so recording ITS pid would make the lock
    # stale the moment it is written. The owner is whatever outlives this
    # call -- a shell script passes $$.
    p.add_argument(
        "--owner-pid",
        type=int,
        default=None,
        metavar="PID",
        help="process whose lifetime the lock tracks (default: this one)",
    )
    args = p.parse_args()

    if args.release_lock:
        ok, why = release_lock(pid=args.owner_pid)
        logger.info("preflight: %s", why) if ok else logger.error("preflight: %s", why)
        return 0 if ok else 1

    report = inspect()
    log_report(report)
    if args.acquire_lock:
        ok, why = acquire_lock(args.acquire_lock, pid=args.owner_pid)
        if not ok:
            logger.error("preflight: %s", why)
            return 1
        logger.info("preflight: %s", why)
    if not args.no_versions:
        log_versions(offline=args.offline)
        # #131: clients are recorded, not pinned. This never refuses -- it
        # names the series boundary so a later reader can split on it.
        check_client_versions(offline=args.offline)
        # #129. Gated with the versions block deliberately: --no-versions
        # means "report running servers only", and that is not a mode that
        # should reach the network.
        log_ci_status(offline=args.offline)
    # #69: an opencode_model that OpenCode cannot resolve makes the client
    # exit in 0.6s, and the row reads as a model failure. Cheap to check here.
    try:
        import tomllib

        with (pathlib.Path(__file__).parent / "tasks.toml").open("rb") as fh:
            opencode_config.log_report(tomllib.load(fh).get("backend", {}))
    except Exception as exc:  # noqa: BLE001 -- preflight must never hard-fail
        logger.error("could not check the opencode config: %s", exc)
    if not report.total_gib:
        logger.info("preflight: nothing is serving a model")
    elif not report.stale and not report.unmatched:
        logger.info("preflight: no unexpected server is holding memory")
    if report.total_gib:
        logger.info(
            "preflight: run this from run.py, or pass the backends you "
            "plan to use, to be told which of these is unexpected"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- served context (#79) --------------------------------------------------
#
# `context_tokens` in tasks.toml is what the backend ASKS for. Until 2026-09-02
# nothing checked what the server actually SERVED, and the two disagreed by 32x:
# Ollama serves a 4096 default unless a Modelfile sets `num_ctx`, while every
# desktop backend declared 131072 copied from the Mac's entries. A 9B model then
# ran a repository task in a 4096-token window, looped for 1566.9s, and wrote a
# row stamped `context_tokens: 131072`.
#
# That is the #78 family -- a server fact the row asserts without ever reading
# it back. The value only becomes observable once the model is resident, so this
# runs after the smoke gate rather than with the other preflight checks.


def served_context(model: str, base_url: str) -> int | None:
    """The context length Ollama has actually loaded `model` with.

    None when it cannot be determined -- the server is unreachable, the model is
    not resident, or the endpoint is not Ollama's. None means "cannot tell",
    which must never be reported as a mismatch.
    """
    import json
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/api/ps"
    try:
        with urllib.request.urlopen(url, timeout=10) as fh:
            data = json.loads(fh.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return None
    for entry in data.get("models") or []:
        name = str(entry.get("name", ""))
        if name == model or name.split(":")[0] == model.split(":")[0]:
            got = entry.get("context_length")
            return int(got) if got is not None else None
    return None


def check_served_context(backends: dict[str, dict]) -> list[str]:
    """Backends whose served context is smaller than the one they declare.

    Returns a list of human-readable mismatches, empty when every backend agrees
    or cannot be checked. Smaller is the failure that matters: a window shorter
    than the declared one silently truncates the task.
    """
    bad = []
    for name, spec in sorted(backends.items()):
        want = spec.get("context_tokens")
        model, base_url = spec.get("model"), spec.get("base_url")
        if not want or not model or not base_url:
            continue
        got = served_context(model, base_url)
        if got is None:
            continue
        if got < want:
            bad.append(
                f"{name}: declares context_tokens={want} but the server loaded "
                f"{model} with {got}. Set num_ctx in a Modelfile, or lower "
                f"context_tokens to what the card can hold."
            )
    return bad
