"""The #276 collation: one row per (phase, rep, ctx), nothing implied.

This table is what a write-up or a later question will be recomputed from, so
a defect here is a defect in every number derived from it afterwards -- and it
will not crash, it will produce a plausible table.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import fan_ab_collate as fac


def when(hhmm: str) -> dt.datetime:
    return dt.datetime.strptime(f"2026-09-09T{hhmm}-0400", "%Y-%m-%dT%H:%M:%S%z")


SERIES = [(when("20:00:00"), 21.0), (when("20:01:00"), 22.0)]


def test_ambient_is_interpolated_between_the_bracketing_samples() -> None:
    """Halfway between 21 and 22 is 21.5, not the nearer of the two."""
    value, bracket, gap = fac.ambient_at(SERIES, when("20:00:30"))
    assert value == 21.5
    assert bracket == "20:00:00/20:01:00"
    assert gap == 60


def test_an_exact_hit_returns_that_sample() -> None:
    assert fac.ambient_at(SERIES, when("20:01:00"))[0] == 22.0


def test_outside_the_series_clamps_and_reports_no_gap() -> None:
    """Extrapolating past the ends would invent a trend from one sample.

    `gap is None` marks the value as an edge clamp rather than an
    interpolation, so a reader can tell the two apart.
    """
    value, _, gap = fac.ambient_at(SERIES, when("19:00:00"))
    assert value == 21.0
    assert gap is None
    assert fac.ambient_at(SERIES, when("23:00:00"))[0] == 22.0


def test_no_ambient_is_none_not_zero() -> None:
    """An absent room temperature must never read as 0 C."""
    assert fac.ambient_at([], when("20:00:00")) == (None, None, None)


def test_a_bad_timestamp_is_none_rather_than_an_exception() -> None:
    assert fac.parse_iso("not a time") is None
    assert fac.parse_iso(None) is None
    assert fac.parse_iso(12345) is None
    assert fac.parse_iso("2026-09-09T20:00:00-0400") is not None


def test_every_declared_field_is_written_for_every_row(tmp_path) -> None:
    """A row must be self-contained: no column silently absent.

    `csv.DictWriter` raises on an unexpected key but is happy to leave a
    declared one blank, so the guard has to be that the row dict covers the
    schema exactly.
    """
    outdir = tmp_path
    (outdir / "01-auto-rep1.csv").write_text(
        "ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps,gen_first_ms,"
        "gen_steady_tokens,gen_steady_tps,kvcache_bytes\n"
        "2048,2048,800.0,128,45.0,23.7,127,45.1,52184460\n"
    )
    manifest = {
        "tree_sha": "abcdef1234567890",
        "gguf": "x.gguf",
        "started_iso": "2026-09-09T19:46:50-0400",
        "arms": ["auto", "max"],
        "phases": [
            {
                "phase": 1,
                "condition": "auto",
                "start_die_c": 30.93,
                "cooldown": {
                    "outcome": "plateau",
                    "waited_s": 251,
                    "last_slope_c_per_min": -0.3,
                    "cooled_on": "max",
                    "settle_in_s": 0,
                },
                "reps": [
                    {
                        "rep": 1,
                        "started_iso": "2026-09-09T20:00:00-0400",
                        "ended_iso": "2026-09-09T20:00:51-0400",
                        "die_c_after": 62.6,
                        "csv": "01-auto-rep1.csv",
                    }
                ],
            }
        ],
    }
    rows = list(fac.rows_for(outdir, manifest, SERIES, []))
    assert len(rows) == 1
    assert set(rows[0]) == set(fac.FIELDS), "row keys must match the schema exactly"
    assert rows[0]["rep_seconds"] == 51
    assert rows[0]["segment"] == 1
    assert rows[0]["condition"] == "auto"
    assert rows[0]["tree_sha"] == "abcdef123456", "sha is shortened, not dropped"


def test_a_missing_rep_csv_drops_that_rep_and_not_the_run(tmp_path) -> None:
    """A rep whose CSV never landed contributes nothing, silently to the table
    and loudly to the log -- it must not abort the other seventeen."""
    manifest = {
        "arms": ["auto", "max"],
        "phases": [
            {
                "phase": 1,
                "condition": "auto",
                "cooldown": {},
                "reps": [
                    {
                        "rep": 1,
                        "csv": "gone.csv",
                        "started_iso": "2026-09-09T20:00:00-0400",
                        "ended_iso": "2026-09-09T20:00:51-0400",
                    }
                ],
            }
        ],
    }
    assert list(fac.rows_for(tmp_path, manifest, SERIES, [])) == []


def test_the_real_run_collated_cleanly() -> None:
    """The committed table for #276: 144 rows, no blanks, both arms present.

    Pinned because this file is the evidence for a published result. If a
    later change to the collation empties a column, that must fail here rather
    than surface in a write-up.
    """
    path = ROOT / "benchmarks/ds4/fan-ab-276/rows.csv"
    if not path.exists():
        import pytest

        pytest.skip("the #276 run output is not present")
    import csv as _csv

    rows = list(_csv.DictReader(path.open()))
    assert len(rows) == 144, "6 phases x 3 reps x 8 frontiers"
    assert set(fac.FIELDS) == set(rows[0])
    assert not [v for row in rows for v in row.values() if v == ""]
    assert {row["condition"] for row in rows} == {"auto", "max"}
    assert {row["cooldown_outcome"] for row in rows} == {"plateau"}
