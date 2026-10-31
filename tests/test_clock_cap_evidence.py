from __future__ import annotations

import csv
import datetime
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import clock_cap_evidence as ev

UTC = datetime.UTC


def t(s: str) -> datetime.datetime:
    return ev.parse_utc(s)


def run(arm: str = "1900", start: str = "2026-10-31T01:00:00Z") -> dict[str, Any]:
    return {
        "run_id": f"r1-{arm}-1",
        "round": 1,
        "arm": arm,
        "start_utc": start,
        "end_utc": "2026-10-31T01:25:00Z",
        "start_gpu_temps_c": [52, 47],
        "phases": [
            {"phase": "prefill", "wall_s": 240.0, "prompt_tps": 1850.0, "output_tps": 0.1},
            {"phase": "c1", "wall_s": 600.0, "prompt_tps": 4.0, "output_tps": 85.0},
            {"phase": "c2", "wall_s": 659.0, "prompt_tps": 5.0, "output_tps": 109.0},
        ],
    }  # fmt: skip


def test_phases_run_back_to_back_and_the_last_ends_at_the_run_end() -> None:
    w = ev.phase_windows([run()])
    assert [p["phase"] for p in w] == ["prefill", "c1", "c2"]
    assert w[0]["start"] == t("2026-10-31T01:00:00Z")
    assert w[1]["start"] == t("2026-10-31T01:04:00Z")  # +240 s
    assert w[2]["start"] == t("2026-10-31T01:14:00Z")  # +600 s
    assert w[2]["end"] == t("2026-10-31T01:25:00Z")  # end_utc, not +659 s


def test_label_at_names_the_phase_or_between() -> None:
    w = ev.phase_windows([run()])
    assert ev.label_at(t("2026-10-31T01:05:00Z"), w) == ("r1-1900-1", "1900", "c1")
    assert ev.label_at(t("2026-10-31T01:25:00Z"), w) == ("", "", "between")
    assert ev.label_at(t("2026-10-31T00:59:59Z"), w) == ("", "", "between")


def test_ny_text_carries_the_offset() -> None:
    assert ev.ny_text(t("2026-10-31T01:09:07Z")) == "2026-10-30T21:09:07-0400"
    assert ev.ny_text(t("2026-11-02T01:09:07Z")) == "2026-11-01T20:09:07-0500"


def test_chunks_cover_the_range_without_gaps() -> None:
    assert ev.chunks(0, 3100, 1500) == [(0, 1500), (1500, 3000), (3000, 3100)]
    assert ev.chunks(0, 1500, 1500) == [(0, 1500)]


def test_matrix_points_and_failures() -> None:
    good = json.dumps(
        {"data": {"result": [{"values": [[1793408940, "2411"], [1793408955, "2509"]]}]}}
    )
    assert ev.matrix_points(good) == [(1793408940, 2411.0), (1793408955, 2509.0)]
    assert ev.matrix_points("") == []
    assert ev.matrix_points('{"data": {"result": []}}') == []


def test_flux_points_reads_time_and_value() -> None:
    table = json.dumps(
        {"columns": ["Time", "Value"], "rows": [["2026-10-31T01:09:04Z", 68.8]]}
    )
    assert ev.flux_points(table) == [(t("2026-10-31T01:09:04Z"), 68.8)]
    assert ev.flux_points('{"type": "gcx.error"}') == []


def test_grid_leaves_a_missing_step_empty() -> None:
    rows = ev.grid({"a": [(0, 1.0), (30, 3.0)]}, 0, 30, 15)
    assert [(r["ts"], r["a"]) for r in rows] == [(0, 1.0), (15, None), (30, 3.0)]


def test_energy_is_watts_times_hours() -> None:
    start, end = t("2026-10-31T01:00:00Z"), t("2026-10-31T01:30:00Z")
    flat = [(start + datetime.timedelta(seconds=s), 400.0) for s in range(0, 1801, 5)]
    assert ev.energy_wh(flat, start, end) == pytest.approx(200.0)  # 400 W for 0.5 h
    ramp = [(start, 0.0), (end, 360.0)]
    assert ev.energy_wh(ramp, start, end) == pytest.approx(90.0)  # mean 180 W, 0.5 h
    assert ev.energy_wh(ramp[:1], start, end) is None


