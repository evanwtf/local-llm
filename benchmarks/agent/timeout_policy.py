"""Stop a trial that is burning the clock without touching the GPU (#366).

Two policies, kept in one module so the reasoning lives in one place.

**Why this exists.** On the DGX Spark an agent trial sometimes "runs" for many
minutes while the GPU sits at idle *power* (about 8 to 12 W with a model
resident; the cluster's worker reads up to 12.2 W, #1450): the client
(opencode) is stuck not calling the model, so the trial rides its full per-step wall-clock
timeout doing nothing. GPU power is the honest signal here -- ``utilization.gpu``
reads 96% while weights merely load and 0% mid-trial during a tool phase, so it
lies in both directions. ``scripts/gpu_utilization.py`` already encodes the
power rule (``IDLE_WATTS``, ``is_busy``), and this module reuses it as the
global floor; ``TIER_IDLE_FLOOR_WATTS`` overrides it per hardware tier.
Power alone cannot tell a stuck client from a light decode on GB10 (#1450), so
where the engine exports a running-request gauge (``ENGINE_SIGNALS``) a sample
is idle only when the engine is idle too (#1453).

(A) ``IdleStallWatchdog`` + ``run_client_with_watchdog`` kill a single trial
whose GPU has been idle for a continuous window, well before its wall-clock
timeout, while the wall-clock stays as a backstop.

(B) ``cell_should_abort`` is a pure circuit-breaker: once enough trials of a
cell have run and more than half of them timed out, stop pouring machine-hours
into a cell that is not producing rows.

Both are built to be tested offline: the watchdog takes an injectable clock and
a plain watts sampler, and the circuit-breaker is a pure function. Nothing here
depends on ``nvidia-smi`` being present -- an unreadable GPU reads as ``None``,
which is held, never counted as idle.
"""

from __future__ import annotations

import logging
import math
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

# gpu_utilization lives in scripts/, which is not on the default import path.
# Insert it the same way provenance.py reaches scripts/lib: at import, so the
# idle-floor default and the watts sampler can both come from the one module
# that already owns the power rule.
_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import gpu_utilization
import memcap

logger = logging.getLogger(__name__)

#: A GPU power reading in watts, or None when the GPU cannot be read. None is
#: "unknown" -- never treated as idle -- so an unreadable sensor cannot abort a
#: trial that is actually working.
WattsSampler = Callable[[], float | None]

#: The global idle floor: at or above this the GPU is doing real work. Sourced
#: from gpu_utilization. It applies where no backend key and no tier floor
#: (TIER_IDLE_FLOOR_WATTS) says otherwise; see resolve_idle_floor.
IDLE_FLOOR_WATTS: float = gpu_utilization.IDLE_WATTS

#: The idle floor per hardware tier (``tier`` in tasks.toml), where the global
#: floor is wrong for that hardware (#1450). A light model on GB10 decodes
#: under 20 W per GPU, so the global floor killed a live trial there.
TIER_IDLE_FLOOR_WATTS: dict[str, float] = {
    # 13 W: above the GB10 loaded idle and below light-model decode (#1450).
    # The cluster reads the busier node, and the worker idles with a TP2 model
    # resident at 11.5-12.0 W average, 10-min peak median 11.8-12.2 W (head
    # 10.5-10.8 W). Inkling 2 Mini decodes at 15.8-17.3 W per GPU (lowest
    # 10-min peak 14.75 W). The #1450 review (60 days, 15 s steps): 12 W
    # missed 58% of 1,179 quiet 10-min windows, 13 W 19%; 0 Inkling-busy
    # windows fall under 13 W, 89 of 324 under 20 W.
    # scripts/idle_floor_evidence.py recounts it with back-to-back windows
    # (2026-11-21: quiet missed 43% at 12 W, 10% at 13 W; Inkling 0 of 43
    # killed at 13 W, 9 of 43 at 20 W).
    "gb10-spark": 13.0,
    "gb10-spark-x2": 13.0,  # same GPU; one floor for both tiers
}

#: Where a resolved idle floor came from, in precedence order. "engine" is
#: the engine-rule floor (#1453), which replaces "tier" and "default" there.
IdleFloorSource = Literal["flag", "backend", "tier", "default", "engine"]

