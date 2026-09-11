"""One shared answer to "is the machine busy", and who claimed so.

`scripts/machine_state.py` exists because `.claude/peer/status.json` said

    "lock": "held",
    "servers": [{"short": "ds4-server", "pid": 50125, "gib": 74.2, "age": "20m"}]

while the lock file did not exist, pid 50125 had been gone for hours, and the
file itself was 10 hours old. A peer read it, believed it, and stood down from
a free machine. Every test here is a way that record could lie.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import machine_state as ms
import preflight


@pytest.fixture
def dead_pid() -> int:
    """A pid that was real and is not any more, without inventing a number."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def proc(pid: int, gib: float, command: str = "/usr/local/bin/ds4-server --port 8000"):
    return preflight.Proc(pid=pid, rss_gib=gib, command=command, age_s=600)


# --- one pid, checked twice --------------------------------------------------


def test_no_pid_recorded_is_missing() -> None:
    status, why, by = ms.check_pid(None)
    assert (status, by) == (ms.MISSING, None)
    assert "nothing recorded" in why


def test_a_pid_that_is_gone_is_stale(dead_pid) -> None:
    status, why, _ = ms.check_pid(dead_pid)
    assert status == ms.STALE
    assert str(dead_pid) in why


def test_a_live_pid_with_nothing_to_check_it_against_is_unconfirmed() -> None:
    """The distinction the whole script is for.

    `os.kill(pid, 0)` answers "does SOME process have this pid". Calling that
    RUNNING is how a stale record gets believed.
    """
    status, why, by = ms.check_pid(os.getpid())
    assert status == ms.UNCONFIRMED
    assert by is None
    assert "not free" in why


def test_a_matching_start_key_confirms_the_process() -> None:
    import unitctl

    key = unitctl.start_key(os.getpid())
    status, _, by = ms.check_pid(os.getpid(), start_key=key)
    assert (status, by) == (ms.RUNNING, "start_key")


def test_a_different_start_key_means_the_pid_was_reused() -> None:
    status, why, _ = ms.check_pid(os.getpid(), start_key="Mon Jan  1 00:00:00 2001")
    assert status == ms.REUSED
    assert "handed this pid to something else" in why


def test_a_process_that_started_after_its_own_record_cannot_be_it() -> None:
    """The check that rescues a record carrying no start key.

    This is what `.claude/peer/status.json` needs: its rows record a pid and
    nothing else identifying, so the file's own mtime is the only evidence
    available -- and it is enough to refuse a pid the kernel has recycled.
    """
    long_ago = dt.datetime(2001, 1, 1, tzinfo=dt.UTC)
    status, why, _ = ms.check_pid(os.getpid(), recorded_at=long_ago)
    assert status == ms.REUSED
    assert "AFTER the record was written" in why


def test_a_process_that_predates_its_record_is_running() -> None:
    later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    status, _, by = ms.check_pid(os.getpid(), recorded_at=later)
    assert (status, by) == (ms.RUNNING, "record_mtime")


def test_a_command_that_no_longer_matches_means_the_pid_was_reused() -> None:
    status, why, _ = ms.check_pid(os.getpid(), expect="ds4-server")
    assert status == ms.REUSED
    assert "not 'ds4-server'" in why


def test_a_matching_command_confirms_the_process() -> None:
    status, _, by = ms.check_pid(os.getpid(), expect="python")
    assert (status, by) == (ms.RUNNING, "command")


