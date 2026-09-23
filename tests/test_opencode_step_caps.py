"""opencode_step_caps.py: which OpenCode steps ran to the output cap. #672"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import opencode_step_caps as osc


def _finish(output, reasoning=0):
    return json.dumps(
        {
            "type": "step_finish",
            "part": {"tokens": {"output": output, "reasoning": reasoning}},
        }
    )


def test_reads_only_step_finish_events_and_skips_junk():
    lines = [
        json.dumps({"type": "step_start"}),
        _finish(198),
        "not json",
        json.dumps({"type": "tool_use"}),
        _finish(16384, 0),
    ]
    assert osc.step_tokens(lines) == [(198, 0), (16384, 0)]


def test_a_step_at_exactly_the_cap_counts_and_one_below_does_not():
    """The value that matters is the client's limit.output, 16,384 for MiMo."""
    steps = [(16384, 0), (16383, 0), (200, 0)]
    assert osc.capped(steps, 16384) == [(16384, 0)]


def test_output_plus_reasoning_reaching_the_cap_counts():
    """Engines differ in whether the limit covers reasoning tokens."""
    assert osc.capped([(10000, 6384)], 16384) == [(10000, 6384)]
    assert osc.capped([(10000, 6383)], 16384) == []


def test_main_summarises_trials_and_steps(tmp_path, capsys):
    a = tmp_path / "a.stdout.jsonl"
    b = tmp_path / "b.stdout.jsonl"
    a.write_text("\n".join([_finish(16384), _finish(16384), _finish(50)]) + "\n")
    b.write_text(_finish(120) + "\n")
    assert osc.main(["--cap", "16384", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert (
        "1 of 2 trials have at least one step at the 16384-token cap; 2 such steps"
        in out
    )
    assert "a.stdout.jsonl\tsteps=3\tcapped=2\tmax_step_tokens=16384" in out
