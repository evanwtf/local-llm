"""scripts/heartbeat.py: the operator heartbeat and its stall flags.

The subprocess, exporter and gh boundaries are untested. The field order, the
parsers and the flags are the logic, pinned here: the flags are what would
have shown the 2026-09-30 cluster idle (server ready at 03:21, nothing
launched until 05:44) while the heartbeat repeated "server compiling".
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import heartbeat as hb

EDT = dt.timezone(dt.timedelta(hours=-4), "EDT")
NOW = dt.datetime(2026, 9, 30, 5, 37, tzinfo=EDT)


def ago(minutes: float) -> str:
    return (NOW - dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


BUSY = {"issue": 840, "task": "#840 server compiling, then 3 trials", "updated": ago(5)}
#: started 30 min ago, due in 60: satisfies the no-ETA flag without firing past-ETA
TIMED = {"started_at": ago(30), "eta": ago(-60)}


def test_fields_come_in_the_operators_order() -> None:
    body = hb.render(
        NOW,
        "M5 Max",
        BUSY | {"next": "#737", "questions": ["approve the 115 GB download? (#451)"]},
        sensors=["SoC 41 W · GPU 71 °C"],
        disk="1,325 GB (33% free) on /Volumes/Models",
        prs="#851 opener [BLOCKED, auto-merge]",
        alerts=[],
    )
    order = [
        "**2026-09-30 05:37** — M5 Max",
        "- **Task:** #840",
        "- **Sensors:** SoC 41 W",
        "- **Disk:** 1,325 GB (33% free)",
        "- **PRs:** #851",
        "- **Next:** #737",
        "- **Questions for you:**\n  1. approve the 115 GB download? (#451)",
    ]
    positions = [body.index(o) for o in order]
    assert positions == sorted(positions)
    assert body.startswith("**2026-09-30 05:37**")  # the time comes first


def test_no_gpu_utilization_in_the_sensors_line() -> None:
    # The operator, 2026-09-30: utilization is "a nearly useless metric".
    line = hb.format_sensors(
        hb.Sensors(gpu_w=41.2, wall_w=88.0, gpu_c=71.4, cpu_c=64.0, cpu_busy_pct=12.3)
    )
    assert line.startswith("GPU 41 W · input 88 W · GPU 71 °C")
    assert "util" not in line and "%" in line.split("CPU")[-1]


def test_questions_say_none_when_there_are_none() -> None:
    body = hb.render(NOW, "x", BUSY, sensors=[], disk="d", prs="p", alerts=[])
    assert "- **Questions for you:** none" in body


def test_the_issue_joins_the_task_once() -> None:
    body = hb.render(
        NOW,
        "x",
        {"issue": 692, "task": "Ollama run"},
        sensors=[],
        disk="d",
        prs="p",
        alerts=[],
    )
    assert "- **Task:** Ollama run (#692)" in body
    assert "(#840)" not in hb.render(
        NOW, "x", BUSY, sensors=[], disk="d", prs="p", alerts=[]
    )


def test_disk_gives_gb_and_percent_and_the_download_floor() -> None:
    assert hb.format_disk(1_325e9, 4_000e9, "/Volumes/Models") == (
        "1,325 GB (33% free) on /Volumes/Models, under the 1,500 GB download floor"
    )
    assert hb.format_disk(2_000e9, 4_000e9, "/") == "2,000 GB (50% free) on /"


def test_prs_list_state_and_auto_merge() -> None:
    prs = [
        {"number": 852, "title": "b", "mergeStateStatus": "BEHIND", "isDraft": True},
        {
            "number": 851,
            "title": "a",
            "mergeStateStatus": "CLEAN",
            "autoMergeRequest": {"x": 1},
        },
    ]
    assert hb.format_prs(prs) == "#851 a [CLEAN, auto-merge]; #852 b [draft, BEHIND]"
    assert hb.format_prs([]) == "none open"
    assert hb.format_prs(None) == "n/a (gh failed)"


# -- the stall flags ----------------------------------------------------------


def test_idle_power_on_a_busy_task_fires_on_the_second_reading() -> None:
    first = hb.idle_power_updates(BUSY, gpu_idle=True, now=NOW)
    assert first == {"idle_power_since": NOW.isoformat(timespec="seconds")}
    # one reading is a sample that may fall in a tool gap: no flag yet
    assert not any(
        "Idle power" in f
        for f in hb.flags(BUSY | first, NOW, gpu_idle=True, log_age_min=None)
    )
    later = NOW + dt.timedelta(minutes=30)
    second = hb.idle_power_updates(BUSY | first, gpu_idle=True, now=later)
    assert second == first  # the start is kept, not reset
    got = hb.flags(BUSY | second, later, gpu_idle=True, log_age_min=None)
    assert any("idle floor for 30 min" in f for f in got)


def test_a_working_reading_clears_idle_power() -> None:
    state = BUSY | {"idle_power_since": ago(30)}
    assert hb.idle_power_updates(state, gpu_idle=False, now=NOW) == {
        "idle_power_since": None
    }
    # an idle task at idle power is not a stall
    idle = {"task": "idle", "idle_power_since": ago(30)}
    assert hb.idle_power_updates(idle, gpu_idle=True, now=NOW) == {
        "idle_power_since": None
    }


def test_overdue_wait_is_flagged() -> None:
    got = hb.flags(BUSY | {"expect_by": ago(40)}, NOW, gpu_idle=False, log_age_min=None)
    assert any("**Overdue:** the current wait was due 40 min ago" in f for f in got)
    assert not any(
        "Overdue" in f
        for f in hb.flags(
            BUSY | {"expect_by": (NOW + dt.timedelta(minutes=5)).isoformat()},
            NOW,
            gpu_idle=False,
            log_age_min=None,
        )
    )


def test_quiet_log_is_flagged_only_for_a_busy_task() -> None:
    assert any(
        "last written 25 min ago" in f
        for f in hb.flags(BUSY, NOW, gpu_idle=False, log_age_min=25)
    )
    idle = {"task": "idle: weekly cap until 08:18 (#762)", "updated": ago(1)} | TIMED
    assert hb.flags(idle, NOW, gpu_idle=True, log_age_min=90) == []


def test_stale_state_is_flagged() -> None:
    got = hb.flags(BUSY | {"updated": ago(137)}, NOW, gpu_idle=False, log_age_min=None)
    assert any("last updated its task 137 min ago" in f for f in got)


def test_idle_needs_a_reason_or_a_question() -> None:
    bare = hb.flags(
        {"task": "idle", "updated": ago(1)}, NOW, gpu_idle=True, log_age_min=None
    )
    assert any("Idle, no reason" in f for f in bare)
    asked = {"task": "idle", "updated": ago(1), "questions": ["approve #451?"]} | TIMED
    assert hb.flags(asked, NOW, gpu_idle=True, log_age_min=None) == []


def test_loop_expiry_warning() -> None:
    old = (NOW - dt.timedelta(days=6, hours=2)).isoformat()
    got = hb.flags(BUSY | {"loop_armed_at": old}, NOW, gpu_idle=False, log_age_min=None)
    assert any("Loop expiring" in f for f in got)


def test_the_2026_09_30_cluster_idle_would_have_been_flagged() -> None:
    """The real window: task frozen at 'server compiling', both GPUs at 10-14 W,
    notes 107 min old, the wait due at 03:32. Every signal fires."""
    state = {
        "issue": 840,
        "task": "#840 glm53ftfjaydual2xrc cluster screen: server compiling,"
        " then remote-client 3 trials",
        "updated": ago(107),
        "expect_by": "2026-09-30T03:32:00-04:00",
        "idle_power_since": ago(107),
    }
    got = " ".join(hb.flags(state, NOW, gpu_idle=True, log_age_min=None))
    for signal in ("Idle power, busy task", "Overdue", "Stale state"):
        assert signal in got


# -- parsers ------------------------------------------------------------------


def test_parse_top_cpu_takes_the_last_sample() -> None:
    text = (
        "CPU usage: 50.0% user, 20.0% sys, 30.0% idle\n"
        "CPU usage: 5.12% user, 7.30% sys, 87.58% idle\n"
    )
    assert hb.parse_top_cpu(text) == pytest.approx(12.42)
    assert hb.parse_top_cpu("") is None


def test_parse_proc_stat_busy_fraction() -> None:
    before = "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 1 1 1\n"
    after = "cpu  200 0 200 1500 100 0 0 0 0 0\n"
    # +200 busy, +800 idle(+iowait) of +1000 total
    assert hb.parse_proc_stat(before, after) == pytest.approx(20.0)
    assert hb.parse_proc_stat("", after) is None


def test_parse_nvidia_smi() -> None:
    assert hb.parse_nvidia_smi("21.37, 41, 30\n") == (21.37, 41.0, 30.0)
    assert hb.parse_nvidia_smi("[N/A], 41, [N/A]") == (None, 41.0, None)
    assert hb.parse_nvidia_smi("") == (None, None, None)


# -- state and watchdog -------------------------------------------------------


def test_bookkeeping_does_not_reset_the_session_clock(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "hb.json"
    hb.write_state(p, {"task": "a"}, NOW)
    later = NOW + dt.timedelta(minutes=50)
    s = hb.write_state(p, {"heartbeat_at": later.isoformat()}, later, stamp=False)
    assert s["updated"] == NOW.isoformat(timespec="seconds")
    assert hb.read_state(p) == s


def test_watchdog_returns_at_once_when_the_heartbeat_is_stale(
    tmp_path: pathlib.Path,
) -> None:
    p = tmp_path / "hb.json"
    old = (dt.datetime.now().astimezone() - dt.timedelta(minutes=40)).isoformat()
    # the cluster timer's heartbeat_at is fresh; only the session's tick counts
    fresh = dt.datetime.now().astimezone().isoformat()
    hb.write_state(p, {"tick_at": old, "heartbeat_at": fresh}, NOW, stamp=False)
    msg = hb.watchdog(p, stale_min=35, poll_s=0)
    assert msg.startswith("WATCHDOG: no tick since") and "re-arm" in msg
    assert "WATCHDOG" in hb.watchdog(tmp_path / "missing.json", 35, 0)


def test_tick_records_only_the_pulse(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "hb.json"
    hb.write_state(p, {"task": "a"}, NOW)
    assert hb.main(["--state", str(p), "--tick"]) == 0
    s = hb.read_state(p)
    assert s["task"] == "a" and s["updated"] == NOW.isoformat(timespec="seconds")
    since = hb.minutes_since(s["tick_at"], dt.datetime.now().astimezone())
    assert since is not None and since < 1


def test_tick_with_set_records_both(tmp_path: pathlib.Path) -> None:
    """--set once returned before --tick, so the tick was lost (#860)."""
    p = tmp_path / "hb.json"
    assert hb.main(["--state", str(p), "--tick", "--set", '{"task": "b"}']) == 0
    s = hb.read_state(p)
    assert s["task"] == "b"
    since = hb.minutes_since(s["tick_at"], dt.datetime.now().astimezone())
    assert since is not None and since < 1


def test_set_alone_is_not_a_tick(tmp_path: pathlib.Path) -> None:
    """A mid-turn state write must not silence the watchdog."""
    p = tmp_path / "hb.json"
    assert hb.main(["--state", str(p), "--set", '{"task": "c"}']) == 0
    assert "tick_at" not in hb.read_state(p)


def test_concurrent_writers_lose_no_update(tmp_path: pathlib.Path) -> None:
    """The session and the cluster timer write one file (Codex review, #851)."""
    import multiprocessing as mp

    p = tmp_path / "hb.json"
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_write_key, args=(str(p), f"k{i}")) for i in range(8)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
        assert proc.exitcode == 0
    state = hb.read_state(p)
    assert {f"k{i}" for i in range(8)} <= set(state)
    assert not list(tmp_path.glob("*.tmp"))