def test_the_check_reads_the_executable_not_the_arguments() -> None:
    """The `pgrep -f` self-match, which this repo has paid for twice.

    A shell running a script that merely MENTIONS ds4-server carries the
    string in its own argv. The first version of this matched anywhere in the
    command line and reported `pid 47424 is running 'ds4-server'` about the
    shell that had just typed `--expect ds4-server`.
    """
    got = subprocess.run(
        ["sh", "-c", "echo ds4-server >/dev/null; ps -o command= -p $$"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ds4-server" in got.stdout, "the trap needs the string in the argv"
    assert ms.binary_of(os.getpid()) is not None
    assert "ds4-server" not in (ms.binary_of(os.getpid()) or "")


def test_the_binary_match_ignores_case() -> None:
    """macOS's framework interpreter reports argv[0] as `Python`.

    A record written as `python` then read as REUSED -- a confident wrong
    answer about a process that was exactly what the record said it was.
    """
    live = ms.binary_of(os.getpid())
    assert live is not None
    for spelling in (live.lower(), live.upper()):
        status, _, _ = ms.check_pid(os.getpid(), expect=spelling)
        assert status == ms.RUNNING, spelling


def test_a_binary_given_as_a_path_matches_by_name(tmp_path) -> None:
    """#225 passes a PATH to the second mlx binary, not just a name."""
    status, _, by = ms.check_pid(os.getpid(), expect="/opt/weird/bin/python")
    assert (status, by) == (ms.RUNNING, "command")


def test_a_dead_pid_is_stale_whatever_else_was_recorded(dead_pid) -> None:
    """Liveness first: no amount of recorded identity revives a gone pid."""
    status, _, _ = ms.check_pid(dead_pid, start_key="whatever", expect="ds4-server")
    assert status == ms.STALE


# --- the file that lied ------------------------------------------------------


@pytest.fixture
def peer_file(tmp_path) -> pathlib.Path:
    return tmp_path / "status.json"


def write_peer(path: pathlib.Path, servers: list[dict]) -> pathlib.Path:
    path.write_text(json.dumps({"lock": "held", "servers": servers}))
    return path


def test_the_real_row_that_misled_a_peer_reads_stale(peer_file, dead_pid) -> None:
    write_peer(
        peer_file,
        [{"short": "ds4-server", "pid": dead_pid, "gib": 74.2, "age": "20m"}],
    )
    (claim,) = ms.peer_status_claims(peer_file)
    assert claim.status == ms.STALE
    assert claim.pid == dead_pid
    assert "74.2" in claim.what, "say what it CLAIMED, so the lie is legible"


def test_the_records_own_age_is_reported(peer_file, dead_pid) -> None:
    """The row said "age 20m" while the file was 10 hours old. Both numbers
    belong in the answer, and only one of them is checkable."""
    write_peer(peer_file, [{"short": "x", "pid": dead_pid}])
    old = dt.datetime.now(dt.UTC) - dt.timedelta(hours=10)
    os.utime(peer_file, (old.timestamp(), old.timestamp()))
    (claim,) = ms.peer_status_claims(peer_file)
    assert claim.record_age_s is not None
    assert 9 * 3600 < claim.record_age_s < 11 * 3600


def test_a_row_carrying_a_start_key_gets_the_strong_check(peer_file) -> None:
    """`peer_status` records one now, so a fresh row is checkable outright
    rather than only against the file's mtime."""
    import unitctl

    key = unitctl.start_key(os.getpid())
    path = peer_file
    path.write_text(
        json.dumps(
            {"servers": [{"short": "python", "pid": os.getpid(), "start_key": key}]}
        )
    )
    (claim,) = ms.peer_status_claims(path)
    assert (claim.status, claim.confirmed_by) == (ms.RUNNING, "start_key")


def test_a_row_whose_start_key_no_longer_matches_is_reused(peer_file) -> None:
    stale_key = "Mon Jan  1 00:00:00 2001"
    write_peer(
        peer_file,
        [{"short": "python", "pid": os.getpid(), "start_key": stale_key}],
    )
    (claim,) = ms.peer_status_claims(peer_file)
    assert claim.status == ms.REUSED


def test_an_old_row_without_a_start_key_still_gets_the_mtime_check(
    peer_file, dead_pid
) -> None:
    """Improving the writer does not retire the rows already on disk."""
    write_peer(peer_file, [{"short": "ds4-server", "pid": dead_pid, "gib": 74.2}])
    (claim,) = ms.peer_status_claims(peer_file)
    assert claim.status == ms.STALE


def test_a_missing_peer_file_is_missing_not_an_error(tmp_path) -> None:
    (claim,) = ms.peer_status_claims(tmp_path / "absent.json")
    assert claim.status == ms.MISSING


def test_an_unreadable_peer_file_is_not_evidence_of_anything(peer_file) -> None:
    """A truncated write is not proof the machine is free."""
    peer_file.write_text('{"servers": [')
    (claim,) = ms.peer_status_claims(peer_file)
    assert claim.status == ms.UNCONFIRMED


def test_a_peer_file_with_no_servers_is_missing(peer_file) -> None:
    write_peer(peer_file, [])
    (claim,) = ms.peer_status_claims(peer_file)
    assert claim.status == ms.MISSING


# --- the inverse failure: a server nobody wrote down -------------------------


def test_a_resident_server_no_record_mentions_is_unrecorded() -> None:
    got = ms.unrecorded([], [proc(4242, 74.2)])
    assert [c.status for c in got] == [ms.UNRECORDED]
    assert got[0].resident_gib == 74.2


def test_an_idle_server_is_not_an_occupant() -> None:
    """An `ollama serve` nobody has asked for anything sits near zero GiB.

    Counting it made the survey report BUSY on an idle machine, which is the
    opposite of the mistake it was written to stop.
    """
    assert ms.unrecorded([], [proc(4242, 0.1, "/usr/local/bin/ollama serve")]) == []


def test_a_server_a_record_already_names_is_not_reported_twice() -> None:
    claim = ms.Claim("peer-status", "ds4-server", 4242, ms.RUNNING, "", "command")
    assert ms.unrecorded([claim], [proc(4242, 74.2)]) == []


# --- the verdict, which fails closed -----------------------------------------


def running(pid: int, gib: float | None) -> ms.Claim:
    return ms.Claim(
        "peer-status", "ds4-server", pid, ms.RUNNING, "", "command", None, gib
    )


def test_nothing_running_is_free() -> None:
    state, why = ms.verdict([ms.Claim("run-lock", "lock", None, ms.MISSING, "")])
    assert state == ms.FREE
    assert "no model server is resident" in why


def test_a_resident_server_is_busy() -> None:
    state, _ = ms.verdict([running(4242, 74.2)])
    assert state == ms.BUSY


def test_a_running_but_empty_server_is_not_busy() -> None:
    state, _ = ms.verdict([running(4242, 0.1)])
    assert state == ms.FREE


def test_a_held_lock_is_busy_even_with_nothing_resident() -> None:
    """A run between arms holds the lock with no server up. Free means free."""
    lock = ms.Claim("run-lock", "decode_ab.py", 4242, ms.RUNNING, "", "start_key")
    state, _ = ms.verdict([lock])
    assert state == ms.BUSY


def test_a_session_claim_holds_the_machine_without_a_pid() -> None:
    """#275: a claim held by an agent session is BUSY though no process backs
    it, and it is the occupant, so the survey does not read FREE past a claim
    whose acquiring CLI has exited."""
    claim = ms.Claim("run-lock", "decode A/B", None, ms.CLAIMED, "session claim")
    state, _ = ms.verdict([claim])
    assert state == ms.BUSY
    assert ms.occupant([claim]) is claim


def test_describe_names_the_agent_when_a_session_claim_has_no_pid() -> None:
    """'Currently on GPU' must not read 'pid None' for a session claim (#275)."""
    claim = ms.Claim("run-lock", "decode A/B", None, ms.CLAIMED, "", "opus-llama")
    phrase = ms.describe(claim)
    assert "pid None" not in phrase
    assert "opus-llama" in phrase


def test_lock_claim_reads_a_session_claim_as_claimed(tmp_path, dead_pid) -> None:
    """A session-claim lock file, whose recorded pid has since died, must not
    collapse to STALE the way a pid-held lock would (#275)."""
    p = tmp_path / "run-lock.json"
    p.write_text(
        json.dumps(
            {
                "hostname": ms.platform.node(),
                "pid": dead_pid,
                "what": "decode A/B",
                "agent": "opus-llama",
                "session_claim": True,
            }
        )
    )
    claim = ms.lock_claim(p)
    assert claim.status == ms.CLAIMED
    assert ms.verdict([claim])[0] == ms.BUSY


def test_a_pid_that_cannot_be_confirmed_is_uncertain_not_free() -> None:
    """The rule that matters. Not knowing is not the same as knowing it is
    free, and reporting FREE invites a second run onto a busy machine."""
    claim = ms.Claim("peer-status", "ds4-server", 4242, ms.UNCONFIRMED, "dunno")
    state, why = ms.verdict([claim])
    assert state == ms.UNCERTAIN
    assert state != ms.FREE
    assert "dunno" in why


def test_busy_beats_uncertain() -> None:
    state, _ = ms.verdict(
        [running(1, 74.2), ms.Claim("peer-status", "y", 2, ms.UNCONFIRMED, "")]
    )
    assert state == ms.BUSY


def test_the_exit_codes_let_a_loop_branch_without_parsing() -> None:
    assert ms.EXIT[ms.FREE] == 0
    assert ms.EXIT[ms.BUSY] == 3
    assert ms.EXIT[ms.UNCERTAIN] == 4
    assert len(set(ms.EXIT.values())) == 3


# --- the occupant, which every status update has to open with ----------------


def test_the_occupant_is_the_biggest_resident_server() -> None:
    got = ms.occupant([running(1, 74.2), running(2, 31.0)])
    assert got is not None and got.pid == 1


def test_an_idle_machine_has_no_occupant() -> None:
    assert ms.occupant([running(1, 0.1)]) is None


def held_lock(pid: int = 4242) -> ms.Claim:
    return ms.Claim("run-lock", "decode_ab_stack.py base vs head", pid, ms.RUNNING, "")


def test_a_sweep_with_no_resident_server_is_still_an_occupant() -> None:
    """The #267 A/B, which read `idle` for its whole 21 minutes.

    A ds4-bench sweep spawns a fresh process per rep that loads the model,
    measures and exits, and `preflight.INFERENCE` lists servers only -- so
    there is often no process to see. During a sweep the occupant is the
    WORK, which is what the lock records.
    """
    got = ms.occupant([held_lock()])
    assert got is not None
    assert got.what == "decode_ab_stack.py base vs head"


def test_a_resident_server_outranks_the_lock() -> None:
    """When there IS something to name, name it rather than the lock."""
    got = ms.occupant([held_lock(), running(7, 74.2)])
    assert got is not None and got.pid == 7


def test_a_stale_lock_is_not_an_occupant() -> None:
    """A holder that is gone holds nothing."""
    stale = ms.Claim("run-lock", "decode_ab.py", 4242, ms.STALE, "")
    assert ms.occupant([stale]) is None


def test_the_sentence_has_one_owner() -> None:
    """peer_brief and the CLI both print it; two builders would drift."""
    assert ms.describe(None) == "idle"
    assert ms.describe(running(7, 74.2)) == "ds4-server pid 7, 74.2 GiB"
    lock = ms.describe(held_lock())
    assert "decode_ab_stack.py base vs head pid 4242" in lock
    assert "no model resident" in lock, "say why there is no size, not None GiB"
    assert "None" not in lock


def test_the_survey_carries_the_rendered_line(tmp_path, peer_file) -> None:
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[proc(4242, 74.2)],
    )
    assert got["occupant_line"] == "ds4-server pid 4242, 74.2 GiB for 10m"


# --- the whole survey, with nothing left to the host -------------------------


def test_the_survey_reads_the_live_number_not_the_recorded_one(
    tmp_path, peer_file, monkeypatch
) -> None:
    """The record said 74.2 GiB. What matters is what the pid holds NOW."""
    write_peer(peer_file, [{"short": "python", "pid": os.getpid(), "gib": 74.2}])
    later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    os.utime(peer_file, (later.timestamp(), later.timestamp()))
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[proc(os.getpid(), 0.3, "python")],
    )
    rows = [c for c in got["claims"] if c["source"] == "peer-status"]
    assert rows[0]["resident_gib"] == 0.3
    assert got["verdict"] == ms.FREE
    assert got["occupant"] is None


