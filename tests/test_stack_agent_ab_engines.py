"""The A/B runner gained a second engine (#191); the first one must not move.

`scripts/stack_agent_ab.sh` was a FLAG A/B harness -- its own run-record says
"same tree and same gguf in both arms: the flags above are the only variable".
#191 needed an ENGINE A/B, and the risk of that change is not that mlx-serve
fails to start. It is that ds4's default path shifts by accident, because every
comparison this repo has published came through it and none of them would say
so.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
AB = ROOT / "scripts" / "stack_agent_ab.sh"
LIB = ROOT / "scripts" / "lib" / "mlx_serve.sh"


def _mlx_branch() -> str:
    """The mlx-serve case inside restart_server, and only that.

    Anchored on the function, not on the string `mlx-serve)` -- that also
    appears in the `engine_ident` helper earlier in the file, and slicing from
    it swept in the whole ds4 branch. The first version of this test passed
    that way for the wrong reason.
    """
    body = AB.read_text()
    fn = body[body.index("restart_server() {") : body.index("sweep() {")]
    start = fn.index("  mlx-serve)")
    end = fn.index("  *)", start)
    return fn[start:end]


@pytest.mark.parametrize("script", [AB, LIB])
def test_it_parses(script: pathlib.Path) -> None:
    # A syntax error here is discovered eight sweeps into a six-hour run.
    done = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("var", ["NEW_ENGINE", "OLD_ENGINE"])
def test_the_default_engine_is_still_ds4(var: str) -> None:
    """Change nothing and this runs exactly what it always ran.

    If a default flips to mlx-serve, #138's re-runs quietly become a different
    experiment and the run-record's engine line is the only thing that would
    say so -- after the fact.
    """
    body = AB.read_text()
    assert re.search(rf"^{var}=\$\{{{var}:-ds4\}}$", body, re.M), (
        f"{var} no longer defaults to ds4"
    )


def test_an_unknown_engine_is_refused_not_defaulted() -> None:
    # A typo must stop the run, not silently select ds4 and produce 120 rows
    # attributed to the wrong engine.
    body = AB.read_text()
    assert "REFUSING: unknown engine" in body


def test_both_engines_are_stopped_before_either_starts() -> None:
    """Two ~100 GiB servers do not fit on this machine at once.

    The previous sweep may have been the other arm, so restarting must stop
    both. #145 is the precedent: a restart with no matching stop left 97.9 GiB
    resident on four consecutive clean runs.
    """
    body = AB.read_text()
    start = body.index("restart_server() {")
    end = body.index("sweep() {")
    fn = body[start:end]
    assert "ds4_stop_server" in fn and "mlx_serve_stop_server" in fn, (
        "restart_server does not stop both engines before starting one"
    )


def test_the_mlx_arm_records_no_metal_route() -> None:
    """The Metal route is a ds4 concept (#149).

    An mlx-serve row must read `unrecorded` rather than inherit the route the
    other arm's server happened to log.
    """
    assert "ds4_record_route" not in _mlx_branch()


def test_the_mlx_arm_serves_rather_than_chats() -> None:
    """`mlx-serve run <model>` is the interactive REPL and never returns."""
    mlx = _mlx_branch()
    assert "--serve" in mlx
    assert "--host 127.0.0.1" in mlx, "the documented default host is 0.0.0.0"


def test_the_draft_log_engine_is_per_arm() -> None:
    # It was hard-coded to ds4. A non-ds4 arm would then claim ds4 provenance.
    body = AB.read_text()
    assert '--draft-log-engine "$engine"' in body


# --- the two bugs the review caught, held by tests ------------------------
#
# Both were found by peer-deepseek reviewing 9cc3342, and both would have
# produced a full night of unusable rows. A review finding that is not held by
# a test comes back.


def test_the_mlx_arm_waits_with_a_model_id() -> None:
    """`wait_ready.py --model` is required, and omitting it fails silently.

    argparse exits 2, the `| tail -1` pipe swallows the status, and the
    harness proceeds WITHOUT waiting -- so the first trials of every sweep hit
    a server still paging in 100 GiB and record 503s as failed rows.
    """
    mlx = _mlx_branch()
    assert "wait_ready.py" in mlx, "the mlx arm does not wait for readiness"
    # Slice the wait_ready invocation, not the whole branch. A bare
    # `"--model" in mlx` passes on the SERVER's own --model flag -- which is
    # how the first version of this test was vacuous, the same defect it was
    # written to prevent.
    call = mlx[mlx.index("wait_ready.py") :]
    call = call[: call.index(")")]
    assert "--model" in call, (
        "wait_ready.py is called without --model; it is required, and the "
        "failure is silent because the pipe hides the exit status"
    )


def test_only_one_exit_trap_is_armed() -> None:
    """Chaining the two teardowns leaves the second one unreachable.

    `ds4_stop_on_exit` does `trap - EXIT INT TERM` and then `exit`, so a
    handler chained after it never runs. Arming both left mlx-serve -- ~85 GiB
    -- resident on every normal exit, the #145 leak this change existed to
    prevent. `mlx_serve_stop_on_exit` stops both engines, so it is the only
    one armed.
    """
    body = AB.read_text()
    armed = [
        ln.strip()
        for ln in body.splitlines()
        if ln.strip() in ("ds4_arm_stop_trap", "mlx_serve_arm_stop_trap")
    ]
    assert armed == ["mlx_serve_arm_stop_trap"], (
        f"expected only mlx_serve_arm_stop_trap to be armed, found {armed}"
    )


def test_the_mlx_teardown_stops_both_engines() -> None:
    # The above is only safe because this one is true.
    lib = LIB.read_text()
    fn = lib[lib.index("mlx_serve_stop_on_exit() {") :]
    fn = fn[: fn.index("\n}")]
    assert "mlx_serve_stop_server" in fn
    assert "ds4_stop_server" in fn, (
        "the sole armed trap does not stop ds4; arming only it would leak ds4"
    )


def test_the_mlx_arm_passes_the_preregistered_kv_quant() -> None:
    # The pre-registration fixes --kv-quant off so KV quantisation is not a
    # third variable moving with engine and quant. Relying on the default
    # would leave that unstated in the run record.
    assert "--kv-quant off" in _mlx_branch()
