#!/usr/bin/env python3
"""Generate the argv/env equivalence fixtures for the #235 port. #235, #264, #149

Two recordings feed `tests/test_equiv.py`:

- `route_agent_ab-port.jsonl` -- the python port's *real* emissions. Captured
  by driving the port's own argv/env builders (`arm_argv`, `server_command`,
  `arm_env`) through the PATH shim, spawning each child the way the port does,
  with `child.run`. This side cannot drift from what the port really emits.
- `route_agent_ab-shell.jsonl` -- the shell driver's emissions. Derived by
  transcribing `route_agent_ab.sh`'s run.py and ds4-server command lines (lines
  156-162 and 197-201). The .sh cannot be run here: it needs the machine lock,
  a clean checkout, and a GPU, and it still carries the #264 flag the port
  removed. So the shell side is a transcription of a frozen .sh, and the tests
  diff the port against it and pin what differs.

Run on a host with Python and uv only (no GPU, no server, no machine lock):

    uv run python scripts/gen_equiv_fixtures.py

The hostile-base trick: both sides spawn against a base environment that has
`DS4_METAL_ENABLE_TENSOR` exported -- an operator who exported it in their own
shell. The assertion that matters is that the port's reference arm still
removes it from the server child even then. `env -u` on the shell and `unset`
on the port have to reach the child, and the recorded fixture is what proves
they do, rather than a shell that happened not to export it.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(REPO / sub))

import equiv
import metal_route
import route_agent_ab as ra

FIXTURES = REPO / "tests" / "fixtures" / "equiv"
PORT = FIXTURES / "route_agent_ab-port.jsonl"
SHELL = FIXTURES / "route_agent_ab-shell.jsonl"

# A head that is not a real one, stable across machines. The driver stamps
# every row with it; here its only job is to be identical on both sides so the
# argv diff is about flags, not about the repo's current head.
HARNESS_HEAD = "deadbeef"
TRIALS = 1

SOURCE_PORT = "route_agent_ab.py (recorded via the PATH shim)"
SOURCE_SHELL = "scripts/route_agent_ab.sh:156-162 and :197-201 (transcribed)"


def hostile_base() -> dict[str, str]:
    """A deterministic base env that exports the #149 variable.

    Both sides run against it. `child.run` merges overrides into `os.environ`
    and removes `unset` keys from the merged copy, so the base must be a real
    environment with PATH and HOME (the shim shebangs need both) plus a leftover
    `DS4_METAL_ENABLE_TENSOR` that the reference arm has to strip.
    """
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        equiv.TENSOR_ENV: "7",  # the operator exported it; it must still go
    }


@contextlib.contextmanager
def hostile_env():
    """Swap os.environ for the controlled base, restoring it afterwards."""
    saved = dict(os.environ)
    base = {
        "PATH": saved.get("PATH", ""),
        "HOME": saved.get("HOME", ""),
        equiv.TENSOR_ENV: "7",  # the operator exported it; it must still go
    }
    os.environ.clear()
    os.environ.update(base)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def write_header(path: pathlib.Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        handle.write(f"# source: {source}\n")


def record_port_side() -> None:
    """Record the python port's real emissions through the shim."""
    with hostile_env(), tempfile.TemporaryDirectory(prefix="equiv-port-") as tmp:
        tmp = pathlib.Path(tmp)
        shim = equiv.write_shim(
            tmp / "shim", tmp / "probe"
        )  # EQUIV_OUT is set per-spawn
        log = tmp / "spawn.log"
        # A stable token, not a temp path: it lands in the recorded --server-log
        # argv, and a committed fixture must not change on every regeneration.
        out_dir = pathlib.Path("out")
        for arm in (metal_route.TENSOR, metal_route.WITHHELD):
            tag = f"{arm}-sweep1"
            set_, unset_ = ra.arm_env(arm)
            kv_dir = ra.KV[arm]
            server_flags = ra.server_command(kv_dir)[1:]
            run_flags = ra.arm_argv(tag, out_dir, TRIALS, HARNESS_HEAD)[4:]
            # The port spawns ds4-server with the arm's env overrides and the
            # arm's unset, through its own child.run. The fake records what the
            # child really sees: set to "1" on the tensor arm, absent on the
            # reference arm even though the base exported it.
            equiv.run_fake(
                shim,
                "ds4-server",
                arm,
                server_flags,
                set_,
                unset_,
                PORT,
                log,
                via="ds4-server",
            )
            # run.py is the client; the variable is not its concern. But every
            # flag the port hands it must be one run.py declares (#264).
            equiv.run_fake(
                shim, "run.py", arm, run_flags, {}, (), PORT, log, via="run.py"
            )
        if not equiv.by_program(equiv.load(PORT), "run.py"):
            raise SystemExit("port fixture recorded no run.py rows")


def record_shell_side() -> None:
    """Transcribe route_agent_ab.sh's run.py and ds4-server invocations."""
    base = hostile_base()
    run_flags = [
        "--backend",
        ra.BACKEND,
        "--trials",
        str(TRIALS),
        "--client",
        "opencode",
        "--no-lock",
        "--skip-tensor-gate",  # #264: run.py removed it; the shell still passes it
        "--require-harness-head",
        HARNESS_HEAD,
    ]
    for arm in (metal_route.TENSOR, metal_route.WITHHELD):
        # The .sh and the port both split the KV cache dir per arm; the flags
        # differ between the arms only in that value, which is the same dir the
        # port uses, so the two drivers' server command lines are byte-identical.
        server_flags = [
            "--metal",
            "-m",
            str(ra.GGUF),
            "--ple",
            str(ra.PLE),
            "--ctx",
            "100000",
            "--warm-weights",
            "--kv-disk-dir",
            str(ra.KV[arm]),
            "--kv-disk-space-mb",
            "8192",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
        if arm == metal_route.TENSOR:
            env = dict(base, **{equiv.TENSOR_ENV: equiv.TENSOR_ENV_ON})
        else:
            env = {k: v for k, v in base.items() if k != equiv.TENSOR_ENV}
        equiv.record(SHELL, equiv.Invocation("ds4-server", arm, server_flags, env))
        equiv.record(SHELL, equiv.Invocation("run.py", arm, run_flags, base))


def main() -> int:
    write_header(PORT, SOURCE_PORT)
    write_header(SHELL, SOURCE_SHELL)
    record_port_side()
    record_shell_side()
    print(f"wrote {PORT}")
    print(f"wrote {SHELL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