def test_the_survey_names_the_occupant(tmp_path, peer_file) -> None:
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[proc(4242, 74.2)],
    )
    assert got["verdict"] == ms.BUSY
    assert got["occupant"]["pid"] == 4242
    assert got["occupant"]["resident_gib"] == 74.2


def test_the_survey_is_json_a_second_agent_can_read(tmp_path, peer_file) -> None:
    """Both agents read one answer; a dict that will not serialize is not one."""
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[],
    )
    round_tripped = json.loads(json.dumps(got))
    assert round_tripped["verdict"] == ms.FREE
    assert round_tripped["hostname"]
    assert "T" in round_tripped["checked_at"], "ISO 8601, with an offset"


def test_every_timestamp_carries_an_offset(tmp_path, peer_file) -> None:
    """scripts/backfill_iso8601.py exists because a naive stamp was compared
    against a Z stamp -- a four-hour error with no error message."""
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[],
    )
    parsed = dt.datetime.strptime(str(got["checked_at"]), "%Y-%m-%dT%H:%M:%S%z")
    assert parsed.tzinfo is not None


# --- how long it has held (#265) ---------------------------------------------


def _lock_claim_with(
    monkeypatch, tmp_path, *, began, taken=None, what="greedy_mtp_ab.py (#151)"
):
    """A RUNNING lock claim: holder started at `began`, lock taken at `taken`.

    The two are separate on purpose. `preflight.acquire_lock` opens the file
    `O_CREAT | O_EXCL` and never rewrites it, so its mtime is the moment the
    lock was taken -- which is what "held" means, and which an agent session
    hours older than its own lock does not share.
    """
    path = tmp_path / "run-lock.json"
    path.write_text(
        json.dumps(
            {
                "cwd": str(ROOT),
                "hostname": os.uname().nodename,
                "pid": 4242,
                # Deliberately a LIE, and far younger than the real start time
                # below. Nothing may read it: see the test that follows.
                "started": dt.datetime.now(dt.UTC).isoformat(),
                "what": what,
            }
        )
    )
    if taken is not None:
        stamp = taken.timestamp()
        os.utime(path, (stamp, stamp))
    monkeypatch.setattr(
        preflight, "read_lock", lambda p=None: json.loads(path.read_text())
    )
    monkeypatch.setattr(preflight, "lock_state", lambda *a, **k: ("theirs", "held"))
    monkeypatch.setattr(
        ms,
        "check_pid",
        lambda *a, **k: (ms.RUNNING, "pid 4242 is running", "start time"),
    )
    monkeypatch.setattr(ms, "started_at", lambda pid: began)
    return ms.lock_claim(path)


