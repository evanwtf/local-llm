"""Tests for scripts/os_compare.py (#499): the OS side rule and the dataset."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import os_compare

BEFORE = "2026-09-17T23:30:00-04:00"
AFTER = "2026-09-18T08:00:00-04:00"


def row(
    started, macos=None, backend="qwen38fnq3", task="parser-date", wall=100.0, **kw
):
    env = {"servers": {backend: {"engine_version": kw.pop("engine", "abc123")}}}
    if macos:
        env["macos"] = macos
    return {
        "backend": backend,
        "task": task,
        "client": "opencode",
        "client_version": kw.pop("client_version", "1.18.31"),
        "started": started,
        "wall_seconds": wall,
        "passed": kw.pop("passed", True),
        "env": env,
        **kw,
    }


def test_a_stamped_row_uses_its_major_version():
    assert os_compare.os_side(row(BEFORE, "26.6.2")) == "26"
    assert os_compare.os_side(row(AFTER, "27.0")) == "27"


def test_an_unstamped_row_before_the_epoch_is_macos_26():
    assert os_compare.os_side(row(BEFORE)) == "26"


def test_an_unstamped_naive_time_is_new_york():
    # 07:00 naive is after the 06:53 epoch in New York. Read as UTC it would
    # be 03:00 New York, before the epoch, and wrongly placed on 26.
    assert os_compare.os_side(row("2026-09-18T07:00:00")) is None
    assert os_compare.os_side(row("2026-09-18T06:00:00")) == "26"


def test_an_unstamped_row_after_the_epoch_has_no_side():
    assert os_compare.os_side(row(AFTER)) is None


def test_a_27_stamp_before_the_epoch_is_a_conflict():
    r = row(BEFORE, "27.0")
    assert os_compare.conflict(r)
    assert os_compare.os_side(r) is None


def test_a_26_stamp_after_the_epoch_is_a_conflict():
    r = row(AFTER, "26.6.2")
    assert os_compare.conflict(r)
    assert os_compare.os_side(r) is None


def test_the_epoch_itself_is_macos_27():
    assert os_compare.os_side(row(os_compare.EPOCH_27.isoformat(), "27.0")) == "27"
    assert os_compare.os_side(row(os_compare.EPOCH_27.isoformat())) is None


def test_build_splits_a_cell_and_counts_what_it_drops():
    rows = [
        row(BEFORE, "26.6.2", wall=100.0, engine="old"),
        row(BEFORE, "26.6.2", wall=140.0, engine="old", passed=False),
        row(AFTER, "27.0", wall=90.0, engine="new"),
        row(AFTER, "26.6.2"),  # conflict
        row(AFTER),  # unplaced
        row(AFTER, "27.0", backend="other"),  # not asked for
    ]
    records, dropped = os_compare.build(rows, ("qwen38fnq3",))
    assert dropped == {"conflict": 1, "unplaced": 1}
    assert len(records) == 1
    r = records[0]
    a, b = r["macos26"]["latest"], r["macos27"]["latest"]
    assert (a["n"], a["passed"]) == (2, 1)
    assert a["median_wall_s"] == 120.0
    assert a["summed_wall_s"] == 240.0
    assert (b["n"], b["passed"]) == (1, 1)
    assert r["engine_changed"] is True
    assert r["client_changed"] is False


def test_latest_keeps_only_the_newest_engine_version_on_a_side():
    # The 2026-09-17 baseline was one row on a new llama.cpp build, and the
    # #213 argv reduction alone kept the three older rows instead. `latest`
    # must anchor on the newest build; `all` keeps the history for context.
    rows = [
        row("2026-09-07T13:00:00-04:00", "26.6.2", wall=100.0, engine="old"),
        row("2026-09-07T14:00:00-04:00", "26.6.2", wall=110.0, engine="old"),
        row("2026-09-17T23:20:00-04:00", "26.6.2", wall=50.0, engine="new"),
        row(AFTER, "27.0", wall=60.0, engine="new"),
    ]
    records, _ = os_compare.build(rows, ("qwen38fnq3",))
    side = records[0]["macos26"]
    assert side["latest"]["n"] == 1
    assert side["latest"]["engine_versions"] == ["new"]
    assert side["latest"]["median_wall_s"] == 50.0
    assert side["all"]["n"] == 3
    assert side["all"]["engine_versions"] == ["new", "old"]
    assert records[0]["engine_changed"] is False


def test_newest_is_by_start_time_not_by_ledger_order():
    rows = [
        row("2026-09-17T23:20:00-04:00", "26.6.2", engine="new"),
        row("2026-09-07T13:00:00-04:00", "26.6.2", engine="old"),
    ]
    records, _ = os_compare.build(rows, ("qwen38fnq3",))
    assert records[0]["macos26"]["latest"]["engine_versions"] == ["new"]


def test_a_cell_with_one_side_only_is_kept_with_an_empty_side():
    records, _ = os_compare.build([row(BEFORE, "26.6.2")], ("qwen38fnq3",))
    empty = {
        "n": 0,
        "passed": 0,
        "median_wall_s": None,
        "summed_wall_s": None,
        "macos": [],
        "engine_versions": [],
        "client_versions": [],
    }
    assert records[0]["macos27"] == {"latest": empty, "all": empty}


def test_other_clients_are_ignored():
    records, _ = os_compare.build(
        [row(BEFORE, "26.6.2", client="claude")], ("qwen38fnq3",)
    )
    assert records == []


def test_main_writes_the_dataset(tmp_path):
    ledger = tmp_path / "results.jsonl"
    ledger.write_text(
        "\n".join(json.dumps(r) for r in [row(BEFORE, "26.6.2"), row(AFTER, "27.0")])
        + "\n"
    )
    out = tmp_path / "dataset.json"
    rc = os_compare.main(
        ["--results", str(ledger), "--out", str(out), "--backend", "qwen38fnq3"]
    )
    assert rc == 0
    doc = json.loads(out.read_text())
    assert doc["epoch_27"] == "2026-09-18T06:53:09-04:00"
    assert doc["cells"][0]["macos27"]["latest"]["n"] == 1


def test_main_refuses_an_empty_dataset(tmp_path):
    ledger = tmp_path / "results.jsonl"
    ledger.write_text("")
    rc = os_compare.main(["--results", str(ledger), "--out", str(tmp_path / "d.json")])
    assert rc == 1