def _write_key(path: str, key: str) -> None:
    for n in range(20):
        hb.write_state(pathlib.Path(path), {key: n}, NOW, stamp=False)


def test_armed_records_a_tick_so_the_watchdog_can_start(
    tmp_path: pathlib.Path,
) -> None:
    """With no tick_at the watchdog fires at once (the DGX, 2026-09-30)."""
    p = tmp_path / "hb.json"
    assert hb.main(["--state", str(p), "--armed"]) == 0
    s = hb.read_state(p)
    assert s["tick_at"] == s["loop_armed_at"]
    assert "updated" not in s  # bookkeeping, not the session's progress clock


def test_fans_round_to_the_nearest_100_rpm() -> None:
    # the operator, 2026-09-30: "3642 rpm would just be 3600rpm"
    assert hb.format_rpm([3642]) == "3,600 rpm"
    assert hb.format_rpm([3650, 7290, 0]) == "3,700 / 7,300 / 0 rpm"
    assert hb.format_rpm([]) == "n/a"


# -- timing and the signature (#860) -------------------------------------------


def test_timing_shows_start_and_eta_with_spans() -> None:
    got = hb.format_timing({"started_at": ago(126), "eta": ago(-114)}, NOW)
    assert got == "started 03:31 (2 h 6 min ago) · ETA 07:31 (in 1 h 54 min)"


