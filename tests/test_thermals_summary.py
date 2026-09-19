"""The envelope a thermals log reduces to (#561).

An idle sample counted as load pulls the median toward the idle floor, and a
truncated last line must not crash the read-out of a run that already
happened.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import thermals_summary as tsum


def line(temp: float, power: float, util: int, clock: int = 1860, t: str = "") -> str:
    body = {
        "gpu0_temp_c": temp,
        "gpu0_power_w": power,
        "gpu0_clock_mhz": clock,
        "gpu0_util_pct": util,
        "utc": t,
    }
    return "2026-09-19T15:17:15+0000 INFO [abc@box none pld=n/a] " + json.dumps(body)


def test_it_reads_the_json_after_any_log_prefix() -> None:
    samples = tsum.parse_samples([line(70, 390, 95), "no json here"])
    assert len(samples) == 1
    assert samples[0]["gpu0_power_w"] == 390


def test_a_truncated_last_line_is_skipped_not_fatal() -> None:
    whole = line(70, 390, 95)
    samples = tsum.parse_samples([whole, whole[:-10]])
    assert len(samples) == 1


def test_idle_samples_do_not_pull_the_busy_median_down() -> None:
    samples = tsum.parse_samples(
        [line(75, 395, 95), line(74, 390, 92), line(60, 20, 0), line(60, 20, 0)]
    )
    env = tsum.envelope(samples)
    assert env.samples == 4
    assert env.busy_samples == 2
    assert env.power_median_busy_w == pytest.approx(392.5)
    assert env.temp_max_c == 75


def test_the_peak_counts_every_sample_busy_or_not() -> None:
    samples = tsum.parse_samples([line(80, 120, 10), line(70, 390, 95)])
    env = tsum.envelope(samples)
    assert env.temp_max_c == 80
    assert env.power_max_w == 390


def test_no_busy_samples_reports_none_rather_than_zero() -> None:
    env = tsum.envelope(tsum.parse_samples([line(40, 20, 0)]))
    assert env.power_median_busy_w is None
    assert "n/a" in tsum.describe(env)


def test_an_empty_log_is_an_error() -> None:
    with pytest.raises(ValueError):
        tsum.envelope([])


def test_describe_quotes_clocks_in_ghz_to_one_decimal() -> None:
    env = tsum.envelope(tsum.parse_samples([line(70, 390, 95, clock=1845)]))
    assert "1.8 GHz" in tsum.describe(env)


def test_a_window_keeps_only_samples_inside_it_compared_as_times() -> None:
    samples = tsum.parse_samples(
        [
            line(70, 390, 95, t="2026-09-19T15:59:59Z"),
            line(71, 391, 95, t="2026-09-19T16:00:00Z"),
            line(72, 392, 95, t="2026-09-19T16:06:06Z"),
            line(73, 393, 95, t="2026-09-19T16:08:13Z"),
        ]
    )
    # The bound carries an offset and the samples carry Z: a string compare
    # would get this wrong, a parsed compare does not.
    kept = tsum.within(samples, "2026-09-19T12:00:00-04:00", "2026-09-19T16:06:06Z")
    assert [s["gpu0_temp_c"] for s in kept] == [71, 72]


def test_no_window_keeps_everything_even_without_timestamps() -> None:
    samples = tsum.parse_samples([line(70, 390, 95)])
    assert tsum.within(samples) == samples


def test_main_exits_nonzero_on_a_log_with_no_samples(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "empty.log"
    log.write_text("nothing\n")
    assert tsum.main([str(log)]) == 1