def test_the_occupant_says_how_long_it_has_held(monkeypatch, tmp_path) -> None:
    """#265 box 2: WHAT is not enough; a reader needs FOR HOW LONG.

    "ab=no" during a live A/B is what opened #265, and a monitor that names the
    occupant without a duration cannot separate a sweep that began a minute ago
    from one that has hung for three hours. That is the question a person
    actually asks on seeing the machine busy.
    """
    ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3, minutes=12)
    claim = _lock_claim_with(monkeypatch, tmp_path, began=ago, taken=ago)
    assert claim.held_s is not None
    assert 3 * 3600 <= claim.held_s <= 3 * 3600 + 13 * 60
    assert "for 3h" in ms.describe(claim)


def test_the_hold_time_is_not_the_records_own_started_field(
    monkeypatch, tmp_path
) -> None:
    """Never the number the record wrote down -- the module's whole thesis.

    It is why `resident_gib` is a live census. A record states an intention at
    the moment it was written; a stale or hand-edited `started` would report a
    three-hour hang as brand new, which is the direction that lets a stuck run
    keep the machine.

    The fixture's `started` says NOW while the lock was taken three hours ago,
    so a reader of that field produces ~0 and only a reader of the filesystem
    passes. `acquire_lock` writes the file `O_CREAT | O_EXCL` and never
    rewrites it, so the mtime is filesystem metadata, not a self-report.
    """
    ago = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)
    claim = _lock_claim_with(monkeypatch, tmp_path, began=ago, taken=ago)
    assert claim.held_s is not None and claim.held_s > 2 * 3600


