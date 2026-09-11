#!/usr/bin/env python3
"""Is the machine busy, and who says so? One answer, for every agent.

    uv run python scripts/machine_state.py            # a line per claim
    uv run python scripts/machine_state.py --json     # the same, as JSON
    uv run python scripts/machine_state.py --pid 50125

Several files in this repo write down a pid and later get read as if the pid
were still meaningful. **Nothing re-checked them**, and on 2026-09-09 that
cost an hour: `.claude/peer/status.json` said the machine was busy --

    "lock": "held",
    "servers": [{"short": "ds4-server", "pid": 50125, "gib": 74.2, "age": "20m"}]

-- and every word of it was false. The lock file did not exist, pid 50125 had
not existed for hours, and the file's own mtime was 10 hours old while the
row inside it claimed the server was 20 minutes into its life. A peer read
that file, believed it, and stood down from a free machine.

## A pid is not an identity

`os.kill(pid, 0)` answers "does SOME process have this pid", which is not the
question. The kernel reuses pids, so a stale record can name a live process
that has nothing to do with what was recorded, and acting on it -- stopping
it, or standing down for it -- is worse than having no record at all.

So every pid here is checked twice: alive, and then **still the same
process**. Three ways to confirm the second, in descending strength:

- `start_key` -- the process's own start time, recorded at spawn. A reused
  pid will not have the same one. `unitctl` records this and owns the check.
- **the record's mtime.** A process that started AFTER its record was written
  cannot be the process that record describes. This needs nothing recorded in
  advance, which is why it is what rescues `.claude/peer/status.json`.
- **the command.** A pid whose live executable no longer resembles what was
  claimed is not it.

A pid that is alive and confirms none of those is `UNCONFIRMED`, not
`RUNNING`. That distinction is the whole point of the script.

## The statuses

| status | meaning |
|---|---|
| `MISSING` | no record, or a record naming no usable pid |
| `RUNNING` | alive, and confirmed to be the process the record describes |
| `UNCONFIRMED` | alive, but nothing available proves it is the same process |
| `REUSED` | alive, and demonstrably a DIFFERENT process |
| `STALE` | the record names a pid that is not alive |
| `UNRECORDED` | a live model server that no record mentions at all |

`UNRECORDED` is the inverse failure and the more dangerous one: a server
somebody started by hand holds the GPU while every record says free.

## The verdict, and why it fails closed

`BUSY` if anything is running or unrecorded. `FREE` only when every claim is
MISSING or STALE **and** no model server is resident. Anything unproven --
`UNCONFIRMED`, `REUSED`, a corrupt or foreign lock -- is `UNCERTAIN`, which is
not `FREE`. Reporting a machine free when it might not be invites a second run
onto a busy one, and that voids both.

Exit codes, so a waiting loop can branch without parsing: 0 free, 3 busy,
4 uncertain, 1 this tool failed.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import os
import pathlib
import platform
import subprocess
import sys
from collections.abc import Sequence

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import preflight
import unitctl

import logs

logger = logging.getLogger(__name__)

MISSING = "MISSING"
RUNNING = "RUNNING"
UNCONFIRMED = "UNCONFIRMED"
REUSED = "REUSED"
STALE = "STALE"
UNRECORDED = "UNRECORDED"
#: A run lock held by an agent SESSION, not a process (#275). There is no pid
#: to check for liveness; it holds the machine until released or cleared. Kept
#: distinct from RUNNING so a reader never sees `RUNNING` beside `pid: null`.
CLAIMED = "CLAIMED"

BUSY = "BUSY"
FREE = "FREE"
UNCERTAIN = "UNCERTAIN"

EXIT = {FREE: 0, BUSY: 3, UNCERTAIN: 4}

#: `--pid` answers a narrower question -- "is this pid the process you think"
#: -- and gets the same three answers, so a caller can branch on one set of
#: codes either way. A pid we cannot confirm shares a code with an uncertain
#: machine, deliberately: both mean do not act on this.
PID_EXIT = {
    RUNNING: EXIT[BUSY],
    STALE: EXIT[FREE],
    MISSING: EXIT[FREE],
    UNCONFIRMED: EXIT[UNCERTAIN],
    REUSED: EXIT[UNCERTAIN],
}

#: Statuses that mean the process behind the record is alive.
LIVE = (RUNNING, UNRECORDED)

#: Statuses of a run lock that mean it holds the machine. RUNNING is a live
#: process; CLAIMED is an agent session with no process (#275). Either is BUSY.
HELD_LOCK = (RUNNING, CLAIMED)

#: Resident memory, in GiB, above which a server is holding a MODEL rather
#: than merely existing. An `ollama serve` that nobody has asked for anything
#: sits at ~0.0 GiB and is not an occupant of the GPU; the smallest stack this
#: repo measures is 31 GB of weights, so anything with a model in it is far
#: above this line and anything idle is far below it. Without this the survey
#: called the machine BUSY because two idle ollamas were running, which is the
#: opposite of the mistake it was written to stop.
RESIDENT_GIB = 8.0

#: Statuses that mean we do not know, which is not the same as free.
UNPROVEN = (UNCONFIRMED, REUSED)

#: The claim source for a bare GPU benchmark (#277). Named so `occupies`,
#: `verdict` and `describe` can recognise it without string-matching `what`.
BENCH_SOURCE = "gpu-bench"

#: GPU-consuming benchmark binaries that take no lock of their own: the lock is
#: held by the driver that spawns them, so a driver that is killed, crashes, or
#: is run by hand leaves one of these on the GPU with nothing claiming it (#277).
#: On 2026-09-09 `machine_state` said `Currently on GPU: idle` while a `ds4-bench`
#: sweep owned it. Matched on the executable name like `parse_ps` -- never the
#: argv, which is the `pgrep -f` self-match this repo has paid for twice.
#:
#: Name-based, so it goes stale as the matrix grows -- deliberately, because the
#: failure it adds errs toward BUSY: a benchmark wrongly omitted here reopens the
#: silent-collision hole, while a name that never runs costs nothing. `speed-bench`
#: is NOT here -- it is a directory in the prompt path, not a binary.
GPU_BENCH = ("ds4-bench", "llama-bench")

# Out of the repo, beside the run lock (#238). Owned by preflight so the
# reader here and the writer in peer_status.py cannot name different files.
PEER_STATUS = preflight.PEER_STATUS_PATH


@dataclasses.dataclass(frozen=True)
class Claim:
    """One pid somebody wrote down, and what became of it."""

    source: str
    what: str
    pid: int | None
    status: str
    detail: str
    confirmed_by: str | None = None
    record_age_s: int | None = None
    #: What the process holds RIGHT NOW, from the live census -- never the
    #: number the record wrote down. The row that started this said 74.2 GiB
    #: about a pid that had not existed for hours.
    resident_gib: float | None = None
    #: How long this claim has HELD the machine -- which is not the same as how
    #: long its process has run, and the two sources differ by claim:
    #:
    #: - a resident server holds from the moment it started, so the OS answers
    #:   it: the process being alive IS the occupation.
    #: - the lock holds from the moment the record was written. Its holder can
    #:   be an agent session hours old that took the lock a minute ago, so the
    #:   process start time over-reports, and structurally: `check_pid` calls a
    #:   lock RUNNING only when the process began at or before the record, so a
    #:   lifetime is always >= the record's own age.
    #:
    #: #265 asks for it so a reader can tell a sweep that just began from one
    #: that has hung for hours. A lifetime cannot answer that for a lock.
    held_s: int | None = None

    @property
    def occupies(self) -> bool:
        """Whether this is something ON the machine, not merely running."""
        if self.status not in LIVE:
            return False
        # A benchmark binary has no idle state: it exists only while it is
        # working the GPU (#277), so its mere presence occupies, whatever it has
        # loaded this instant. A server, by contrast, can sit resident-but-idle
        # (an `ollama serve` nobody has asked anything sits at ~0 GiB), so it
        # must clear the resident-model bar to count.
        if self.source == BENCH_SOURCE:
            return True
        return (self.resident_gib or 0.0) >= RESIDENT_GIB

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    def line(self) -> str:
        pid = f"pid {self.pid}" if self.pid is not None else "no pid"
        age = (
            f" [record {preflight.human_age(self.record_age_s)} old]"
            if (self.record_age_s is not None)
            else ""
        )
        gib = f" {self.resident_gib:.1f} GiB now" if self.resident_gib else ""
        held = (
            f" held {preflight.human_age(self.held_s)}"
            if self.held_s is not None
            else ""
        )
        return (
            f"{self.status:<12} {self.source}: {self.what} ({pid}{gib}){held}{age} "
            f"-- {self.detail}"
        )


def started_at(pid: int) -> dt.datetime | None:
    """When THIS incarnation of `pid` started, or None.

    `ps -o lstart=` prints a LOCAL time with no offset. It is given this
    machine's offset immediately, so nothing downstream compares a naive
    stamp against an aware one: `scripts/backfill_iso8601.py` exists because
    that comparison was made once in `results-*.jsonl`, and it is a
    four-hour error with no error message.
    """
    got = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    if got.returncode != 0 or not got.stdout.strip():
        return None
    try:
        naive = dt.datetime.strptime(got.stdout.strip(), "%a %b %d %H:%M:%S %Y")  # noqa: DTZ007
    except ValueError:
        return None
    return naive.astimezone()


def binary_of(pid: int) -> str | None:
    """The live process's EXECUTABLE, basename only, or None when it is gone.

    Not the command line. Matching a name anywhere in the arguments is the
    `pgrep -f` self-match this repo has paid for twice: a shell running a
    script that merely mentions `ds4-server` has the string in its own argv,
    so the check passes on the wrong process. Found here the same way -- the
    first version of this said `pid 47424 is running 'ds4-server'` about the
    shell that had just typed the words `--expect ds4-server`.

    `preflight.Proc.short` takes the same slice for the same reason.
    """
    got = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    if got.returncode != 0 or not got.stdout.split():
        return None
    return got.stdout.split()[0].rsplit("/", 1)[-1]


def check_pid(
    pid: int | None,
    *,
    start_key: str | None = None,
    recorded_at: dt.datetime | None = None,
    expect: str | None = None,
) -> tuple[str, str, str | None]:
    """(status, why, what confirmed it) for one recorded pid.

    Each check is skipped when the evidence for it was not recorded, and a pid
    that survives every check it CAN be given is only `UNCONFIRMED`. Silence
    is not confirmation.
    """
    if pid is None:
        return MISSING, "nothing recorded a pid", None
    if not unitctl.alive(pid):
        return STALE, f"pid {pid} is not running", None

    if start_key is not None:
        live = unitctl.start_key(pid)
        if live != start_key:
            return (
                REUSED,
                (
                    f"pid {pid} is alive but started at {live!r}, not "
                    f"{start_key!r} -- the kernel has handed this pid to "
                    "something else"
                ),
                None,
            )
        return RUNNING, f"pid {pid} is running and its start time matches", "start_key"

    if recorded_at is not None:
        began = started_at(pid)
        if began is not None and began > recorded_at:
            return (
                REUSED,
                (
                    f"pid {pid} started at {began:%Y-%m-%dT%H:%M:%S%z}, AFTER "
                    f"the record was written at "
                    f"{recorded_at:%Y-%m-%dT%H:%M:%S%z} -- it cannot be the "
                    "process the record describes"
                ),
                None,
            )
        if began is not None:
            return (
                RUNNING,
                f"pid {pid} is running and predates its own record",
                "record_mtime",
            )

    if expect:
        live = binary_of(pid)
        # Case-insensitive: on macOS the framework interpreter's argv[0] is
        # `Python`, so an arm recorded as `python` read as REUSED -- a
        # confident wrong answer about a process that was exactly what the
        # record said it was.
        want = expect.rsplit("/", 1)[-1].lower()
        if live is not None and want not in live.lower():
            return REUSED, f"pid {pid} is running {live!r}, not {want!r}", None
        if live is not None:
            return RUNNING, f"pid {pid} is running {live!r}", "command"

    return (
        UNCONFIRMED,
        (
            f"pid {pid} is alive, but nothing recorded can prove it is the "
            "same process -- treat the machine as uncertain, not free"
        ),
        None,
    )


def record_age_s(path: pathlib.Path) -> int | None:
    try:
        return int(dt.datetime.now(dt.UTC).timestamp() - path.stat().st_mtime)
    except OSError:
        return None


def recorded_at(path: pathlib.Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).astimezone()
    except OSError:
        return None


def lock_claim(path: pathlib.Path | None = None) -> Claim:
    """The machine lock, re-checked rather than believed.

    `preflight.lock_state` already classifies the lock and is left owning
    that; this adds the identity check it does not do, because `_pid_alive`
    alone cannot tell a live holder from a reused pid.
    """
    path = path or preflight.LOCK_PATH
    lock = preflight.read_lock(path)
    state, why = preflight.lock_state(lock, platform.node(), os.getpid())
    age = record_age_s(path)
    if lock is None:
        return Claim("run-lock", "the machine lock", None, MISSING, why)
    if state in ("corrupt", "foreign"):
        return Claim("run-lock", "the machine lock", None, UNCONFIRMED, why, None, age)
    what = str(lock.get("what") or "unspecified work")
    if lock.get("session_claim"):
        # #275: held by an agent session, not a process. No pid to confirm --
        # CLAIMED, held since the record was written, so the machine reads
        # BUSY instead of collapsing to STALE when the acquiring CLI exits.
        agent = str(lock.get("agent") or "an unidentified agent")
        return Claim("run-lock", what, None, CLAIMED, why, agent, age, held_s=age)
    holder = lock.get("pid")
    if not isinstance(holder, int):
        return Claim("run-lock", what, None, MISSING, why, None, age)
    status, detail, by = check_pid(holder, recorded_at=recorded_at(path))
    if state == "ours":
        detail = f"{detail} (this process holds it)"
    # The lock has been held since the record was written -- `age` already is
    # that, measured from the file's mtime. Not `started_at(holder)`: a session
    # can be hours old and have taken the lock a minute ago.
    return Claim(
        "run-lock",
        what,
        holder,
        status,
        detail,
        by,
        age,
        held_s=age if status in LIVE else None,
    )


def peer_status_claims(path: pathlib.Path | None = None) -> list[Claim]:
    """Every server row in the peer's status file, re-checked.

    This is the file that lied. A row written since `peer_status` learned to
    record `start_key` is checked against that -- the strong check. An older
    row carries only a pid, and the file's own mtime is what does the work: a
    server that started after the file was written is not the server the file
    is describing. Both are here because the old rows do not disappear when
    the writer improves.
    """
    path = path or PEER_STATUS
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return [
            Claim("peer-status", "the peer's status file", None, MISSING, f"no {path}")
        ]
    except (OSError, ValueError) as exc:
        return [
            Claim(
                "peer-status",
                "the peer's status file",
                None,
                UNCONFIRMED,
                f"{path} is unreadable ({exc}); it is not evidence of anything",
            )
        ]
    when, age = recorded_at(path), record_age_s(path)
    rows = raw.get("servers") or []
    claims = []
    for row in rows:
        pid = row.get("pid") if isinstance(row, dict) else None
        short = str(row.get("short", "?")) if isinstance(row, dict) else "?"
        gib = row.get("gib") if isinstance(row, dict) else None
        what = f"{short} ({gib} GiB when recorded)" if gib else short
        key = row.get("start_key") if isinstance(row, dict) else None
        status, detail, by = check_pid(
            pid if isinstance(pid, int) else None,
            start_key=key if isinstance(key, str) else None,
            recorded_at=when,
            expect=short,
        )
        claims.append(Claim("peer-status", what, pid, status, detail, by, age))
    if not claims:
        claims.append(
            Claim(
                "peer-status",
                "no server rows",
                None,
                MISSING,
                "the file lists none",
                None,
                age,
            )
        )
    return claims


def unit_claims(state_dir: pathlib.Path | None = None) -> list[Claim]:
    """Every unitctl record. These carry a start key, so they are checkable."""
    directory = state_dir or unitctl.STATE_DIR
    claims = []
    for name in sorted(unitctl.units(state_dir)):
        unit = unitctl.read(name, state_dir)
        if unit is None:
            claims.append(Claim("unit", name, None, MISSING, "record unreadable"))
            continue
        status, detail, by = check_pid(
            unit.pid,
            start_key=unit.start_key,
            recorded_at=recorded_at(unitctl.record_path(name, state_dir)),
            expect=unit.command[0] if unit.command else None,
        )
        claims.append(
            Claim(
                "unit",
                name,
                unit.pid,
                status,
                detail,
                by,
                record_age_s(unitctl.record_path(name, state_dir)),
            )
        )
    if not claims:
        claims.append(
            Claim("unit", "no units", None, MISSING, f"nothing in {directory}")
        )
    return claims


def unrecorded(claims: Sequence[Claim], procs: Sequence[preflight.Proc]) -> list[Claim]:
    """Model servers that are resident and that nothing wrote down.

    The inverse failure, and the worse one: every record says free while a
    server holds 74 GiB. `preflight.parse_ps` owns what counts as a server --
    a second list of process names here would drift from it.
    """
    known = {c.pid for c in claims if c.pid is not None and c.status in LIVE}
    return [
        Claim(
            "ps",
            # The age used to be baked into the display name here, and only
            # here -- no other claim names itself after its own age. Now that
            # `held_s` carries it as a field, keeping both prints the same
            # fact twice ("ds4-server (up 10m) ... for 10m") and gives two
            # renderings to drift apart. The field wins; the name is a name.
            p.short,
            p.pid,
            UNRECORDED,
            "a resident model server that no record mentions",
            resident_gib=round(p.rss_gib, 1),
            held_s=p.age_s,
        )
        for p in procs
        if p.pid not in known and p.rss_gib >= RESIDENT_GIB
    ]


def servers() -> list[preflight.Proc]:
    """The live census, via preflight so there is one definition of a server."""
    return preflight.parse_ps(
        preflight._capture(["ps", "-eo", "pid,rss,etime,command"])
    )


def bench_claims(ps_text: str | None = None) -> list[Claim]:
    """Bare GPU benchmark binaries, which hold the GPU under no lock (#277).

    `ds4-bench` takes no lock of its own -- the driver that spawns it does -- so
    a driver that dies without reaping leaves a GPU consumer that the lock
    claim, the peer file and the unit records all miss. This is the census that
    catches it: `GPU_BENCH` matched on the executable, via `parse_ps` so the
    match is the same careful one the servers get. Each is reported `UNRECORDED`
    -- alive, and nothing wrote it down -- the same status a stray server gets.
    """
    text = (
        ps_text
        if ps_text is not None
        else preflight._capture(["ps", "-eo", "pid,rss,etime,command"])
    )
    return [
        Claim(
            BENCH_SOURCE,
            p.short,
            p.pid,
            UNRECORDED,
            "a GPU benchmark running under no lock -- a driver was killed or "
            "crashed without reaping it, or it was started by hand (#277)",
            resident_gib=round(p.rss_gib, 1),
            held_s=p.age_s,
        )
        for p in preflight.parse_ps(text, markers=GPU_BENCH)
    ]


def occupant(claims: Sequence[Claim]) -> Claim | None:
    """What is on the machine, or None. `None` means idle, and idle counts.

    This is the answer to "Currently on GPU: what?", which every status update
    in this project has to open with.

    **The lock is the fallback, and it is not a technicality.** A resident
    server is the obvious occupant, but a `ds4-bench` sweep has none: each rep
    spawns a fresh process that loads the model, measures, and exits, and
    `preflight.INFERENCE` lists servers only. During the whole of the #267 A/B
    on 2026-09-09 this line read `idle` while three runs owned the machine.
    The verdict was right throughout because the lock carried it; the sentence
    a person reads was wrong. During a sweep the occupant is the WORK, which
    is what the lock records.
    """
    on = [c for c in claims if c.occupies]
    if on:
        return max(on, key=lambda c: c.resident_gib or 0.0)
    held = [c for c in claims if c.source == "run-lock" and c.status in HELD_LOCK]
    return held[0] if held else None


def describe(on: Claim | None) -> str:
    """The "Currently on GPU:" phrase. One owner, because two would drift.

    `peer_brief` and this script's own CLI both print it, and before this they
    each built it from the survey by hand.
    """
    if on is None:
        return "idle"
    # A session claim (#275) has no pid; naming its agent is the honest
    # substitute for "pid N", and "pid None" reads like a bug in the line
    # every status update has to open with.
    if on.pid is None:
        where = (
            f"{on.what} (session claim by {on.confirmed_by or 'an unidentified agent'})"
        )
    else:
        where = f"{on.what} pid {on.pid}"
    # How long it has held, from the OS. #265 asked for it because "ab=no"
    # during a live A/B sent a reader hunting for a detector, and a monitor
    # that says WHAT and not FOR HOW LONG cannot tell a sweep that started a
    # minute ago from one that has hung for three hours -- which is the whole
    # question a person asks on seeing the machine busy.
    for_ = f" for {preflight.human_age(on.held_s)}" if on.held_s is not None else ""
    if on.resident_gib:
        return f"{where}, {on.resident_gib} GiB{for_}"
    return f"{where}{for_} (holding the lock; no model resident this instant)"


def verdict(claims: Sequence[Claim]) -> tuple[str, str]:
    """(BUSY|FREE|UNCERTAIN, why). Never FREE on the strength of not knowing."""
    busy = [c for c in claims if c.occupies]
    held = [c for c in claims if c.source == "run-lock" and c.status in HELD_LOCK]
    unproven = [c for c in claims if c.status in UNPROVEN]
    if busy or held:
        return BUSY, "; ".join(f"{c.what} ({c.status.lower()})" for c in busy + held)
    if unproven:
        return UNCERTAIN, "; ".join(f"{c.source}: {c.detail}" for c in unproven)
    return FREE, "no lock is held, and no model server is resident"


def survey(
    *,
    lock_path: pathlib.Path | None = None,
    peer_path: pathlib.Path | None = None,
    unit_dir: pathlib.Path | None = None,
    procs: Sequence[preflight.Proc] | None = None,
    bench_text: str | None = None,
) -> dict[str, object]:
    """The whole answer, as the JSON both agents read."""
    live = list(procs) if procs is not None else servers()
    resident = {p.pid: round(p.rss_gib, 1) for p in live}
    # A server occupies the machine for as long as it is up, so `ps` has
    # already answered "how long has it held" -- it is the same number that
    # renders as "(up 10m)". No second lookup, and nothing to disagree with.
    held = {p.pid: p.age_s for p in live}
    claims = [lock_claim(lock_path)]
    claims += peer_status_claims(peer_path)
    claims += unit_claims(unit_dir)
    # The live number, attached to every record that named a live pid. A
    # record's own figure is what it was when somebody wrote it down, which
    # is exactly the thing this script exists not to believe.
    claims = [
        dataclasses.replace(
            c,
            resident_gib=resident.get(c.pid),
            # And its hold time, from the same census and for the same
            # reason. Without this the field was set on the lock claim alone,
            # while `occupant()` prefers a resident server whenever one exists
            # -- so the duration was missing from exactly the case #265 opens
            # with, and present only on the fallback path.
            held_s=held.get(c.pid),
        )
        if c.pid in resident
        else c
        for c in claims
    ]
    claims += unrecorded(claims, live)
    # A bare benchmark holds the GPU under no lock, so it is found from the
    # process table, not from any record (#277). Injected in tests; captured
    # live otherwise. `procs` overriding the server census must also govern the
    # bench census, or a test that hands in its own processes still shells out.
    if bench_text is None and procs is not None:
        bench_text = ""
    claims += bench_claims(bench_text)
    state, why = verdict(claims)
    on = occupant(claims)
    return {
        "checked_at": dt.datetime.now(dt.UTC).astimezone().strftime(logs.DATEFMT),
        "hostname": platform.node(),
        "verdict": state,
        "why": why,
        "occupant": on.as_dict() if on else None,
        "occupant_line": describe(on),
        "claims": [c.as_dict() for c in claims],
    }


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true", help="the survey, machine-readable")
    p.add_argument(
        "--pid",
        type=int,
        help="check one pid instead of surveying; needs --expect to confirm it",
    )
    p.add_argument("--expect", help="the command --pid should be running")
    p.add_argument("--peer-status", type=pathlib.Path, default=None)
    p.add_argument("--lock", type=pathlib.Path, default=None)
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN if args.json else logs.FORMAT)

    if args.pid is not None:
        status, detail, by = check_pid(args.pid, expect=args.expect)
        got = {"pid": args.pid, "status": status, "detail": detail, "confirmed_by": by}
        logger.info(
            "%s", json.dumps(got, indent=2) if args.json else f"{status}: {detail}"
        )
        return PID_EXIT[status]

    got = survey(lock_path=args.lock, peer_path=args.peer_status)
    if args.json:
        logger.info("%s", json.dumps(got, indent=2))
    else:
        for claim in got["claims"]:
            logger.info("%s", Claim(**claim).line())
        logger.info("Currently on GPU: %s", got["occupant_line"])
        logger.info("VERDICT %s -- %s", got["verdict"], got["why"])
    return EXIT[str(got["verdict"])]


if __name__ == "__main__":
    raise SystemExit(main())