def test_energy_holds_the_edge_readings_to_the_window() -> None:
    start, end = t("2026-10-31T01:00:00Z"), t("2026-10-31T02:00:00Z")
    pts = [(t("2026-10-31T01:10:00Z"), 100.0), (t("2026-10-31T01:50:00Z"), 100.0)]
    assert ev.energy_wh(pts, start, end) == pytest.approx(100.0)


def test_held_value_is_the_last_reading_before() -> None:
    pts = [(t("2026-10-31T00:00:00Z"), 66.0), (t("2026-10-31T01:00:00Z"), 67.5)]
    assert ev.held_value(pts, t("2026-10-31T00:59:59Z")) == 66.0
    assert ev.held_value(pts, t("2026-10-31T01:00:00Z")) == 67.5
    assert ev.held_value(pts, t("2026-10-30T23:00:00Z")) is None


def series_rows() -> list[dict[str, str]]:
    rows = []
    for i, s in enumerate(range(0, 1501, 15)):
        when = t("2026-10-31T01:00:00Z") + datetime.timedelta(seconds=s)
        rows.append(
            {"time_utc": ev.utc_text(when), "head_sm_clock_mhz": "1889",
             "worker_sm_clock_mhz": "1890", "head_gpu_temp_c": str(60 + i % 11),
             "worker_gpu_temp_c": "65", "head_gpu_power_w": "35",
             "worker_gpu_power_w": "36", "head_fan1_rpm": "4860",
             "head_fan2_rpm": "7290"}
        )  # fmt: skip
    return rows


def test_summarize_builds_a_run_from_the_raw_series() -> None:
    start = t("2026-10-31T01:00:00Z")
    plugs = {
        "head": [(start + datetime.timedelta(seconds=s), 160.0) for s in range(0, 1501, 5)],
        "worker": [(start + datetime.timedelta(seconds=s), 143.0) for s in range(0, 1501, 5)],
    }  # fmt: skip
    inlet = [(t("2026-10-30T23:40:00Z"), 69.1)]
    (rec,) = ev.summarize([run()], series_rows(), plugs, inlet)
    assert rec["wall_s"] == 1500.0
    assert rec["prefill_tps"] == 1850.0 and rec["c2_tps"] == 109.0
    assert rec["head_sm_clock_avg"] == 1889.0
    assert rec["head_gpu_temp_max"] == 70.0
    assert rec["pair_outlet_w_avg"] == 303.0
    assert rec["pair_wh"] == pytest.approx(303.0 * 1500 / 3600)  # 126.25 Wh
    assert rec["inlet_f"] == 69.1
    assert rec["start_new_york"] == "2026-10-30T21:00:00-0400"


def test_export_writes_every_file_and_refuses_a_missing_series(tmp_path: Path) -> None:
    rows = [run()]

    def prom(sel: str, a: int, b: int, step: int) -> str:
        values = [[ts, "1889"] for ts in range(a, b + 1, step)]
        return json.dumps({"data": {"result": [{"values": values}]}})

    def flux(q: str) -> str:
        start = q.split("range(start: ")[1].split(",")[0]
        return json.dumps({"columns": ["Time", "Value"], "rows": [[start, 150.0]]})

    counts = ev.export(rows, tmp_path, prom, flux)
    assert set(counts) == {"timeseries.csv", "outlets.csv", "inlet.csv", "phases.csv"}
    with (tmp_path / "timeseries.csv").open() as fh:
        ts = list(csv.DictReader(fh))
    # 10 min of padding each side of a 25 min run, at 15 s: 181 rows.
    assert len(ts) == counts["timeseries.csv"] == 181
    assert all(r["head_sm_clock_mhz"] == "1889.0" for r in ts)
    assert {r["phase"] for r in ts} == {"between", "prefill", "c1", "c2"}
    assert counts["phases.csv"] == 3

    def empty(sel: str, a: int, b: int, step: int) -> str:
        return ""

    with pytest.raises(SystemExit, match="no data"):
        ev.export(rows, tmp_path / "x", empty, flux)