def test_a_lock_holder_older_than_its_lock_reports_the_lock_not_its_lifetime(
    monkeypatch, tmp_path
) -> None:
    """The over-report this field shipped with, and the reason it was wrong.

    An agent session runs for hours and takes the lock for a two-minute sweep.
    Its process lifetime says eight hours; it has held the machine for two
    minutes. Reporting the lifetime tells a reader to go find a hung run.

    It over-reported structurally, not occasionally: `check_pid` calls a lock
    RUNNING only when the holder began at or before the record was written, so
    a lifetime was always >= the record's own age, never less.
    """
    claim = _lock_claim_with(
        monkeypatch,
        tmp_path,
        began=dt.datetime.now(dt.UTC) - dt.timedelta(hours=8),
        taken=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=2),
    )
    assert claim.held_s is not None
    assert claim.held_s < 10 * 60, "held the lock 2 minutes, not 8 hours"
    assert "for 8h" not in ms.describe(claim)


def test_a_dead_holder_reports_no_hold_time(monkeypatch, tmp_path) -> None:
    """A stale lock has no holder, so it has held for nothing.

    Reporting an age here would say a gone process has held the machine for
    three hours, which reads as "wait for it" when the correct instruction is
    "something died".
    """
    monkeypatch.setattr(
        ms, "check_pid", lambda *a, **k: (ms.STALE, "pid 4242 is gone", None)
    )
    began = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)
    path = tmp_path / "run-lock.json"
    path.write_text(json.dumps({"cwd": str(ROOT), "pid": 4242, "what": "x"}))
    monkeypatch.setattr(
        preflight, "read_lock", lambda p=None: json.loads(path.read_text())
    )
    monkeypatch.setattr(preflight, "lock_state", lambda *a, **k: ("theirs", "held"))
    monkeypatch.setattr(ms, "started_at", lambda pid: began)
    assert ms.lock_claim(path).held_s is None


