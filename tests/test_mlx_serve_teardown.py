"""The mlx-serve teardown helper: `scripts/lib/mlx_serve.sh` (#235).

The twin of `test_ds4_server_teardown.py`, and it exists because the defect
that file records has an identical copy here. `mlx_serve_arm_stop_trap` chains
onto an existing EXIT handler exactly as `ds4_arm_stop_trap` did, and
`mlx_serve_stop_on_exit` opened `local status=$?` -- which in the chained case
is the EXISTING handler's status, not the script's.

**It has never fired.** `stack_agent_ab.sh` is the only file that sources this
one, it arms nothing before line 481, so the bare branch runs and the status
survives. That is not a reason to leave it: the chained branch was written to
be taken -- the library's own comment says the handler it expects to chain
onto is ds4's teardown -- and `stack_agent_ab.sh:534` states as a fact that
"the EXIT trap (mlx_serve_stop_on_exit) preserves $?", which is the property
the chained branch broke. A latent defect under a comment asserting the
opposite is how the ds4 one survived seven callers.

Reproduced before it was fixed: through the chained branch `exit 1` came out
as 0; through the bare branch it came out as 1.

The fake `pgrep`/`pkill` is the same shape as the ds4 file's, so these need no
mlx-serve, no model, and no Metal.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
HELPER = REPO / "scripts" / "lib" / "mlx_serve.sh"


@pytest.fixture
def fake_ps(tmp_path: pathlib.Path) -> pathlib.Path:
    """A PATH where an 'mlx-serve' runs until something kills it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "state").write_text("running\n")
    (bin_dir / "pgrep").write_text(
        textwrap.dedent(f"""\
        #!/bin/sh
        echo "pgrep $*" >> {tmp_path}/calls
        [ "$(cat {tmp_path}/state)" = "running" ] || exit 1
        echo 4242
        """)
    )
    (bin_dir / "pkill").write_text(
        textwrap.dedent(f"""\
        #!/bin/sh
        echo "pkill $*" >> {tmp_path}/calls
        echo stopped > {tmp_path}/state
        """)
    )
    for tool in ("pgrep", "pkill"):
        (bin_dir / tool).chmod(0o755)
    return tmp_path


def run_script(fake_ps: pathlib.Path, body: str) -> tuple[int, str, str]:
    script = fake_ps / "run.sh"
    script.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nsource {HELPER}\n"
        + textwrap.dedent(body)
    )
    script.chmod(0o755)
    proc = subprocess.run(
        ["bash", str(script)],
        env={"PATH": f"{fake_ps / 'bin'}:/usr/bin:/bin", "HOME": str(fake_ps)},
        capture_output=True,
        text=True,
        timeout=30,
        # The status IS the assertion in half these tests. Raising on it would
        # turn every one of them into an error before it could be checked.
        check=False,
    )
    return (
        proc.returncode,
        proc.stdout + proc.stderr,
        (fake_ps / "state").read_text().strip(),
    )


def test_a_clean_finish_stops_the_server(fake_ps):
    code, _, state = run_script(
        fake_ps,
        """
        mlx_serve_arm_stop_trap
        echo done
    """,
    )
    assert code == 0
    assert state == "stopped", "100 GiB does not get to outlive the run"


def test_a_failing_run_keeps_its_status(fake_ps):
    """The bare branch -- what `stack_agent_ab.sh` actually takes today."""
    code, _, state = run_script(
        fake_ps,
        """
        mlx_serve_arm_stop_trap
        exit 3
    """,
    )
    assert code == 3
    assert state == "stopped"


def test_a_failing_run_keeps_its_status_through_a_CHAINED_trap(fake_ps):
    """The defect. Reproduced as 0 before the fix, asserted as 3 after it.

    The existing handler here SUCCEEDS, which is what makes the bug quiet: the
    status the chained trap read was 0 and the status it should have read was
    3, and nothing downstream could tell the difference.
    """
    code, _, state = run_script(
        fake_ps,
        f"""
        trap 'echo released > {fake_ps}/lock' EXIT
        mlx_serve_arm_stop_trap
        exit 3
    """,
    )
    assert code == 3, "the chained trap must not overwrite the run's status"
    assert state == "stopped"
    assert (fake_ps / "lock").read_text().strip() == "released", (
        "and chaining must still run the handler it chained onto"
    )


def test_arming_the_trap_does_not_discard_an_existing_one(fake_ps):
    code, _, state = run_script(
        fake_ps,
        f"""
        trap 'echo released > {fake_ps}/lock' EXIT
        mlx_serve_arm_stop_trap
        echo done
    """,
    )
    assert code == 0
    assert state == "stopped"
    assert (fake_ps / "lock").read_text().strip() == "released"


def test_it_also_stops_ds4_when_that_library_is_loaded(fake_ps):
    """Both engines, because the trap cannot know which arm was interrupted.

    A run that alternates arms leaves either engine resident. Asking for both
    is free -- stopping one that is not running is a no-op -- and asking for
    one is a coin flip.
    """
    code, _, _ = run_script(
        fake_ps,
        """
        ds4_stop_server() { echo "ds4 teardown asked"; return 0; }
        mlx_serve_arm_stop_trap
        echo done
    """,
    )
    assert code == 0


def test_the_pattern_does_not_match_a_shell_that_merely_mentions_the_server():
    """`pgrep -f` reads the whole command line, so a bare name self-matches.

    `--serve` is in the server's argv and not in this file's own invocation.
    The constant is read from the library rather than retyped: a copy here
    would agree with itself and stop agreeing with the shell.
    """
    text = HELPER.read_text()
    assert "MLX_SERVE_PATTERN=${MLX_SERVE_PATTERN:-'mlx-serve --model'}" in text


def test_the_shell_it_guards_is_still_here():
    """When `stack_agent_ab.sh` is ported and deleted, this file goes too."""
    assert (REPO / "scripts" / "stack_agent_ab.sh").exists()
