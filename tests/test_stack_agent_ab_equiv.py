"""The #138/#191 stack A/B, shell against port (#235, #191, #225).

`vault/stack_agent_ab.sh` -> `scripts/stack_agent_ab.py`. The last of the
retirement differentials, and the only one that does not drive the whole shell.

## Why this one extracts functions instead of running the script

The other differentials run the real `.sh` end to end against recording fakes.
This script is 541 lines and its top level takes the machine lock, arms two
chained EXIT traps, syncs worktrees, checks two engine binaries and starts a
shim before it reaches a sweep. Driving all of that would need more scaffolding
than the file has lines, and #235's own stopping rule says to stop and write
the deviation down rather than build it.

So this executes the shell's OWN function text -- `server_argv` and `sweep` --
under bash with the fakes on PATH, and compares what each hands its children.
That is the technique `test_stack_agent_ab_engines.py` already established
here, for the same reason: *"a string-match guard would be satisfied by a
pattern written from the broken code. This executes the actual function text."*

**What that does not cover, stated rather than implied:** the top-level
sequence -- lock, trap arming, worktree sync, shim start, sweep ordering. Those
are covered by `test_stack_agent_ab_python.py`, `test_stack_agent_ab_failure.py`
and `tests/test_mlx_serve_teardown.py` on the shell side, and by the port's own
tests on the other. What is compared here is the two command lines that decide
what gets measured, for both engines.
"""

from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
AB = ROOT / "vault" / "stack_agent_ab.sh"

for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(ROOT / sub))

import stack_agent_ab as driver
import stack_arm

HEAD = "0123456789abcdef0123456789abcdef01234567"


def _fn(name: str) -> str:
    """One function's source text, lifted out of the shell.

    Sliced on `name() {` .. `\\n}` exactly as the engines test does. A helper
    rather than a copy, so both files read the same shell.
    """
    body = AB.read_text()
    start = body.index(f"{name}() {{")
    return body[start : body.index("\n}", start) + 2]


def _shell_server_argv(call: list[str], env: dict[str, str] | None = None) -> list[str]:
    """`SERVER_ARGV` after the real `server_argv` runs with these arguments."""
    args = " ".join(shlex.quote(a) for a in call)
    script = "\n".join(
        [
            "set -euo pipefail",
            _fn("server_argv"),
            f"server_argv {args}",
            'printf "%s\\n" ${SERVER_ARGV[*]+"${SERVER_ARGV[*]}"}',
        ]
    )
    done = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
    )
    assert done.returncode == 0, f"server_argv refused:\n{done.stdout}\n{done.stderr}"
    return done.stdout.strip().split()


def _arm(**kw: object) -> stack_arm.Arm:
    """A port-side arm with #138's defaults, overridden per test."""
    base: dict[str, object] = {
        "name": "new",
        "backend": "qwen38fnds4kimat",
        "engine": stack_arm.DS4,
        "tree": pathlib.Path("/t/tree"),
        "gguf": pathlib.Path("/t/model.gguf"),
        "ple": pathlib.Path("/t/ple.gguf"),
        "kv": pathlib.Path("/t/kv"),
        "flags": "",
        "run_flags": "",
        "mlx_model": None,
        "mlx_port": stack_arm.MLX_PORT,
        "mlx_bin": stack_arm.MLX_SERVE,
    }
    base.update(kw)
    return stack_arm.Arm(**base)  # type: ignore[arg-type]


def test_the_script_parses() -> None:
    subprocess.run(["bash", "-n", str(AB)], check=True)


def test_the_ds4_arm_gets_the_same_server_command(tmp_path) -> None:
    """The engine both #138 arms run, and the one every published row used."""
    shell = _shell_server_argv(
        ["ds4", "/t/model.gguf", "/t/ple.gguf", "/t/kv", "", "", "11234", "mlx-serve"],
    )
    port = stack_arm.server_argv(_arm())
    # argv[0] differs by construction: the shell `cd`s into the tree and runs
    # `./ds4-server`, the port names the binary absolutely. Everything that
    # decides what is measured is in the rest.
    assert shell[0].endswith("ds4-server"), shell[0]
    assert port[0].endswith("ds4-server"), port[0]
    assert shell[1:] == port[1:], f"shell {shell[1:]}\nport  {port[1:]}"