def test_a_resident_server_occupant_says_how_long_it_has_held(
    tmp_path, peer_file
) -> None:
    """The case #265 opens with, and the one the field originally missed.

    `occupant()` prefers a resident server and falls back to the lock. The
    hold time was set in `lock_claim` alone, so whenever a server WAS resident
    -- an agent A/B, which is what #265 was written during -- the occupant
    line carried no duration at all. The fallback was the only path that
    worked, and it is the path taken least.
    """
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[dataclasses.replace(proc(4242, 74.2), age_s=2 * 3600 + 30 * 60)],
    )
    on = got["occupant"]
    assert on is not None, "a 74.2 GiB server is the occupant"
    assert on["held_s"] is not None, "and it must say how long it has held"
    assert 2 * 3600 <= on["held_s"] <= 3 * 3600
    assert "for 2h" in str(got["occupant_line"])


def test_a_servers_hold_time_is_its_lifetime_because_that_is_the_occupation(
    tmp_path, peer_file
) -> None:
    """Servers and locks read different clocks, and the difference is real.

    A server occupies the machine for exactly as long as it exists, so the OS
    answers it. A lock's holder can outlive nothing and predate everything --
    see the lock tests above. One field, two sources, because "how long has
    this held the machine" has two different answers.
    """
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[dataclasses.replace(proc(4242, 74.2), age_s=4 * 60)],
    )
    held = got["occupant"]["held_s"]
    assert held is not None and held < 10 * 60


