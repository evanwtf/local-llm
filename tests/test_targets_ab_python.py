"""The #146 targets A/B driver, ported from shell (#235).

The regression test that matters is the last one. On 2026-09-06 five dry runs
of the shell each reached an unguarded `pkill -f qwen_tool_shim` and killed the
shim belonging to the REAL batch in progress. Run 4's smoke gate got
"Connection refused", the batch ended after three runs -- legacy once, sandbox
twice -- and could not satisfy its own pre-registration. The machine lock did
not catch it: a dry run skips the lock, so it was invisible to the running
batch and destructive to it.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import batch as batchlib
import strip_ab_report
import strip_toggle_ab
import targets_ab
import tool_shim
from source_text import code_of

# ------------------------------------------------------------------- #146 arms


def test_the_arm_is_a_run_py_flag() -> None:
    assert targets_ab.ARMS == ("legacy", "sandbox")


def test_the_run_directory_matches_what_the_report_parses(tmp_path) -> None:
    b = batchlib.Batch(
        repo=ROOT,
        results=tmp_path / "r.jsonl",
        manifest=tmp_path / "m.jsonl",
        logdir=tmp_path,
        bench_logs=tmp_path,
        batch="0906-0743",
        harness_head="abc1234",
        backend=targets_ab.BACKEND,
        prefix=targets_ab.PREFIX,
    )
    assert b.run_dir(1, "legacy").name == "146-targets-legacy-0906-0743-run1"
    assert strip_ab_report.batch_of({"dir": str(b.run_dir(1, "legacy"))}) == "0906-0743"


def test_a_dry_run_must_move_off_both_real_paths(tmp_path) -> None:
    real = targets_ab.DEFAULT_RESULTS
    with pytest.raises(batchlib.Void):
        targets_ab.check_dry_run_paths(real, targets_ab.manifest_for(real))
    with pytest.raises(batchlib.Void):
        targets_ab.check_dry_run_paths(
            tmp_path / "r.jsonl", targets_ab.manifest_for(real)
        )
    # Both moved: allowed.
    targets_ab.check_dry_run_paths(tmp_path / "r.jsonl", tmp_path / "m.jsonl")


def test_a_dry_run_starts_nothing_and_stops_nothing(tmp_path, monkeypatch) -> None:
    """The 2026-09-06 regression, asserted.

    A dry run must not reach the shim, the server, the lock or run.py. Anything
    it touches, it can destroy for a batch it cannot see.
    """

    def explode(*args, **kwargs):
        raise AssertionError("a dry run must not touch the machine")

    monkeypatch.setattr(tool_shim, "serving", explode)
    monkeypatch.setattr(targets_ab.ds4_server, "serving", explode)
    monkeypatch.setattr(targets_ab.batchlib, "machine", explode)
    monkeypatch.setattr(targets_ab.batchlib, "run_one", explode)
    monkeypatch.setattr(targets_ab, "sync_targets", explode)

    manifest = tmp_path / "m.jsonl"
    rc = targets_ab.sweep(
        runs=4,
        until=None,
        results=tmp_path / "r.jsonl",
        manifest=manifest,
        logdir=tmp_path / "logs",
        batch_id="0906-0743",
        dry=True,
        owner_pid=1,
    )
    assert rc == 0
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert [r["arm"] for r in rows] == ["legacy", "sandbox", "sandbox", "legacy"]
    assert all(r["dry"] is True for r in rows)


def test_a_dry_run_does_not_write_to_the_real_results(tmp_path) -> None:
    with pytest.raises(batchlib.Void):
        targets_ab.sweep(
            runs=4,
            until=None,
            results=targets_ab.DEFAULT_RESULTS,
            manifest=targets_ab.manifest_for(targets_ab.DEFAULT_RESULTS),
            logdir=tmp_path,
            batch_id="b",
            dry=True,
            owner_pid=1,
        )


# ------------------------------------------------------------------- #112 arms


def test_the_arm_is_the_shims_mode() -> None:
    assert strip_toggle_ab.ARMS == ("on", "off")
    assert strip_toggle_ab.strip_for("on") is True
    assert strip_toggle_ab.strip_for("off") is False


def test_a_typo_in_an_arm_name_raises_rather_than_picking_one() -> None:
    # Defaulting to strip-on would silently run the wrong experiment, which is
    # the whole failure mode #112 exists to prevent.
    with pytest.raises(ValueError):
        strip_toggle_ab.strip_for("ON")


def test_the_112_run_directory_matches_the_report(tmp_path) -> None:
    b = batchlib.Batch(
        repo=ROOT,
        results=tmp_path / "r.jsonl",
        manifest=tmp_path / "m.jsonl",
        logdir=tmp_path,
        bench_logs=tmp_path,
        batch="0906-0743",
        harness_head="abc1234",
        backend=strip_toggle_ab.BACKEND,
        prefix=strip_toggle_ab.PREFIX,
    )
    assert b.run_dir(2, "off").name == "112-strip-off-0906-0743-run2"


def test_the_112_manifest_keeps_its_utc_stamps() -> None:
    # The existing manifest is UTC-with-Z. A re-run appending local-with-offset
    # would mix forms in one file; `epoch` reads both, but there is no reason
    # to make a reader check which.
    source = code_of(ROOT / "scripts" / "strip_toggle_ab.py")
    assert "utc=True" in source


# ------------------------------------------------------------------ both, still


def test_neither_driver_calls_pgrep() -> None:
    for name in ("targets_ab.py", "strip_toggle_ab.py"):
        code = code_of(ROOT / "scripts" / name)
        assert "pgrep" not in code, name
        assert "pkill" not in code, name


# ------------------------------------------------------- #112 disk-KV mechanism


def test_the_kv_budget_reaches_the_server() -> None:
    import disk_kv_mechanism as kv

    argv = kv.server_command(32768)
    assert argv[argv.index("--kv-disk-space-mb") + 1] == "32768"


def test_the_raised_budget_is_the_only_difference_from_the_default() -> None:
    import disk_kv_mechanism as kv

    raised = kv.server_command(32768)
    default = kv.server_command(8192)
    assert [a for a in raised if a != "32768"] == [a for a in default if a != "8192"]


def test_the_disk_kv_test_takes_the_lock_through_run_py() -> None:
    # It holds no lock of its own, so run.py must be allowed to take one. A
    # copied --no-lock would leave the machine unclaimed for 90 minutes.
    import disk_kv_mechanism as kv

    assert "--no-lock" not in kv.run_argv(3)


def test_the_disk_kv_test_neither_starts_nor_stops_the_shim() -> None:
    # Its header says so: the :8101 shim belongs to whatever else is using the
    # machine. `require_running` refuses; `serving` would start one.
    import disk_kv_mechanism as kv

    source = code_of(ROOT / "scripts" / "disk_kv_mechanism.py")
    assert "require_running" in source
    assert "tool_shim.serving" not in source
    assert kv.SHIM_PORT == 8101


def test_the_dry_run_sees_the_cutoff_fire(tmp_path, monkeypatch) -> None:
    """The check has to be in the branch a person rehearses.

    The port first had two loops -- one for a dry run, one for a real batch --
    and only the real one asked whether the cutoff had passed, while
    `--dry-run`'s own help promised "the cutoff and the arm order". A guard
    that lives only in the branch nobody rehearses is a guard nobody has ever
    watched fire, and #175 is what that costs: `[ "23:35" \\< "2359" ]` is
    true, so the shell's cutoff fired every night at 23:00 and its author
    never saw it.

    So: one loop, and `--dry-run` swaps only the per-run body. Here the clock
    advances half an hour per read and walks past the cutoff mid-batch.
    """
    start = 1_788_000_000.0  # a fixed instant; the arithmetic is what matters
    reads = iter(range(100))
    monkeypatch.setattr(batchlib, "now", lambda: start + 1800.0 * next(reads))
    monkeypatch.setattr(
        batchlib,
        "resolve_until",
        lambda hhmm, at=None: start + 3600.0,  # two reads from now
    )

    manifest = tmp_path / "m.jsonl"
    with pytest.raises(batchlib.Void) as caught:
        targets_ab.sweep(
            runs=4,
            until="09:30",
            results=tmp_path / "r.jsonl",
            manifest=manifest,
            logdir=tmp_path / "logs",
            batch_id="0909-0800",
            dry=True,
            owner_pid=1,
        )
    assert "a partial batch is no result" in str(caught.value)

    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert rows[-1]["arm"] == "VOID", "the manifest must say the batch voided"
    assert rows[-1]["reason"] == "past-until"
    assert len(rows) < 5, "it stopped; it did not quietly run all four"
