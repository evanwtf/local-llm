"""#367: every decode A/B driver writes sweep-order.txt that sensor_windows reads.

`run-order.txt` records order and no times, so `sensor_windows.py` rejected
every line and the thermal trace of an A/B had to be rebuilt by hand from the
driver's stdout.
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import ab_driver
import sensor_windows

DRIVERS = (
    "decode_ab.py",
    "decode_ab_engine.py",
    "decode_ab_stack.py",
    "metal_knob_ab.py",
    "prefill_chunk_ab.py",
)


def test_a_window_round_trips_through_sensor_windows(tmp_path):
    with ab_driver.sweep_window(tmp_path, "on-rep1"):
        pass
    windows = sensor_windows.read_windows(
        tmp_path / "sweep-order.txt", dt.datetime.now().astimezone().date()
    )
    assert [w[0] for w in windows] == ["on-rep1"]
    assert windows[0][1] <= windows[0][2]


def test_a_failed_arm_still_gets_its_window(tmp_path):
    """The thermal trace matters most for the arm that died."""
    with pytest.raises(RuntimeError), ab_driver.sweep_window(tmp_path, "off-rep2"):
        raise RuntimeError("ds4-bench exited 1")
    line = (tmp_path / "sweep-order.txt").read_text().split()
    assert line[0] == "off-rep2"
    assert len(line) == 3


def test_windows_append_in_run_order(tmp_path):
    for tag in ("a-rep1", "b-rep1", "b-rep2", "a-rep2"):
        with ab_driver.sweep_window(tmp_path, tag):
            pass
    lines = (tmp_path / "sweep-order.txt").read_text().splitlines()
    assert [ln.split()[0] for ln in lines] == ["a-rep1", "b-rep1", "b-rep2", "a-rep2"]


@pytest.mark.parametrize("name", DRIVERS)
def test_each_driver_records_its_arms(name):
    tree = ast.parse((REPO / "scripts" / name).read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.With)
        and any(
            ast.unparse(item.context_expr.func) == "ab_driver.sweep_window"
            for item in n.items
            if isinstance(item.context_expr, ast.Call)
        )
    ]
    assert calls, f"{name} runs its arms without ab_driver.sweep_window"