def test_an_unreadable_start_time_leaves_the_hold_unstated(tmp_path, peer_file) -> None:
    """No number beats a wrong one. A missing start time says nothing.

    The census can carry no age -- `ps` prints an `etime` that does not parse,
    or a row assembled without one. Rendering "for 0s" there would report a
    server that has just appeared, which is the opposite of what happened.
    """
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[dataclasses.replace(proc(4242, 74.2), age_s=None)],
    )
    assert got["occupant"]["held_s"] is None
    assert " for " not in str(got["occupant_line"])


# --- a bare GPU benchmark, holding the machine under no lock (#277) -----------

_PS_BENCH = (
    "  PID    RSS  ELAPSED COMMAND\n"
    "72405 2000000   05:23 "
    "/Users/x/git/ds4/ds4-bench -m /m/model.gguf --metal --csv out.csv\n"
)


def test_a_bare_benchmark_is_found_from_the_process_table() -> None:
    claims = ms.bench_claims(_PS_BENCH)
    assert [c.status for c in claims] == [ms.UNRECORDED]
    assert claims[0].source == ms.BENCH_SOURCE
    assert claims[0].what == "ds4-bench"
    assert claims[0].pid == 72405


def test_a_benchmark_occupies_even_below_the_resident_model_bar() -> None:
    """A bench has no idle state: running at all means working the GPU (#277).

    A server below RESIDENT_GIB is idle and does not occupy; a bench below it is
    a bench that has not finished loading, and it still owns the device.
    """
    tiny = (
        "  PID    RSS  ELAPSED COMMAND\n"
        "72405 50000   00:02 /x/ds4-bench -m /m/model.gguf --metal\n"
    )
    claim = ms.bench_claims(tiny)[0]
    assert claim.resident_gib < ms.RESIDENT_GIB
    assert claim.occupies


def test_a_process_that_merely_names_ds4_bench_is_not_matched() -> None:
    """The executable, not the argv -- the pgrep -f self-match this repo has
    paid for twice. A shell whose command line mentions ds4-bench is not it."""
    shell = (
        "  PID    RSS  ELAPSED COMMAND\n88888 1000   00:10 /bin/zsh -c echo ds4-bench\n"
    )
    assert ms.bench_claims(shell) == []


def test_speed_bench_is_not_matched_it_is_a_directory() -> None:
    """`speed-bench` is the prompt directory in the argv, never a binary."""
    d = (
        "  PID    RSS  ELAPSED COMMAND\n"
        "90001 2000000   01:00 /x/ds4-server --prompt /x/speed-bench/corpus.txt\n"
    )
    assert ms.bench_claims(d) == []


def test_the_survey_reports_a_bare_benchmark_as_busy(tmp_path, peer_file) -> None:
    """The #277 scenario exactly: no lock, no resident server, a ds4-bench on
    the GPU. The tool used to read FREE / 'Currently on GPU: idle'."""
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[],  # nothing resident
        bench_text=_PS_BENCH,
    )
    assert got["verdict"] == ms.BUSY
    assert "ds4-bench" in got["occupant_line"]


def test_no_benchmark_leaves_the_survey_free(tmp_path, peer_file) -> None:
    """The bench census must not make an idle machine read busy."""
    write_peer(peer_file, [])
    got = ms.survey(
        lock_path=tmp_path / "no-lock.json",
        peer_path=peer_file,
        unit_dir=tmp_path / "units",
        procs=[],
        bench_text="  PID    RSS  ELAPSED COMMAND\n",
    )
    assert got["verdict"] == ms.FREE
