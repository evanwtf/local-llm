"""The M5 Max drivers pass `--targets legacy` explicitly (#146).

`run.py --targets` defaults to `sandbox` since 2026-11-07. The operator chose
to keep the M5 Max on `legacy` until its next fresh series, so its tables do not
mix the two layouts. Every driver here serves a Metal engine (ds4-metal or
mlx-serve), so it runs only on the M5 Max, and the flag is unconditional.

A driver that omits the flag now runs `sandbox` without a word. This test
builds each driver's `run.py` command line and reads the value back.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(ROOT / sub))

import batch as batchlib
import degeneration_cascade_run
import disk_kv_mechanism
import greedy_mtp_ab
import macos_settled_rerun
import mtp_treatment_gate
import restart_between_trials
import route_agent_ab
import stack_agent_ab
import stack_arm
import strip_toggle_ab

HEAD = "0123456789abcdef0123456789abcdef01234567"
P = pathlib.Path("/t")


def _targets(argv: list[str]) -> str:
    """The value run.py reads: argparse keeps the last `--targets`."""
    at = len(argv) - 1 - argv[::-1].index("--targets")
    return argv[at + 1]


def _stack_arm(run_flags: str = "") -> stack_arm.Arm:
    return stack_arm.Arm(
        name="new",
        backend="qwen38fnds4kimat",
        engine=stack_arm.DS4,
        tree=P / "tree",
        gguf=P / "model.gguf",
        ple=P / "ple.gguf",
        kv=P / "kv",
        flags="",
        run_flags=run_flags,
        mlx_model=None,
        mlx_port=stack_arm.MLX_PORT,
        mlx_bin=stack_arm.MLX_SERVE,
    )


def _strip_toggle_argv() -> list[str]:
    b = batchlib.Batch(
        repo=P,
        results=P / "r.jsonl",
        manifest=P / "m.jsonl",
        logdir=P,
        bench_logs=P,
        batch="b",
        harness_head=HEAD,
        backend="qwen38fnds4shim",
        prefix="112-strip",
    )
    return batchlib.argv(b, strip_toggle_ab.RUN_FLAGS)


def _every_m5max_run_argv() -> dict[str, list[str]]:
    return {
        "degeneration_cascade_run": degeneration_cascade_run.run_argv(
            P, "b", 1, P / "s.log"
        ),
        "disk_kv_mechanism": disk_kv_mechanism.run_argv(1),
        "greedy_mtp_ab": greedy_mtp_ab.arm_argv("bk", "t", P, "b", 1),
        "macos_settled_rerun": macos_settled_rerun.run_argv(
            macos_settled_rerun.PLAN[0], "b"
        ),
        "mtp_treatment_gate": mtp_treatment_gate.run_argv(
            P, P / "s.log", batch="b", trials=1, require_draft=True
        ),
        "restart_between_trials A": restart_between_trials.run_argv(
            restart_between_trials.ARMS["A"], P / "s.log"
        ),
        "restart_between_trials B": restart_between_trials.run_argv(
            restart_between_trials.ARMS["B"], P / "s.log"
        ),
        "route_agent_ab": route_agent_ab.arm_argv("t", P, 1, HEAD),
        "stack_agent_ab": stack_agent_ab.run_argv(_stack_arm(), "t", P, HEAD),
        "strip_toggle_ab": _strip_toggle_argv(),
    }


def test_every_m5max_driver_runs_the_legacy_layout() -> None:
    wrong = {
        name: ("--targets" in argv and _targets(argv))
        for name, argv in _every_m5max_run_argv().items()
        if "--targets" not in argv or _targets(argv) != "legacy"
    }
    assert not wrong, f"these drivers would run sandbox on the M5 Max: {wrong}"


def test_an_arms_own_run_flags_still_win_in_stack_agent_ab() -> None:
    """RUN_FLAGS come after the default, so an arm can still ask for sandbox
    on purpose (a fresh series)."""
    argv = stack_agent_ab.run_argv(_stack_arm("--targets sandbox"), "t", P, HEAD)
    assert _targets(argv) == "sandbox"
