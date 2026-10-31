from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import clock_cap_ab as cc


def test_schedule_rotates_so_each_arm_takes_each_position() -> None:
    arms = ["uncapped", "2500", "2200", "1900"]
    plan = cc.schedule(arms, 4)
    assert plan[:4] == [(1, "uncapped"), (1, "2500"), (1, "2200"), (1, "1900")]
    assert plan[4:8] == [(2, "2500"), (2, "2200"), (2, "1900"), (2, "uncapped")]
    for pos in range(4):
        assert {plan[r * 4 + pos][1] for r in range(4)} == set(arms)


def test_schedule_runs_every_arm_once_per_round() -> None:
    plan = cc.schedule(["a", "b", "c"], 3)
    assert len(plan) == 9
    for r in (1, 2, 3):
        assert sorted(a for rr, a in plan if rr == r) == ["a", "b", "c"]


def test_clock_command_cap_and_reset() -> None:
    assert cc.clock_command("2200") == [
        "sudo",
        "-n",
        "/usr/bin/nvidia-smi",
        "-lgc",
        "0,2200",
    ]
    assert cc.clock_command("uncapped") == ["sudo", "-n", "/usr/bin/nvidia-smi", "-rgc"]


@pytest.mark.parametrize("bad", ["0", "200", "9000", "fast"])
def test_clock_command_refuses_a_bad_arm(bad: str) -> None:
    with pytest.raises(ValueError):
        cc.clock_command(bad)


def test_prompts_differ_per_run_so_the_prefix_cache_cannot_serve_them() -> None:
    a = cc.long_prompt("r1-2200-1", 0, 3)
    b = cc.long_prompt("r1-2200-2", 0, 3)
    assert a != b
    assert a[:20] != b[:20]  # the difference is at the start, not the end
    assert cc.decode_prompt("x", "c1", 0) != cc.decode_prompt("x", "c1", 1)


def test_cool_down_waits_for_every_gpu() -> None:
    temps = iter([50, 41, 44, 40])  # first poll: head hot; second: both cool
    readings: list[int] = []

    def fake(cmd: list[str]) -> str:
        value = next(temps)
        readings.append(value)
        return f"{value}\n"

    now = [0.0]

    def sleep(s: float) -> None:
        now[0] += s

    out = cc.cool_down(
        [fake, fake], [45, 42], min_s=10, max_s=600, sleep=sleep, clock=lambda: now[0]
    )
    assert out == [44, 40]
    assert now[0] == 25  # min 10 s, then one 15 s poll


def test_cool_down_gives_up_at_max() -> None:
    now = [0.0]

    def sleep(s: float) -> None:
        now[0] += s

    out = cc.cool_down(
        [lambda cmd: "60\n"],
        [40],
        min_s=10,
        max_s=40,
        sleep=sleep,
        clock=lambda: now[0],
    )
    assert out == [60]
    assert now[0] >= 40
