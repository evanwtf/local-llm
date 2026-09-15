"""The backfill converts each ledger in the zone its machine wrote (#209).

One zone for every ledger was the defect this pins: the Ryzen desktop wrote
UTC, so reading its naive rows as New York moves 224 values four hours with no
error. These tests cover the zone choice, the refusals, and the file scope.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_iso8601 as bf

M5 = pathlib.PurePosixPath(
    "hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/results.jsonl"
)
DGX = pathlib.PurePosixPath("hardware/Cortex-X925-128GB-GB10/results.jsonl")
RYZEN = pathlib.PurePosixPath("hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/results.jsonl")


def test_a_naive_m5_value_is_read_as_new_york():
    zone = bf.zone_for(M5)
    assert bf.convert("2026-09-06T02:46:35", zone) == "2026-09-06T02:46:35-0400"


def test_a_naive_dgx_value_is_read_as_new_york():
    assert bf.zone_for(DGX).key == "America/New_York"


def test_a_naive_ryzen_value_is_read_as_utc_not_new_york():
    zone = bf.zone_for(RYZEN)
    got = bf.convert("2026-09-02T04:09:47", zone)
    assert got == "2026-09-02T04:09:47+0000"
    assert not got.endswith("-0400")


def test_an_unknown_hardware_directory_is_refused():
    with pytest.raises(ValueError, match="LEDGER_ZONES"):
        bf.zone_for(pathlib.PurePosixPath("hardware/New-Box/results.jsonl"))


def test_a_file_outside_hardware_is_read_as_new_york():
    rel = pathlib.PurePosixPath("benchmarks/agent/results-112-strip-ab.jsonl")
    assert bf.zone_for(rel).key == "America/New_York"


def test_a_utc_z_value_moves_to_the_ledger_zone():
    assert bf.convert("2026-09-06T21:16:48Z", bf.ZONE) == "2026-09-06T17:16:48-0400"
    assert bf.convert("2026-09-06T21:16:48Z", bf.UTC) == "2026-09-06T21:16:48+0000"


def test_a_colon_offset_keeps_its_instant():
    assert bf.convert("2026-09-06T07:43:45-04:00", bf.UTC) == "2026-09-06T07:43:45-0400"


def test_a_canonical_value_is_unchanged():
    assert bf.convert("2026-09-12T01:02:21+0000", bf.ZONE) == "2026-09-12T01:02:21+0000"


def test_the_repeated_fall_back_hour_is_refused_in_new_york():
    with pytest.raises(ValueError, match="ambiguous"):
        bf.convert("2026-11-01T01:30:00", bf.ZONE)


def test_the_skipped_spring_forward_hour_is_refused_in_new_york():
    with pytest.raises(ValueError, match="does not exist"):
        bf.convert("2026-03-08T02:30:00", bf.ZONE)


def test_the_new_york_fall_back_hour_is_unambiguous_in_utc():
    assert bf.convert("2026-11-01T01:30:00", bf.UTC) == "2026-11-01T01:30:00+0000"


def test_an_unrecognized_shape_is_refused():
    with pytest.raises(ValueError, match="unrecognized"):
        bf.convert("2026-09-06 07:43", bf.ZONE)


def test_walk_converts_the_env_mtime_fields_and_counts_them():
    import collections

    counts: collections.Counter[str] = collections.Counter()
    row = {
        "started": "2026-09-06T02:46:35-0400",
        "model": "2026-09-06T02:46:35",  # looks like a time, is not a time field
        "env": {
            "gguf_mtime": "2026-09-02T18:58:50",
            "ds4_server_mtime": "2026-08-16T06:53:08",
            "llamacpp_server_mtime": "2026-09-12T23:53:18",
        },
    }
    out = bf.walk(row, bf.ZONE, counts=counts)
    assert out["env"] == {
        "gguf_mtime": "2026-09-02T18:58:50-0400",
        "ds4_server_mtime": "2026-08-16T06:53:08-0400",
        "llamacpp_server_mtime": "2026-09-12T23:53:18-0400",
    }
    assert out["model"] == "2026-09-06T02:46:35"
    assert out["started"] == row["started"]
    assert counts == {
        "gguf_mtime": 1,
        "ds4_server_mtime": 1,
        "llamacpp_server_mtime": 1,
    }


def _globbed() -> set[str]:
    return {str(p.relative_to(ROOT)) for g in bf.DATA_GLOBS for p in ROOT.glob(g)}


def test_every_live_hardware_ledger_is_in_scope_and_has_a_zone():
    ledgers = {str(p.relative_to(ROOT)) for p in ROOT.glob("hardware/*/results.jsonl")}
    assert ledgers, "no hardware ledgers found"
    assert ledgers <= _globbed()
    for rel in ledgers:
        bf.zone_for(pathlib.PurePosixPath(rel))


def test_the_archive_and_the_smoke_ledger_are_out_of_scope():
    globbed = _globbed()
    assert not any(p.startswith("docs/archive/") for p in globbed)
    assert not any(p.endswith("results-smoke.jsonl") for p in globbed)