def test_tables_give_mean_range_and_share_of_uncapped() -> None:
    def rec(arm: str, wh: float) -> dict[str, Any]:
        r: dict[str, Any] = {c: "" for c in ev.SUMMARY_COLUMNS}
        r.update(arm=arm, round=1, pair_wh=wh)
        return r

    recs = [rec("uncapped", 170.0), rec("uncapped", 174.0), rec("1900", 127.0)]
    text = ev.render_tables(recs)
    assert text.startswith("| measure | uncapped | 1900 |\n")  # arms from the runs
    line = next(ln for ln in text.splitlines() if ln.startswith("| energy per run"))
    assert "172.0 (170.0–174.0)" in line
    assert "127.0 (127.0–127.0), 73.8%" in line  # 127 / 172
    named = ev.render_tables(recs, ["uncapped", "2500", "1900"])
    line = next(ln for ln in named.splitlines() if ln.startswith("| energy per run"))
    assert "| n/a |" in line  # an arm named but never run


def test_arm_order_is_uncapped_then_highest_clock_first() -> None:
    assert ev.arm_order(["1700", "uncapped", "1900", "1600", "1900"]) == [
        "uncapped", "1900", "1700", "1600"
    ]  # fmt: skip
    assert ev.arm_order(["900", "1000"]) == ["1000", "900"]  # numeric, not text
    assert ev.arm_label("1900") == "1,900 MHz"
    assert ev.arm_label("uncapped") == "uncapped"


def test_arm_colors_follow_the_clock_and_refuse_an_eighth_arm() -> None:
    colors = ev.arm_colors(["1600", "uncapped", "1900"])
    assert colors == {"uncapped": "#0b2f5c", "1900": "#104281", "1600": "#1d5aa8"}
    assert (
        len(ev.arm_colors(["uncapped", "2500", "2200", "1900", "1800", "1700", "1600"]))
        == 7
    )
    with pytest.raises(SystemExit, match="8 arms"):
        ev.arm_colors(["uncapped", *[str(1000 + 100 * i) for i in range(7)]])


def test_curve_steps_price_each_step_in_wh_per_added_second() -> None:
    def rec(arm: str, wall: float, wh: float) -> dict[str, Any]:
        return {"arm": arm, "wall_s": wall, "pair_wh": wh}

    recs = [
        rec("uncapped", 1450.0, 168.0), rec("uncapped", 1460.0, 166.0),
        rec("1900", 1515.0, 128.0),
        rec("1800", 1545.0, 126.0),
        rec("1700", 1545.0, 127.0),  # no time added: no rate, not a division error
    ]  # fmt: skip
    steps = ev.curve_steps(recs, ["uncapped", "1900", "1800", "1700"])
    assert [(s["from"], s["to"]) for s in steps] == [
        ("uncapped", "1900"), ("1900", "1800"), ("1800", "1700")
    ]  # fmt: skip
    assert steps[0]["added_s"] == pytest.approx(60.0)  # mean 1455 -> 1515
    assert steps[0]["saved_wh"] == pytest.approx(39.0)  # mean 167 -> 128
    assert steps[0]["wh_per_s"] == pytest.approx(0.65)
    assert steps[1]["wh_per_s"] == pytest.approx(2.0 / 30.0)
    assert steps[2]["saved_wh"] == pytest.approx(-1.0)  # a step that costs energy
    assert steps[2]["wh_per_s"] is None


def test_curve_steps_skip_an_arm_without_energy() -> None:
    recs = [
        {"arm": "uncapped", "wall_s": 1450.0, "pair_wh": 168.0},
        {"arm": "1900", "wall_s": 1515.0, "pair_wh": ""},  # an outlet was down
        {"arm": "1800", "wall_s": 1545.0, "pair_wh": 126.0},
    ]
    (step,) = ev.curve_steps(recs, ["uncapped", "1900", "1800"])
    assert (step["from"], step["to"]) == ("uncapped", "1800")