#: The power floor of the engine rule (#1453). With the engine's word that no
#: request runs, the floor no longer has to sit under a light decode, so the
#: GB10 tier exception (13 W) is not needed and the global floor applies.
#:
#: Evidence (the #1453 review, 2026-11-27): a replay of all 460 trials of
#: 600 s or more on vLLM, llama.cpp and SGLang over 60 days. The engine rule
#: stops none of them, the 13 W power rule none, and the old 20 W power rule
#: two (the two vLLM gpu-idle-stall rows). The engine rule catches the real
#: stall of 2026-10-17 (0 running, tokens flat 07:52-08:02 UTC, 15.6-16.2 W),
#: which 13 W misses, and keeps the 2026-11-21 Inkling decode (1 running,
#: tokens rising, 15.6-16.9 W). scripts/idle_floor_evidence.py counts quiet
#: 10-min windows: 53 of 559 missed at 13 W, 44 under this rule (vLLM 15 -> 9,
#: llama.cpp 8 -> 5, TensorFold 30 -> 30). The 9 it newly catches overlap no
#: trial; they are idle hours with a model loaded. It still misses 9 vLLM and
#: 5 llama.cpp quiet windows. Its "killed" counts cannot show an engine
#: window by construction, so they are not evidence for this floor.
ENGINE_IDLE_FLOOR_WATTS: float = IDLE_FLOOR_WATTS


@dataclass(frozen=True)
class EngineSignal:
    """The families of an engine's own ``/metrics`` that say it is working.

    ``running`` is a gauge of the requests in progress now. ``progress`` is a
    counter of generated tokens: the gauge is a snapshot, and a short request
    between two samples shows only in the counter.
    """

    engine: str
    running: str
    progress: str


#: The engines whose ``/metrics`` the watchdog reads, by tasks.toml ``engine``
#: (#1453). A hung engine whose gauge stays above 0 is never called idle;
#: see #1546. Each name was checked against real output: vLLM's in Prometheus
#: (stored from a vLLM server; ``engine="0"``, ``model_name=...``), llama.cpp's
#: in Prometheus and in a live capture (b10809), SGLang's in a live capture
#: (nightly 2026-09-21; the exporter's llme_requests_running for SGLang is
#: mapped from it). Every other engine keeps the power rule:
#:
#: * TensorFold, by decision in #1453. Native TensorFold exports
#:   tensorfold:requests_running, so it can join; see #1545.
#: * ds4, Ollama, mlx-serve and the rest export no such signal.
ENGINE_SIGNALS: dict[str, EngineSignal] = {
    "vllm": EngineSignal(
        "vllm", "vllm:num_requests_running", "vllm:generation_tokens_total"
    ),
    "sglang": EngineSignal(
        "sglang", "sglang:num_running_reqs", "sglang:generation_tokens_total"
    ),
    "llama.cpp": EngineSignal(
        "llama.cpp", "llamacpp:requests_processing", "llamacpp:tokens_predicted_total"
    ),
}


@dataclass(frozen=True)
class EngineReading:
    """One read of an engine: requests running now, and its token counter."""

    running: float
    progress: float | None


#: One engine read, or None when the engine cannot be read (endpoint down, a
#: scrape gap, a body without the running gauge).
EngineSampler = Callable[[], "EngineReading | None"]

#: How long one engine read may take. The watchdog polls every 15 s, or
#: every 2 s when a client memory cap is on (DEFAULT_MEM_POLL_SECS), as on a
#: bare host.
ENGINE_TIMEOUT_SECS: float = 5.0

#: A continuous idle stretch this long (seconds) means the trial has stalled.
#: An agent trial alternates decode with tool and test phases, so a short
#: window would flag a healthy tool phase; ten minutes is long enough that a
#: live run cannot hide inside its gaps.
DEFAULT_IDLE_STALL_SECS: float = 600.0

#: How often the watchdog samples power while a client runs.
DEFAULT_POLL_SECS: float = 15.0

#: How long a killed process group is given to die on SIGTERM before SIGKILL.
DEFAULT_GRACE_SECS: float = 5.0

#: When a memory cap is active, sample at least this often regardless of the
#: (coarser) power-poll interval, so a fast runaway is caught with little
#: overshoot: at ~0.5 GiB/s a 15s poll overshoots ~7.5 GiB, a 2s poll ~1 GiB.
DEFAULT_MEM_POLL_SECS: float = 2.0