def test_the_mlx_arm_gets_the_same_server_command() -> None:
    """#191's second engine. `--serve`, not a chat; `--kv-quant off` (#225).

    mlx-serve keeps no disk KV, so the ds4 KV flags are absent on both sides --
    an arm that carried them would be asking a second engine for a first
    engine's cache.
    """
    shell = _shell_server_argv(
        [
            "mlx-serve",
            "",
            "",
            "",
            "",
            "/t/pack",
            "11234",
            "mlx-serve",
        ],
    )
    port = stack_arm.server_argv(
        _arm(engine=stack_arm.MLX_SERVE, mlx_model=pathlib.Path("/t/pack"))
    )
    assert shell == port, f"shell {shell}\nport  {port}"
    assert "--serve" in shell, "an mlx arm that chats is measuring another thing"
    assert ["--kv-quant", "off"] == [
        shell[shell.index("--kv-quant")],
        shell[shell.index("--kv-quant") + 1],
    ]
    assert "--kv-disk-dir" not in shell, "mlx-serve keeps no disk KV"


def test_the_mlx_binary_is_the_arms_own_not_the_brew_default() -> None:
    """#225: two arms must be able to name two different binaries.

    Both resolving to one brew install is a paired comparison of a build
    against itself, and it reports as a clean null.
    """
    shell = _shell_server_argv(
        ["mlx-serve", "", "", "", "", "/t/pack", "11234", "/opt/other/mlx-serve"],
    )
    port = stack_arm.server_argv(
        _arm(
            engine=stack_arm.MLX_SERVE,
            mlx_model=pathlib.Path("/t/pack"),
            mlx_bin="/opt/other/mlx-serve",
        )
    )
    assert shell[0] == port[0] == "/opt/other/mlx-serve"
    assert shell == port


def test_an_arms_extra_flags_reach_the_server_on_both_sides() -> None:
    """`FLAGS` is how #210/#151 put MTP on against MTP off in ONE tree.

    A port that dropped it would run both arms as the control and report the
    treatment's number.
    """
    flags = "--mtp-model /t/mtp.gguf --mtp-draft 7 --mtp-timing"
    shell = _shell_server_argv(
        [
            "ds4",
            "/t/model.gguf",
            "/t/ple.gguf",
            "/t/kv",
            flags,
            "",
            "11234",
            "mlx-serve",
        ],
    )
    port = stack_arm.server_argv(_arm(flags=flags))
    assert shell[1:] == port[1:], f"shell {shell[1:]}\nport  {port[1:]}"
    for token in ("--mtp-model", "--mtp-draft", "--mtp-timing"):
        assert token in shell and token in port, token


@pytest.mark.parametrize(
    ("engine", "expect"),
    [(stack_arm.DS4, "ds4"), (stack_arm.MLX_SERVE, None)],
)
def test_the_draft_log_engine_is_omitted_for_an_engine_run_py_rejects(
    engine, expect
) -> None:
    """run.py accepts ds4|mtplx. Passing `mlx-serve` is an argparse error that
    ends the sweep in one second -- not a harmless unknown option.

    The shell decides this in a `case` inside `sweep`; the port reads
    `arm.draft_log_engine`. Both must OMIT rather than pass a rejected name.
    """
    text = _fn("sweep")
    assert "ds4 | mtplx)" in text, "the shell's case list changed"
    arm = _arm(
        engine=engine,
        mlx_model=pathlib.Path("/t/pack") if engine == stack_arm.MLX_SERVE else None,
    )
    argv = driver.run_argv(arm, "new-sweep1", pathlib.Path("/t/out"), HEAD)
    if expect is None:
        assert "--draft-log-engine" not in argv, argv
    else:
        assert argv[argv.index("--draft-log-engine") + 1] == expect


def test_the_ports_run_argv_matches_the_shells_literal_flags() -> None:
    """The flags the shell writes into `run.py`, read out of the shell.

    Compared against the port's list rather than retyped here: a constant
    typed into this file would agree with itself and stop agreeing with the
    script, which is the whole failure `tests/fixtures/logs/README.md` records.
    """
    text = _fn("sweep")
    for flag in (
        "--backend",
        "--trials",
        "--client",
        "--no-lock",
        "--require-harness-head",
        "--server-log",
    ):
        assert flag in text, f"the shell no longer passes {flag}"
    argv = driver.run_argv(_arm(), "new-sweep1", pathlib.Path("/t/out"), HEAD)
    assert argv[argv.index("--require-harness-head") + 1] == HEAD
    assert argv[argv.index("--server-log") + 1] == "/t/out/server-new-sweep1.log"
    assert argv[argv.index("--trials") + 1] == "1"
    assert "--no-lock" in argv, "the batch holds the lock; run.py must not"


def test_the_two_arms_must_not_name_the_same_backend() -> None:
    """Both sides refuse it. Rows from two arms sharing a backend name are
    indistinguishable at read-out, which is a voided batch discovered hours
    later rather than a refusal at second zero."""
    assert "REFUSING: both arms name backend" in AB.read_text()
    with pytest.raises(Exception) as caught:
        stack_arm.check_pair(_arm(name="new"), _arm(name="old"))
    assert "backend" in str(caught.value).lower(), caught.value


def test_the_shell_it_replaces_is_still_here() -> None:
    assert AB.exists()