def test_fan_speed_snaps_to_the_controller_step() -> None:
    assert ev.fan_speed(7290) == 7300
    assert ev.fan_speed(9400) == 10000  # a sample caught mid-ramp
    assert ev.fan_speed(4500) == 4200
    assert ev.fan_speed(12000) == 10000


def test_fan_seconds_split_the_run_by_share_of_samples() -> None:
    secs = ev.fan_seconds([10000.0, 10000.0, 7300.0, 7290.0], 1500.0)
    assert secs == {4200: 0.0, 5400: 0.0, 7300: 750.0, 10000: 750.0}
    assert sum(ev.fan_seconds([5400.0] * 7, 1234.5).values()) == pytest.approx(1234.5)
    assert ev.fan_seconds([], 1500.0) == {4200: 0.0, 5400: 0.0, 7300: 0.0, 10000: 0.0}


def test_summarize_puts_the_whole_run_at_one_fan_speed() -> None:
    start = t("2026-10-31T01:00:00Z")
    plugs = {
        n: [(start + datetime.timedelta(seconds=s), 150.0) for s in range(0, 1501, 5)]
        for n in ("head", "worker")
    }
    (rec,) = ev.summarize([run()], series_rows(), plugs, [])
    assert rec["head_fan2_s_at_7300"] == 1500.0  # fan2 reads 7,290 throughout
    assert rec["head_fan2_s_at_10000"] == 0.0


def test_break_gaps_splits_a_line_across_a_long_pause() -> None:
    a, b = t("2026-10-31T01:00:00Z"), t("2026-10-31T01:00:15Z")
    c = t("2026-10-31T09:00:00Z")  # the next session, 8 h later
    out = ev.break_gaps([(a, 1.0), (b, 2.0), (c, 3.0)])
    assert [v for _, v in out] == [1.0, 2.0, None, 3.0]
    assert a < out[2][0] < c
    assert ev.break_gaps([(a, 1.0), (b, 2.0)]) == [(a, 1.0), (b, 2.0)]
    assert ev.break_gaps([]) == []


def write_session(d: Path, run_id: str, start: str, inlet: str) -> None:
    d.mkdir()
    (d / "timeseries.csv").write_text(f"time_utc,v\n{start},1\n")
    (d / "outlets.csv").write_text(f"time_utc,watts\n{start},100\n")
    (d / "inlet.csv").write_text(f"time_utc,inlet_f\n{inlet},68.0\n")
    (d / "phases.csv").write_text(f"run_id,start_utc\n{run_id},{start}\n")
    rec: dict[str, Any] = {c: "" for c in ev.SUMMARY_COLUMNS}
    rec.update(run_id=run_id, round=1, arm="1900", start_utc=start, pair_wh=127.0)
    with (d / "runs.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ev.SUMMARY_COLUMNS)
        w.writeheader()
        w.writerow(rec)


def test_combine_merges_sessions_in_time_order(tmp_path: Path) -> None:
    late, early = tmp_path / "late", tmp_path / "early"
    # The same inlet reading appears in both sessions' look-back windows.
    write_session(late, "r1-1900-b", "2026-10-31T12:00:00Z", "2026-10-31T00:00:00Z")
    write_session(early, "r1-1900-a", "2026-10-31T01:00:00Z", "2026-10-31T00:00:00Z")
    counts = ev.combine([late, early], tmp_path / "all")
    assert counts["runs.csv"] == 2 and counts["inlet.csv"] == 1  # kept once
    runs = list(csv.DictReader((tmp_path / "all" / "runs.csv").open()))
    assert [r["run_id"] for r in runs] == ["r1-1900-a", "r1-1900-b"]  # by time
    assert "127.0 (127.0–127.0)" in (tmp_path / "all" / "tables.md").read_text()


def test_combine_refuses_sessions_with_different_columns(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    write_session(a, "r1", "2026-10-31T01:00:00Z", "2026-10-31T00:00:00Z")
    write_session(b, "r2", "2026-10-31T12:00:00Z", "2026-10-31T11:00:00Z")
    (b / "inlet.csv").write_text("time_utc,inlet_c\n2026-10-31T11:00:00Z,20\n")
    with pytest.raises(SystemExit, match="header differs"):
        ev.combine([a, b], tmp_path / "all")
