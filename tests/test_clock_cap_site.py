from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import clock_cap_site as site

REPO = Path(__file__).resolve().parent.parent


def write_session(d: Path, run_id: str) -> None:
    d.mkdir()
    (d / "runs.csv").write_text(
        "run_id,round,arm,start_utc,wall_s,pair_wh,inlet_f\n"
        f"{run_id},1,1900,2026-10-31T01:00:00Z,1500.0,127.5,\n"
    )
    cols = ",".join(site.SERIES_COLUMNS)
    (d / "timeseries.csv").write_text(
        f"time_utc,time_new_york,run_id,arm,phase,{cols}\n"
        "2026-10-31T01:00:00Z,x,r,1900,c1,1889,1890,70,68,35,36,4900,\n"
    )
    (d / "outlets.csv").write_text(
        "time_utc,time_new_york,run_id,arm,phase,outlet,watts\n"
        "2026-10-31T01:00:00Z,x,r,1900,c1,head,160.5\n"
        "2026-10-31T01:00:05Z,x,r,1900,c1,worker,144.0\n"
    )
    (d / "inlet.csv").write_text(
        "time_utc,time_new_york,inlet_f\n2026-10-31T00:00:00Z,x,69.1\n"
    )
    (d / "phases.csv").write_text(
        "run_id,round,arm,phase,start_utc,end_utc,start_new_york,end_new_york,wall_s\n"
        f"{run_id},1,1900,c1,2026-10-31T01:00:00Z,2026-10-31T01:10:00Z,x,y,600\n"
    )


def test_build_tags_each_run_with_its_session_and_keeps_types(tmp_path: Path) -> None:
    write_session(tmp_path / "a", "r1-1900-a")
    write_session(tmp_path / "b", "r1-1900-b")
    data = site.build([("night", tmp_path / "a"), ("day", tmp_path / "b")])
    first, second = data["sessions"]
    assert first["label"] == "night" and second["runs"][0]["session"] == "day"
    run = first["runs"][0]
    assert run["arm"] == "1900" and run["round"] == "1"  # text stays text
    assert run["pair_wh"] == 127.5 and run["inlet_f"] is None  # numbers; empty is None
    assert first["series"]["head_sm_clock_mhz"] == [1889.0]
    assert first["series"]["head_fan2_rpm"] == [None]
    assert first["outlets"]["worker"]["watts"] == [144.0]
    assert first["phases"][0]["phase"] == "c1"


def test_build_refuses_a_repeated_session_label(tmp_path: Path) -> None:
    write_session(tmp_path / "a", "r1")
    with pytest.raises(SystemExit, match="repeat"):
        site.build([("x", tmp_path / "a"), ("x", tmp_path / "a")])


def test_parse_session_needs_a_label_and_a_dir() -> None:
    assert site.parse_session("2026-10-31=docs/x") == ("2026-10-31", Path("docs/x"))
    for bad in ("docs/x", "=docs/x", "label="):
        with pytest.raises(SystemExit, match="LABEL=DIR"):
            site.parse_session(bad)


def test_the_committed_sessions_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page's real inputs, with the same arguments the Pages workflow passes."""
    monkeypatch.chdir(REPO)
    out = tmp_path / "data.json"
    site.main([
        "--session", "2026-10-30=docs/clock-cap-1319/data",
        "--session", "2026-10-31=docs/clock-cap-1319/curve/data",
        "--out", str(out),
    ])  # fmt: skip
    data = json.loads(out.read_text())
    assert [len(s["runs"]) for s in data["sessions"]] == [12, 21]
    arms = {r["arm"] for s in data["sessions"] for r in s["runs"]}
    assert arms == {"uncapped", "2500", "2200", "1900", "1800", "1700", "1600"}
