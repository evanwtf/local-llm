"""Tests for scripts/prefill_depth.py (#158): cold vs appended prefill at depth."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import prefill_depth

HEADER = (
    "ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps,gen_first_ms,"
    "gen_steady_tokens,gen_steady_tps,kvcache_bytes,prompt_file,prompt_bytes\n"
)


def row(ctx: int, prefill: int, tps: float) -> str:
    return f"{ctx},{prefill},{tps},0,0,0,0,0,1,promessi_sposi.txt,1329139\n"


def write(path: pathlib.Path, *rows: str) -> pathlib.Path:
    path.write_text(HEADER + "".join(rows))
    return path


# --- reading ds4-bench CSVs --------------------------------------------------


def test_appended_rates_skip_the_first_frontier(tmp_path: pathlib.Path) -> None:
    # The first frontier of the sweep prefills from an empty KV: it is a cold
    # prefill of `step` tokens, not an appended one, and must not be counted.
    csv = write(
        tmp_path / "a.csv",
        row(8192, 8192, 900.0),
        row(16384, 8192, 700.0),
        row(24576, 8192, 600.0),
    )
    assert prefill_depth.appended_rates(csv, step=8192) == {16384: 700.0, 24576: 600.0}


def test_appended_rates_ignore_a_row_with_another_step(tmp_path: pathlib.Path) -> None:
    csv = write(tmp_path / "a.csv", row(8192, 8192, 900.0), row(12288, 4096, 1.0))
    assert prefill_depth.appended_rates(csv, step=8192) == {}


def test_cold_rate_reads_the_single_full_prefill_row(tmp_path: pathlib.Path) -> None:
    csv = write(tmp_path / "c.csv", row(32768, 32768, 812.5))
    assert prefill_depth.cold_rate(csv, depth=32768) == 812.5


def test_a_cold_csv_that_did_not_prefill_everything_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    # A cold run that reports fewer prefill tokens than its depth reused a
    # cache: that is an appended number wearing a cold label.
    csv = write(tmp_path / "c.csv", row(32768, 8192, 812.5))
    with pytest.raises(ValueError, match="not a cold prefill"):
        prefill_depth.cold_rate(csv, depth=32768)


# --- the plan: what each arm runs ------------------------------------------


def test_the_appended_arm_is_one_sweep_across_every_depth() -> None:
    plan = prefill_depth.bench_plan("appended", [8192, 16384, 32768], step=8192)
    assert plan == [("appended", 8192, 32768)]


def test_the_cold_arm_is_one_fresh_run_per_depth() -> None:
    plan = prefill_depth.bench_plan("cold", [8192, 16384, 32768], step=8192)
    assert plan == [
        ("cold-8192", 8192, 8192),
        ("cold-16384", 16384, 16384),
        ("cold-32768", 32768, 32768),
    ]


def test_depths_must_sit_on_the_appended_sweep() -> None:
    # A depth between frontiers has a cold number and no appended partner.
    with pytest.raises(prefill_depth.Refusal, match="multiple of"):
        prefill_depth.check_plan([16384, 20000], step=8192, reps=4)


def test_the_first_depth_must_be_past_the_first_frontier() -> None:
    # At depth == step the appended sweep has only its cold first frontier.
    with pytest.raises(prefill_depth.Refusal, match="deeper than"):
        prefill_depth.check_plan([8192], step=8192, reps=4)


def test_an_odd_rep_count_is_refused() -> None:
    with pytest.raises(prefill_depth.Refusal, match="lead"):
        prefill_depth.check_plan([16384, 32768], step=8192, reps=3)


# --- the report ------------------------------------------------------------


def lay_out(out: pathlib.Path) -> None:
    """Two reps, depths 16384 and 32768, step 8192."""
    for rep, (a16, a32, c16, c32) in enumerate(
        [(700.0, 500.0, 1000.0, 800.0), (720.0, 520.0, 1040.0, 820.0)], start=1
    ):
        write(
            out / f"appended-rep{rep}.csv",
            row(8192, 8192, 1200.0),
            row(16384, 8192, a16),
            row(24576, 8192, 600.0),
            row(32768, 8192, a32),
        )
        write(out / f"cold-16384-rep{rep}.csv", row(16384, 16384, c16))
        write(out / f"cold-32768-rep{rep}.csv", row(32768, 32768, c32))


def test_summarize_pairs_cold_and_appended_per_depth(tmp_path: pathlib.Path) -> None:
    lay_out(tmp_path)
    got = prefill_depth.summarize(tmp_path, [16384, 32768], step=8192)
    assert got[16384]["cold"] == [1000.0, 1040.0]
    assert got[16384]["appended"] == [700.0, 720.0]
    assert got[32768]["cold"] == [800.0, 820.0]
    assert got[32768]["appended"] == [500.0, 520.0]


def test_render_gives_seconds_beside_every_rate(tmp_path: pathlib.Path) -> None:
    lay_out(tmp_path)
    md = prefill_depth.render(
        prefill_depth.summarize(tmp_path, [16384, 32768], 8192), 8192
    )
    # depth 16384: cold median 1020 t/s is 16.1 s for all 16384 tokens;
    # appended median 710 t/s is 11.5 s for the last 8192. 710/1020 = 70%.
    assert "| 16384 | 1020.0 | 16.1 | 710.0 | 11.5 | 70% | 2 |" in md
    assert "| 32768 | 810.0 | 40.5 | 510.0 | 16.1 | 63% | 2 |" in md