def test_timing_says_what_is_missing_and_what_has_passed() -> None:
    assert hb.format_timing({}, NOW) == "started not recorded · ETA not recorded"
    got = hb.format_timing({"started_at": ago(90), "eta": ago(15)}, NOW)
    assert got.endswith("ETA 05:22 (passed 15 min ago)")


def test_timing_carries_the_date_when_it_is_not_today() -> None:
    got = hb.format_timing({"started_at": "2026-09-29T22:00:00-04:00"}, NOW)
    assert got.startswith("started 2026-09-29 22:00 (7 h 37 min ago)")


def test_the_timing_line_follows_the_task() -> None:
    body = hb.render(
        NOW, "M5 Max", BUSY | TIMED, sensors=["s"], disk="d", prs="p", alerts=[]
    )
    lines = body.splitlines()
    task = next(i for i, ln in enumerate(lines) if ln.startswith("- **Task:**"))
    assert lines[task + 1].startswith("- **Timing:** started 05:07")


def test_a_task_without_start_or_eta_is_flagged() -> None:
    got = hb.flags(BUSY, NOW, gpu_idle=False, log_age_min=None)
    assert any("No ETA" in f for f in got)
    assert not any(
        "No ETA" in f
        for f in hb.flags(BUSY | TIMED, NOW, gpu_idle=False, log_age_min=None)
    )


