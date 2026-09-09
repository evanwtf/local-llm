"""The batch loop the #146 and #112 drivers share (#235).

Three things here are load-bearing and each cost something to learn:

* the cutoff **voids** rather than truncating -- a partial batch is no result;
* it compares integers, because a string compare of "23:35" against "2359" is
  true and fired the cutoff every hour of 23 (#175);
* the manifest is an interface. `strip_ab_report.py` maps a row to an arm by
  time window, so a stamp it cannot parse maps every row to no arm at all --
  which once happened to 118 of 120 rows.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ab_driver
import batch as batchlib
import strip_ab_report
from source_text import code_of


def at(hh: int, mm: int, day: int = 6) -> float:
    return dt.datetime(2026, 9, day, hh, mm, 30).timestamp()


# ------------------------------------------------------------------- the cutoff


def test_a_cutoff_later_today_resolves_to_today() -> None:
    got = batchlib.resolve_until("23:30", at=at(21, 0))
    assert dt.datetime.fromtimestamp(got) == dt.datetime(2026, 9, 6, 23, 30, 0)


def test_a_cutoff_already_past_resolves_to_tomorrow() -> None:
    got = batchlib.resolve_until("09:15", at=at(21, 0))
    assert dt.datetime.fromtimestamp(got) == dt.datetime(2026, 9, 7, 9, 15, 0)


def test_seconds_are_pinned_to_zero() -> None:
    # Both `date` dialects filled unspecified fields from the current time, so
    # a bare "23:59" carried the current second and the cutoff drifted up to
    # 59s past the injected clock. The at-cutoff test then passed only when the
    # current second happened to be 0.
    got = batchlib.resolve_until("23:59", at=at(21, 0))
    assert dt.datetime.fromtimestamp(got).second == 0


def test_at_exactly_the_cutoff_the_batch_voids() -> None:
    # Strictly past, not at-or-past, when resolving; >= when firing. The two
    # halves have to agree or the boundary run both starts and voids.
    cutoff = batchlib.resolve_until("23:30", at=at(21, 0))
    assert batchlib.past(cutoff, at=cutoff) is True
    assert batchlib.past(cutoff, at=cutoff - 1) is False


def test_the_comparison_is_on_numbers_not_strings() -> None:
    # #175: "23:35" < "2359" is True in shell, because ':' (58) beats '5' (53),
    # so the cutoff fired whenever the hour was 23. Here 23:35 is before 23:59.
    cutoff = batchlib.resolve_until("23:59", at=at(23, 35))
    assert batchlib.past(cutoff, at=at(23, 35)) is False


def test_no_cutoff_never_fires() -> None:
    assert batchlib.past(None) is False


def test_an_unparseable_cutoff_is_refused() -> None:
    with pytest.raises(ValueError):
        batchlib.resolve_until("2359")
    with pytest.raises(ValueError):
        batchlib.resolve_until("later")


def test_the_injected_clock_is_namespaced(monkeypatch) -> None:
    # A generic NOW in somebody's shell must not shift the cutoff.
    monkeypatch.setenv("NOW", "1")
    monkeypatch.delenv(batchlib.FAKE_NOW, raising=False)
    assert abs(batchlib.now() - dt.datetime.now().timestamp()) < 5
    monkeypatch.setenv(batchlib.FAKE_NOW, "1000000")
    assert batchlib.now() == 1000000.0


# -------------------------------------------------------------------- the order


def test_the_arms_run_a_b_b_a() -> None:
    assert batchlib.order(("legacy", "sandbox"), 4) == [
        "legacy",
        "sandbox",
        "sandbox",
        "legacy",
    ]


def test_the_order_matches_ab_driver_read_one_arm_at_a_time() -> None:
    # Two alternation policies would be one too many. This asserts they are the
    # same sequence rather than asserting it in prose.
    arms = [
        ab_driver.Arm(name="legacy", backend="x", serve=ab_driver.nothing),
        ab_driver.Arm(name="sandbox", backend="x", serve=ab_driver.nothing),
    ]
    flat = [a.name for n in range(1, 5) for a in ab_driver.order(arms, n)]
    assert flat == batchlib.order(("legacy", "sandbox"), 8)


def test_only_a_multiple_of_four_lets_both_arms_lead_equally() -> None:
    assert batchlib.leads_equally(4) is True
    assert batchlib.leads_equally(8) is True
    assert batchlib.leads_equally(2) is False
    assert batchlib.leads_equally(6) is False


# ----------------------------------------------------------------- the manifest


def test_both_stamp_forms_mean_the_same_instant() -> None:
    # targets_ab wrote local-with-offset, strip_toggle wrote UTC-with-Z. Both
    # are in existing manifests; `epoch` normalises them and a string compare
    # does not.
    moment = at(14, 3)
    local = batchlib.stamp(moment)
    utc = batchlib.stamp(moment, utc=True)
    assert local != utc
    assert strip_ab_report.epoch(local) == pytest.approx(
        strip_ab_report.epoch(utc), abs=1
    )


def test_a_written_window_maps_a_row_back_to_its_arm(tmp_path) -> None:
    manifest = tmp_path / "m.jsonl"
    batchlib.append(
        manifest,
        {
            "run": 2,
            "arm": "sandbox",
            "started": batchlib.stamp(at(10, 0)),
            "ended": batchlib.stamp(at(10, 30)),
            "dir": "/x/146-targets-sandbox-0906-0743-run2",
            "batch": "0906-0743",
        },
    )
    entries = [json.loads(line) for line in manifest.read_text().splitlines()]
    inside = dt.datetime.fromtimestamp(at(10, 15)).isoformat(timespec="seconds")
    assert strip_ab_report.arm_of(inside, entries) == "sandbox"
    assert strip_ab_report.run_of(inside, entries) == (2, "sandbox")


def test_a_row_outside_every_window_maps_to_no_arm(tmp_path) -> None:
    entries = [
        {
            "run": 1,
            "arm": "on",
            "started": batchlib.stamp(at(10, 0)),
            "ended": batchlib.stamp(at(10, 30)),
        }
    ]
    outside = dt.datetime.fromtimestamp(at(12, 0)).isoformat(timespec="seconds")
    assert strip_ab_report.arm_of(outside, entries) is None


def test_the_run_directory_carries_a_readable_batch_id(tmp_path) -> None:
    b = batchlib.Batch(
        repo=ROOT,
        results=tmp_path / "r.jsonl",
        manifest=tmp_path / "m.jsonl",
        logdir=tmp_path,
        bench_logs=tmp_path,
        batch="0906-0743",
        harness_head="abc1234",
        backend="qwen38fnds4shim",
        prefix="146-targets",
    )
    entry = {"dir": str(b.run_dir(3, "legacy"))}
    assert strip_ab_report.batch_of(entry) == "0906-0743"


def test_a_void_row_says_why(tmp_path) -> None:
    manifest = tmp_path / "m.jsonl"
    batchlib.void(manifest, 3, "23:30")
    entry = json.loads(manifest.read_text().strip())
    assert entry == {
        "run": 3,
        "arm": "VOID",
        "reason": "past-until",
        "until": "23:30",
    }


# ------------------------------------------------------- run.py's actual flags


def declared_flags(path: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name != "add_argument":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and str(arg.value).startswith("--"):
                found.add(str(arg.value))
    return found


def test_every_flag_the_batch_emits_is_one_run_py_declares(tmp_path) -> None:
    # #264: the #149 shell passed --skip-tensor-gate for weeks after run.py
    # removed it. Six server restarts, hours, zero rows, exit 0.
    b = batchlib.Batch(
        repo=ROOT,
        results=tmp_path / "r.jsonl",
        manifest=tmp_path / "m.jsonl",
        logdir=tmp_path,
        bench_logs=tmp_path,
        batch="0906-0743",
        harness_head="abc1234",
        backend="qwen38fnds4shim",
        prefix="146-targets",
    )
    emitted = {
        token
        for token in batchlib.argv(b, ["--targets", "sandbox"])
        if token.startswith("--")
    }
    declared = declared_flags(ROOT / "benchmarks" / "agent" / "run.py")
    assert emitted <= declared, f"run.py does not declare {sorted(emitted - declared)}"


def test_the_batch_holds_the_lock_so_a_run_must_not(tmp_path) -> None:
    b = batchlib.Batch(
        repo=ROOT,
        results=tmp_path / "r.jsonl",
        manifest=tmp_path / "m.jsonl",
        logdir=tmp_path,
        bench_logs=tmp_path,
        batch="b",
        harness_head="h",
        backend="qwen38fnds4shim",
        prefix="p",
    )
    assert "--no-lock" in batchlib.argv(b, [])


def test_the_shared_loop_calls_no_pgrep() -> None:
    assert "pgrep" not in code_of(ROOT / "scripts" / "lib" / "batch.py")
    assert "pkill" not in code_of(ROOT / "scripts" / "lib" / "batch.py")


def test_a_leftover_from_a_killed_run_is_not_this_runs_evidence(tmp_path) -> None:
    """`old-sweep1` once held 22 transcripts for a 15-task sweep.

    Seven were left by a run killed at 08:17 before its own move ran, and the
    next run of the same arm claimed them. `lib/transcript_move.sh` closed it
    with a marker file and `find -newer`; an mtime comparison needs neither.
    """
    import os
    import time as _time

    bench = tmp_path / "bench-logs"
    bench.mkdir()
    b = batchlib.Batch(
        repo=ROOT,
        results=tmp_path / "r.jsonl",
        manifest=tmp_path / "m.jsonl",
        logdir=tmp_path,
        bench_logs=bench,
        batch="b",
        harness_head="h",
        backend="qwen38fnds4shim",
        prefix="146-targets",
    )
    stale = bench / "mbox-scan-qwen38fnds4shim-opencode-1.stdout.jsonl"
    stale.write_text("{}")
    os.utime(stale, (1000, 1000))

    since = _time.time()
    _time.sleep(0.01)
    fresh = bench / "parser-date-qwen38fnds4shim-opencode-1.stdout.jsonl"
    fresh.write_text("{}")

    destination = tmp_path / "run1"
    assert batchlib.collect(b, destination, since) == 1
    assert (destination / fresh.name).exists()
    assert stale.exists()