def positive_watts(value: object, what: str = "idle floor") -> float:
    """``value`` as a positive finite float, or ValueError naming ``what``.

    NaN compares false with everything, so a NaN floor reads every sample as
    idle and kills each live trial at the window; a floor at or below zero
    never sees idle and switches the watchdog off. Both are typos, not choices.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{what} must be a positive finite number, got {value!r}")
    return float(value)


def resolve_idle_floor(
    flag: float | None, backend: Mapping[str, object]
) -> tuple[float, IdleFloorSource]:
    """Return the idle floor for one backend, and where it came from (#1450).

    Precedence: an explicit ``--idle-floor-watts`` flag, then the backend's
    ``idle_floor_watts`` key in tasks.toml, then the default for the
    backend's ``tier``, then the global :data:`IDLE_FLOOR_WATTS`.

    A flag or backend key that is not a positive finite number raises
    ValueError: a typo must stop the run, not fall back silently to another
    floor or disable the watchdog.
    """
    if flag is not None:
        return positive_watts(flag, "--idle-floor-watts"), "flag"
    if "idle_floor_watts" in backend:
        value = backend["idle_floor_watts"]
        return positive_watts(value, "backend idle_floor_watts"), "backend"
    tier = backend.get("tier")
    if isinstance(tier, str) and tier in TIER_IDLE_FLOOR_WATTS:
        return TIER_IDLE_FLOOR_WATTS[tier], "tier"
    return IDLE_FLOOR_WATTS, "default"


def resolve_engine_idle_floor(
    flag: float | None, backend: Mapping[str, object]
) -> tuple[float, IdleFloorSource]:
    """The power floor of the engine rule (#1453), and where it came from.

    An explicit flag or backend key wins, as in :func:`resolve_idle_floor`.
    Otherwise :data:`ENGINE_IDLE_FLOOR_WATTS`, not the tier floor.
    """
    floor, source = resolve_idle_floor(flag, backend)
    if source in ("flag", "backend"):
        return floor, source
    return ENGINE_IDLE_FLOOR_WATTS, "engine"


def engine_metrics_url(backend: Mapping[str, object]) -> str | None:
    """The ``/metrics`` URL of the backend's engine, or None without a URL.

    ``engine_url`` names the engine behind a shim; else ``base_url``. In
    remote mode both already name the server (``remote.rewrite``).
    """
    url = backend.get("engine_url") or backend.get("base_url")
    if not isinstance(url, str) or not url:
        return None
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/metrics", "", ""))


def engine_signal_for(
    backend: Mapping[str, object],
) -> tuple[EngineSignal | None, str]:
    """The engine signal for a backend, or None for the power rule, and why."""
    engine = str(backend.get("engine") or "")
    signal = ENGINE_SIGNALS.get(engine)
    if signal is None:
        return None, (
            f"power rule: engine {engine or '(none)'} has no running-request "
            "signal the watchdog reads (#1453)"
        )
    if engine_metrics_url(backend) is None:
        return None, f"power rule: engine {engine} but no base_url to read"
    return signal, f"engine rule: {engine} {signal.running}"


def _family_sum(text: str, name: str) -> float | None:
    """The sum of every sample of one family in a /metrics body, or None."""
    total: float | None = None
    for line in text.splitlines():
        if not line.startswith(name):
            continue
        rest = line[len(name) :]
        if rest[:1] == "{":
            end = rest.find("}")
            if end < 0:
                continue
            rest = rest[end + 1 :]
        elif rest[:1] not in (" ", "\t"):
            continue  # a longer name: vllm:generation_tokens_created
        fields = rest.split()
        if not fields:
            continue
        try:
            value = float(fields[0])
        except ValueError:
            continue
        if math.isfinite(value):
            total = (total or 0.0) + value
    return total


def parse_engine_metrics(text: str, signal: EngineSignal) -> EngineReading | None:
    """The engine's reading from its /metrics body; None without the gauge.

    A body with no running gauge is unreadable, never "0 running": a
    llama-server without ``--metrics`` or a shim must not read as idle.
    """
    running = _family_sum(text, signal.running)
    if running is None:
        return None
    return EngineReading(running, _family_sum(text, signal.progress))


def fetch_engine_reading(
    url: str, signal: EngineSignal, timeout: float = ENGINE_TIMEOUT_SECS
) -> EngineReading | None:
    """One read-only GET of the engine's /metrics; None on any failure.

    Any failure, not a list of them: a cut body or a non-HTTP reply raises
    http.client.HTTPException, which is neither URLError nor OSError, and an
    exception here would end the poll loop (#1453 review).
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 -- a raise here ends the poll loop
        return None
    return parse_engine_metrics(body, signal)


class IdleStallWatchdog:
    """Track the current continuous GPU-idle stretch. Pure and threadless.

    Call :meth:`sample` on a cadence; ask :meth:`stalled` whether the idle
    stretch has reached the window. A reading at or above ``idle_floor_watts``
    resets the stretch; a reading below it starts or extends it; a ``None``
    reading is *held* -- the stretch neither resets nor grows, because an
    unreadable GPU is not evidence of idleness and must never trigger an abort.

    Held means the unreadable interval is not counted at all. Idle time is the
    sum of the intervals between two consecutive idle readings with nothing
    unreadable between them. Measuring from the stretch's first reading
    instead counted the whole gap: 10 W, then None through real GPU work, then
    10 W 601 s later read as 601 s idle and killed the trial (review of
    2026-10-05).

    The clock is injectable so the whole thing is testable without sleeping.
    :meth:`stalled` reads no clock of its own, so one poll costs exactly one
    clock read.
    """

    def __init__(
        self,
        watts: WattsSampler,
        *,
        idle_floor_watts: float = IDLE_FLOOR_WATTS,
        idle_floor_source: IdleFloorSource | None = None,
        idle_stall_secs: float = DEFAULT_IDLE_STALL_SECS,
        poll_secs: float = DEFAULT_POLL_SECS,
        monotonic: Callable[[], float] = time.monotonic,
        engine: EngineSampler | None = None,
        engine_floor_watts: float = ENGINE_IDLE_FLOOR_WATTS,
        engine_floor_source: IdleFloorSource = "engine",
        engine_name: str = "",
        power_only_reason: str = "",
    ) -> None:
        self._watts = watts
        #: #1453: the engine's running-request signal, or None for the power
        #: rule. With it, a sample is idle only when the engine reports no
        #: running request and no new tokens AND the GPU reads under
        #: ``engine_floor_watts``. A sample whose engine read fails is judged
        #: by the power rule against ``idle_floor_watts``, as before #1453.
        self._engine = engine
        self.engine_floor_watts = engine_floor_watts
        self.engine_floor_source = engine_floor_source
        self.engine_name = engine_name
        #: Why this watchdog has no engine signal, for the stall log line.
        self.power_only_reason = power_only_reason
        #: The engine's token counter at its last successful read.
        self._progress: float | None = None
        #: Idle samples in the open stretch, by the rule that judged them.
        self._judged = {"engine": 0, "power": 0}
        self.idle_floor_watts = idle_floor_watts
        #: Where the floor came from (#1450), for the stall log line. Not
        #: given: "default" for the global floor, else "caller", so a floor
        #: passed without a source never claims to be the global default.
        self.idle_floor_source: str = idle_floor_source or (
            "default" if idle_floor_watts == IDLE_FLOOR_WATTS else "caller"
        )
        self.idle_stall_secs = idle_stall_secs
        self.poll_secs = poll_secs
        self._monotonic = monotonic
        #: True while the last readable sample was idle: a stretch is open.
        self._idle = False
        #: Idle seconds counted in the open stretch, unreadable gaps excluded.
        self._idle_secs = 0.0
        #: The instant of the last idle reading with no unreadable one since,
        #: or None. The next idle reading counts the time from here.
        self._idle_at: float | None = None

    def sample(self) -> float | None:
        """Take one reading and fold it into the idle-stretch state."""
        watts = self._watts()
        if watts is None:
            # Unknown: hold. Keep the idle time already counted, and drop the
            # reference instant, so the gap up to the next reading is never
            # counted -- a run of Nones cannot make the stretch grow, nor
            # erase one that had begun.
            self._idle_at = None
            return None
        # Power at or above both floors is work under either rule, so skip the
        # engine read. That spares /metrics during decode: each llama.cpp read
        # resets the buckets behind its tokens-per-second gauges (#1453
        # review). The counter baseline then goes stale, so the first read
        # after it counts as work, and the stretch starts one sample later.
        if self._engine is not None and watts >= max(
            self.engine_floor_watts, self.idle_floor_watts
        ):
            working: bool | None = True
        else:
            working = self._engine_working()
        if working is None:
            rule, idle = "power", watts < self.idle_floor_watts
        else:
            rule, idle = "engine", not working and watts < self.engine_floor_watts
        now = self._monotonic()
        if not idle:
            self._idle = False
            self._idle_secs = 0.0
            self._idle_at = None
            self._judged = {"engine": 0, "power": 0}
            return watts
        if self._idle and self._idle_at is not None:
            self._idle_secs += now - self._idle_at
        self._idle = True
        self._idle_at = now
        self._judged[rule] += 1
        return watts

    def _engine_working(self) -> bool | None:
        """Did the engine work? None: no engine signal, or it was unreadable.

        Working means a request runs now, or the token counter moved since
        the last read. A counter that went down (a restart) moved too. A
        failed read keeps the last counter value, so tokens made across the
        gap still count as work at the next read.
        """
        if self._engine is None:
            return None
        reading = self._engine()
        if reading is None:
            return None
        moved = (
            reading.progress is not None
            and self._progress is not None
            and reading.progress != self._progress
        )
        if reading.progress is not None:
            self._progress = reading.progress
        return reading.running > 0 or moved

    def rule_summary(self) -> str:
        """Which rule judged the open idle stretch, for the stall log line."""
        power = f"below {self.idle_floor_watts:g} W ({self.idle_floor_source})"
        if self._engine is None:
            why = f"; {self.power_only_reason}" if self.power_only_reason else ""
            return f"power rule: {power}{why}"
        engine = (
            f"engine rule: {self.engine_name or 'engine'} reported no running "
            "request and no new tokens, and the GPU read below "
            f"{self.engine_floor_watts:g} W ({self.engine_floor_source})"
        )
        n_engine, n_power = self._judged["engine"], self._judged["power"]
        if not n_power:
            return engine
        blind = f"{self.engine_name or 'the engine'} could not be read"
        if not n_engine:
            return f"power rule: {power} for {n_power} samples: {blind}"
        return (
            f"{engine} for {n_engine} samples; power rule: {power} for "
            f"{n_power} samples, while {blind}"
        )

    def idle_seconds(self) -> float:
        """Idle seconds in the current stretch, as of the last readable sample."""
        return self._idle_secs if self._idle else 0.0

    def stalled(self) -> bool:
        """True once the stretch's counted idle time reaches the full window."""
        return self._idle and self._idle_secs >= self.idle_stall_secs


@dataclass
class ClientResult:
    """What a watched client run produced.

    ``returncode`` is None when the process was killed and never reported one.
    ``idle_stalled``, ``timed_out`` and ``memory_killed`` are the three ways the
    watchdog ended it; all False on a normal exit. ``peak_rss_gib`` is the
    largest tree-RSS sample taken during the run (0.0 when no cap was set).
    """

    stdout: str
    stderr: str
    returncode: int | None
    idle_stalled: bool
    timed_out: bool
    memory_killed: bool = False
    peak_rss_gib: float = 0.0


def _terminate_group(
    proc: subprocess.Popen[str],
    grace_secs: float,
    log: logging.Logger,
) -> None:
    """SIGTERM the child's process group, then SIGKILL it after a grace.

    A pid is a process; a trial is a tree. The client spawns a server-facing
    child, a shell, a test run -- killing the pid alone orphans the rest. The
    child was started with ``start_new_session=True``, so it leads its own
    group and one signal reaches the whole tree.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    # Snapshot the counted tree BEFORE signalling anything: once the client
    # dies its orphans are reparented and can no longer be found from it. A
    # descendant that left the group -- a detached tool call -- is in this
    # snapshot and not in the group, and on 2026-09-17 that is how a 24.8 GiB
    # runaway survived this kill and grew to 108,524 MiB (#485).
    tree = memcap.tree_pids(proc.pid, memcap._rss_kib_by_pid())
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_secs)
    except subprocess.TimeoutExpired:
        log.warning("client group %d did not exit on SIGTERM; sending SIGKILL", pgid)
    # Always SIGKILL the group -- do NOT return early just because the leader
    # exited. The client (opencode) can exit on SIGTERM while a descendant -- the
    # model-executed runaway this cap exists to stop -- ignores SIGTERM and keeps
    # allocating toward a global OOM (#379, found in review). SIGKILL to an
    # already-empty group is a harmless ProcessLookupError.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for pid in tree:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue


def run_client_with_watchdog(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None,
    env: dict[str, str] | None,
    timeout: float | None,
    watchdog: IdleStallWatchdog,
    poll_secs: float = DEFAULT_POLL_SECS,
    grace_secs: float = DEFAULT_GRACE_SECS,
    memory_cap_gib: float | None = None,
    rss_sampler: Callable[[int], float] = memcap.sample_tree_rss_gib,
    log: logging.Logger = logger,
) -> ClientResult:
    """Run a client subprocess under the idle-stall watchdog.

    Like run.py's ``run()`` -- ``stdin`` closed, stdout/stderr captured, text
    mode -- but via ``Popen`` with ``start_new_session=True`` so the child is
    its own process group and the whole tree can be signalled at once.

    The loop drains output with ``communicate(timeout=...)`` (which reads the
    pipes as it waits, so a chatty client cannot deadlock on a full buffer)
    and samples the watchdog each interval. A sustained idle stall ends the run
    early; the hard ``timeout`` stays as a wall-clock backstop. Whatever the
    child emitted is always collected before returning.
    """
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    start = time.monotonic()
    idle_stalled = False
    timed_out = False
    memory_killed = False
    peak_rss_gib = 0.0
    stdout = ""
    stderr = ""
    # With a memory cap on, wake often enough to catch a fast runaway before it
    # overshoots far past the cap; otherwise keep the coarse power-poll cadence.
    wait_secs = min(poll_secs, DEFAULT_MEM_POLL_SECS) if memory_cap_gib else poll_secs
    # Any exception out of the loop (a sampler that raises, an interrupt)
    # must take the client group with it. The client leads its own session,
    # so nothing else stops it, and the caller then deletes the worktree it
    # works in (#1453 review).
    try:
        while True:
            # Never wait past the deadline. A child that exited inside a poll
            # window after its deadline returned as a clean run, so the watched
            # path gave up to `wait_secs` more than the plain path (review of
            # 2026-10-05). At the deadline the wait times out and the check below
            # ends the run.
            wait = wait_secs
            if timeout is not None:
                wait = max(0.0, min(wait_secs, timeout - (time.monotonic() - start)))
            try:
                # communicate() drains the pipes while it waits, so the child never
                # blocks on a full buffer; retrying after a timeout keeps the output
                # already read. On a clean exit this returns and we are done.
                stdout, stderr = proc.communicate(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                pass
            if memory_cap_gib:
                # The agent phase gets the ceiling the oracle already has (#82/#379).
                # The runaway grows in a descendant of the client (model-written code
                # the agent executed), so sample the whole tree, not just the client.
                peak_rss_gib = max(peak_rss_gib, rss_sampler(proc.pid))
                if peak_rss_gib > memory_cap_gib:
                    memory_killed = True
                    log.error(
                        "client memory cap: process tree reached %.1f GiB, cap is "
                        "%.1f GiB -- killing the group before it OOMs the box (#379)",
                        peak_rss_gib,
                        memory_cap_gib,
                    )
                    break
            # After the memory check: an engine read can take up to its timeout
            # (#1453), and a runaway must not wait on it.
            watchdog.sample()
            if watchdog.stalled():
                idle_stalled = True
                log.error(
                    "client GPU-idle-stall: %s, for >= %.0fs -- killing "
                    "the process group (was riding a %s timeout doing nothing)",
                    watchdog.rule_summary(),
                    watchdog.idle_stall_secs,
                    "None" if timeout is None else f"{timeout:.0f}s",
                )
                break
            if timeout is not None and (time.monotonic() - start) >= timeout:
                timed_out = True
                log.error(
                    "client wall-clock timeout after %.0fs -- killing the group",
                    timeout,
                )
                break
    except BaseException:
        _terminate_group(proc, grace_secs, log)
        raise

    if idle_stalled or timed_out or memory_killed:
        _terminate_group(proc, grace_secs, log)
        # Collect whatever was produced before and during the kill.
        try:
            rest_out, rest_err = proc.communicate(timeout=grace_secs)
        except subprocess.TimeoutExpired:
            rest_out, rest_err = "", ""
        stdout = (stdout or "") + (rest_out or "")
        stderr = (stderr or "") + (rest_err or "")

    return ClientResult(
        stdout=stdout or "",
        stderr=stderr or "",
        returncode=proc.returncode,
        idle_stalled=idle_stalled,
        timed_out=timed_out,
        memory_killed=memory_killed,
        peak_rss_gib=peak_rss_gib,
    )


def cell_should_abort(
    trials_done: int,
    total: int,
    timeouts: int,
    *,
    min_fraction: float = 0.25,
    timeout_fraction: float = 0.50,
) -> tuple[bool, str]:
    """Should a cell stop scheduling more trials? A verdict with its reason.

    Abort only when all three hold:

      * ``trials_done >= ceil(min_fraction * total)`` -- enough of the planned
        cell has run to judge it, not a first unlucky trial;
      * ``trials_done > 0``;
      * ``timeouts / trials_done > timeout_fraction`` -- strictly more than the
        fraction, so a cell sitting exactly at the line is given the benefit of
        the doubt.

    ``total <= 0`` (planned size unknown) and ``trials_done == 0`` never abort.
    """
    if total <= 0:
        return False, "not enough data: planned cell size is unknown (total <= 0)"
    if trials_done <= 0:
        return False, "not enough data: no trials done yet"
    threshold = math.ceil(min_fraction * total)
    if trials_done < threshold:
        return (
            False,
            (
                f"not enough data: {trials_done}/{total} done, need >= "
                f"{threshold} ({min_fraction:.0%} of {total}) before judging"
            ),
        )
    rate = timeouts / trials_done
    if rate > timeout_fraction:
        return (
            True,
            (
                f"abort: {timeouts}/{trials_done} trials timed out "
                f"({rate:.0%} > {timeout_fraction:.0%} of a judged cell)"
            ),
        )
    return (
        False,
        (
            f"continue: {timeouts}/{trials_done} timed out "
            f"({rate:.0%} <= {timeout_fraction:.0%})"
        ),
    )


def gpu_watts() -> float | None:
    """One best-effort GPU power reading in watts, or None if unreadable.

    Reuses gpu_utilization's samplers (dcgmi first, then nvidia-smi). This is
    the default watts sampler for the watchdog and the source for the
    ``sample-watts`` CLI. It never raises: an unreadable GPU is None, which the
    watchdog holds rather than counting as idle.
    """
    got = gpu_utilization._dcgmi() or gpu_utilization._query()
    watts = got.get("watts") if got else None
    return watts if isinstance(watts, (int, float)) else None


# --- standalone CLI ---------------------------------------------------------


def _evaluate(backend: str, results_path: pathlib.Path, total: int | None) -> int:
    """Print the circuit-breaker verdict for a backend's rows on disk."""
    import results

    rows = [
        r
        for r in results.load(results_path)
        if r.get("backend") == backend and not results.is_excluded(r)
    ]
    trials_done = len(rows)
    timeouts = sum(1 for r in rows if r.get("error") == "timeout")
    # By default the whole cell IS what has run: judge the timeouts against the
    # rows on disk. --total compares them against a planned cell size instead,
    # so a half-finished cell can be judged before it fills.
    planned = total if total is not None else trials_done
    abort, reason = cell_should_abort(trials_done, planned, timeouts)
    logger.info(
        "%s: %d trials, %d timeouts, planned total %d -- %s",
        backend,
        trials_done,
        timeouts,
        planned,
        reason,
    )
    return 1 if abort else 0


def _sample_watts(tier: str | None = None) -> int:
    floor, source = resolve_idle_floor(None, {"tier": tier} if tier else {})
    watts = gpu_watts()
    if watts is None:
        logger.info("GPU power unavailable")
    else:
        logger.info(
            "GPU power: %.1f W (idle floor %g W, from %s)", watts, floor, source
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    import provenance

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser(
        "evaluate",
        help="print the per-cell timeout circuit-breaker verdict for a backend",
    )
    ev.add_argument("--backend", required=True)
    ev.add_argument("--results", required=True, type=pathlib.Path)
    ev.add_argument(
        "--total",
        type=int,
        default=None,
        help="planned cell size to judge against; default: the rows on disk",
    )

    sw = sub.add_parser("sample-watts", help="print one GPU watts reading, best effort")
    sw.add_argument(
        "--tier",
        default=None,
        help="show the idle floor for this tasks.toml tier (13 W on GB10, "
        f"#1450); default: the global {IDLE_FLOOR_WATTS:g} W",
    )

    args = parser.parse_args(argv)
    provenance.configure()

    if args.cmd == "evaluate":
        return _evaluate(args.backend, args.results, args.total)
    return _sample_watts(args.tier)


if __name__ == "__main__":
    raise SystemExit(main())