def test_a_passed_eta_is_flagged() -> None:
    late = BUSY | {"started_at": ago(200), "eta": ago(25)}
    got = hb.flags(late, NOW, gpu_idle=False, log_age_min=None)
    assert any("Past ETA" in f and "25 min ago" in f for f in got)


def _body() -> str:
    return hb.render(
        NOW,
        "DGX cluster",
        BUSY | TIMED,
        sensors=["**outlet** 180 W"],
        disk="d",
        prs="p",
        alerts=[],
    )


def test_a_signed_heartbeat_verifies() -> None:
    assert hb.verify(hb.sign(_body(), "scripts/cluster_heartbeat.py")) is None


def test_lines_around_the_heartbeat_are_allowed() -> None:
    msg = (
        "Tick done.\n\n"
        + hb.sign(_body(), "scripts/heartbeat.py")
        + "\n\n#840 trial 21 started."
    )
    assert hb.verify(msg) is None


def test_trailing_spaces_and_blank_lines_do_not_break_it() -> None:
    signed = hb.sign(_body(), "scripts/heartbeat.py")
    assert hb.verify(signed.replace("\n", "  \n\n")) is None


def test_an_edited_body_fails() -> None:
    signed = hb.sign(_body(), "scripts/heartbeat.py")
    assert "does not match" in (hb.verify(signed.replace("180 W", "150 W")) or "")


def test_a_hand_written_heartbeat_fails() -> None:
    """The DGX's 08:04 tick on 2026-09-30: bullets, no outlet, no signature."""
    freehand = (
        "2026-09-30 08:04 EDT — DGX cluster\n\n"
        "* On GPU: #840 glm53ftfjaydual2xrc, 20/42 trials\n"
        "* Sensors: both ~63 °C\n* Disk: head 648 GB (16% free)"
    )
    assert "no signature" in (hb.verify(freehand) or "")


def test_a_copied_signature_under_a_hand_written_body_fails() -> None:
    sig = hb.sign(_body(), "scripts/heartbeat.py").splitlines()[-1]
    fake = "**2026-09-30 08:04** — DGX cluster\n\n- **Task:** fine\n\n" + sig
    assert "does not match" in (hb.verify(fake) or "")
