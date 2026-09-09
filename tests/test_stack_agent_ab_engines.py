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
import shlex
import subprocess
import tempfile

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


def _server_argv(engine: str) -> str:
    """The server_argv case arm for one engine, and only that.

    server_argv is the single source the run-record and restart_server both
    read, so the flags that used to live in the mlx branch now live here. The
    guards that assert on the mlx branch's flags must assert on this instead,
    or they pass for the wrong reason -- the same defect _mlx_branch() was
    written to avoid. Slicing one case arm keeps the ds4 arm out of the mlx
    assertions: --host 127.0.0.1 appears in both arms, so a whole-function
    slice would let the mlx guard pass on the ds4 arm's flag. Each arm ends in
    `;;` (server_argv has no `*)` arm to slice to), so the slice runs to the
    arm's own terminator.
    """
    body = AB.read_text()
    fn = body[body.index("server_argv() {") :]
    fn = fn[: fn.index("\n}")]
    start = fn.index(f"  {engine})")
    end = fn.index(";;", start) + 2
    return fn[start:end]


def _run_server_argv(
    calls: list[list[str]], cwd: pathlib.Path | None = None
) -> subprocess.CompletedProcess:
    """Run the real server_argv from the script under set -euo pipefail.

    A string-match guard would be satisfied by a pattern written from the
    broken code. This executes the actual function text, so the empty-array
    set -u abort and the glob fire the way they would in the run. Each call is
    wrapped in an `if` so a refusal does not abort the probe before it can
    print the resulting SERVER_ARGV.
    """
    body = AB.read_text()
    start = body.index("server_argv() {")
    end = body.index("\n}", start) + 2
    fn = body[start:end]
    lines = ["set -euo pipefail", fn]
    for call in calls:
        args = " ".join(shlex.quote(a) for a in call)
        lines.append(
            f'if server_argv {args}; then printf "rc=0\\n"; '
            f'else printf "rc=%d\\n" "$?"; fi'
        )
        # ${SERVER_ARGV[*]+...} is the 3.2-safe empty-array idiom: on bash 3.2,
        # "${SERVER_ARGV[*]}" on an empty array is unbound under set -u, and the
        # refusal path leaves SERVER_ARGV empty.
        lines.append('printf "argv=%s\\n" ${SERVER_ARGV[*]+"${SERVER_ARGV[*]}"}')
    return subprocess.run(
        ["bash", "-c", "\n".join(lines)],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


@pytest.mark.parametrize("script", [AB, LIB])
def test_it_parses(script: pathlib.Path) -> None:
    """A syntax error here is discovered eight sweeps into a six-hour run.

    Check it with the interpreter the file actually runs under. The first
    version used `sh -n` on a `#!/usr/bin/env bash` script: on macOS `sh` is
    permissive enough to accept a bash array, on the Linux runner `sh` is dash
    and rejected `ds4_files=()` at line 136. CI went red for four commits and
    the script was never at fault -- the checker was.

    lib/*.sh carry no shebang because they are sourced, never executed; they
    are sourced BY a bash script, so bash is their interpreter too.
    """
    first = script.read_text().split("\n", 1)[0]
    shell = (
        "bash" if not first.startswith("#!") else first.removeprefix("#!").split()[-1]
    )
    done = subprocess.run(
        [shell, "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("var", ["NEW_ENGINE", "OLD_ENGINE"])
def test_the_default_engine_is_still_ds4(var: str) -> None:
    """Change nothing and this runs exactly what it always ran.

    If a default flips to mlx-serve, #138's re-runs quietly become a different
    experiment and the run-record's engine line is the only thing that would
    say so -- after the fact.
    """
    body = AB.read_text()
    assert re.search(rf"^{var}=\$\{{{var}:-ds4\}}$", body, re.MULTILINE), (
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
    mlx = _server_argv("mlx-serve")
    assert "--serve" in mlx
    assert "--host 127.0.0.1" in mlx, "the documented default host is 0.0.0.0"


def test_the_draft_log_engine_is_omitted_for_engines_run_py_rejects() -> None:
    """run.py takes --draft-log-engine ds4|mtplx and nothing else.

    This test previously asserted `--draft-log-engine "$engine"` was always
    passed, which enforced the WRONG behaviour: it made the flag per-arm, and
    an mlx-serve arm then passed a value argparse rejects. The first sweep died
    in one second with `invalid choice: 'mlx-serve'`. A test can encode an
    assumption instead of a requirement, and this one did.
    """
    body = AB.read_text()
    assert '--draft-log-engine "$engine"' not in body, (
        "the engine name is passed straight through; run.py rejects mlx-serve"
    )
    assert "$draft_flag" in body, "the flag is no longer built conditionally"
    # And the guard: only the two engines run.py accepts may set it.
    case = body[body.index("local draft_flag=") :]
    case = case[: case.index("esac")]
    assert "ds4 | mtplx)" in case or "ds4|mtplx)" in case, case[:200]
    assert "mlx-serve" not in case, "mlx-serve must not set --draft-log-engine"


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
    # would leave that unstated in the run record. The flag lives in
    # server_argv, the single source the record and restart_server both read,
    # so the record states it.
    assert "--kv-quant off" in _server_argv("mlx-serve")


def test_the_run_record_states_the_effective_server_command_line() -> None:
    """run-record must state what actually runs, not just the FLAGS variable.

    The mlx branch hard-codes --ctx-size 100000 --kv-quant off and the ds4
    branch hard-codes --ctx 100000 --warm-weights --kv-disk-space-mb 8192
    --host --port. A record that shows only NEW_FLAGS/OLD_FLAGS understates
    the effective command line: on 2026-09-08 the mlx arm ran with
    --kv-quant off and the record said "NEW flags=<none>". The record must
    state the effective argv, built by the same function that runs the server,
    so the two cannot drift.
    """
    body = AB.read_text()
    start = body.index('echo "# stack agent A/B, started')
    end = body.index('> "$OUT/run-record.txt"')
    record = body[start:end]
    assert "NEW server:" in record and "OLD server:" in record, (
        "run-record does not state the effective server command line for both "
        "arms; it shows only the FLAGS variable, which omits the hard-coded flags"
    )


# --- server_argv is executed, not grepped -----------------------------------
#
# A guard that greps for `${f[@]+` would have been just as satisfied by the
# broken version if the pattern had been written from the broken code. These
# extract the real function text and run it under set -euo pipefail, so the
# empty-array set -u abort and the glob fire the way they would in the run.


def test_server_argv_survives_empty_flags() -> None:
    """NEW_FLAGS is empty in the live #191 config, so server_argv's first call
    has an empty flags array. On bash 3.2 (the /bin/bash this script runs
    under), "${f[@]}" on an empty array is unbound under set -u and aborts the
    whole run before sweep 1. The 3.2-safe idiom is ${f[@]+"${f[@]}"}."""
    done = _run_server_argv(
        [
            ["ds4", "/m/q.gguf", "/m/ple.json", "/kv", ""],
            ["mlx-serve", "/m/pack", "", "", "", "/m/pack", "11234"],
        ]
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("rc=0") == 2, done.stdout


def test_server_argv_refuses_an_unknown_engine() -> None:
    """A mistyped engine must not print a stale array from the previous call.

    In the record block the two calls are back to back, so a stale SERVER_ARGV
    would print the NEW arm's command line under OLD server: -- a record
    confidently stating the wrong thing. server_argv must reset the array and
    refuse, so the refusal aborts the caller under set -e.
    """
    done = _run_server_argv(
        [
            ["mlx-serve", "/m/pack", "", "", "", "/m/pack", "11234"],
            ["ds4x", "/m/q.gguf", "/m/ple.json", "/kv", ""],
        ]
    )
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    argv_lines = [l for l in lines if l.startswith("argv=")]
    assert "rc=1" in done.stdout, done.stdout
    assert argv_lines[-1] == "argv=", done.stdout  # empty, not a stale array


def test_server_argv_does_not_glob() -> None:
    """A flag value containing a glob must stay literal.

    The old word-split passed --foo *.gbnf literally; eval would glob it. The
    array must not expand it either, or a flag value that happens to match a
    file in the cwd would silently change the command line.
    """
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d)
        (p / "a.gbnf").write_text("")
        (p / "b.gbnf").write_text("")
        done = _run_server_argv(
            [["mlx-serve", "/m/pack", "", "", "--foo *.gbnf", "/m/pack", "11234"]],
            cwd=p,
        )
    assert done.returncode == 0, done.stderr
    assert "--foo *.gbnf" in done.stdout, done.stdout


def test_server_argv_defaults_to_the_path_binary() -> None:
    """A caller that names no binary must still get the PATH one.

    #225: the per-arm binary path defaults to the bare name, so an existing
    caller that passes no binary runs exactly what it always ran -- the brew
    install resolved on PATH.
    """
    done = _run_server_argv([["mlx-serve", "/m/pack", "", "", "", "/m/pack", "11234"]])
    assert done.returncode == 0, done.stderr
    assert "mlx-serve --model /m/pack" in done.stdout, done.stdout


def test_server_argv_uses_the_named_binary() -> None:
    """A per-arm binary path must override the PATH default.

    #225 arm B is a git checkout; naming its built binary is what keeps the two
    arms from both resolving to the same brew binary on PATH.
    """
    done = _run_server_argv(
        [
            [
                "mlx-serve",
                "/m/pack",
                "",
                "",
                "",
                "/m/pack",
                "11234",
                "/m/tree/zig-out/bin/mlx-serve",
            ]
        ]
    )
    assert done.returncode == 0, done.stderr
    assert "/m/tree/zig-out/bin/mlx-serve --model /m/pack" in done.stdout, done.stdout


def _engine_ident(binary: str, cwd: pathlib.Path | None = None) -> str:
    """Run the real engine_ident from the script against one binary."""
    body = AB.read_text()
    start = body.index("engine_ident() {")
    end = body.index("\n}", start) + 2
    script = (
        "set -euo pipefail\n"
        + body[start:end]
        + f'\nengine_ident mlx-serve "" "{binary}"\n'
    )
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=cwd, check=False
    )
    return out.stdout.strip()


def test_a_brew_symlink_is_not_stamped_with_homebrews_sha(tmp_path):
    """The Homebrew trap, twice now, so it gets a test.

    brew links <prefix>/bin/tool -> ../Cellar/tool/<version>/bin/tool, and
    <prefix> is itself a git checkout. Resolving only the dirname lands in
    <prefix>/bin, which contains no /Cellar/, so the guard never fires and
    `git rev-parse` answers with HOMEBREW's HEAD. It stamped the 26.9.1 arm
    with `08e85c4e42` -- a real sha, of the wrong repo -- on the Python side
    (fixed in a219ca5) and again here, because `pwd -P` resolves a directory
    and not a link. #225 compares two builds; two arms both wearing a sha,
    with no way to tell which repo it came from, is worse than no label.
    """
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    cellar = prefix / "Cellar" / "mlx-serve" / "26.9.1" / "bin"
    cellar.mkdir(parents=True)
    real = cellar / "mlx-serve"
    real.write_text("#!/bin/sh\necho 'mlx-serve 26.9.1'\n")
    real.chmod(0o755)
    (prefix / "bin" / "mlx-serve").symlink_to(
        pathlib.Path("..") / "Cellar" / "mlx-serve" / "26.9.1" / "bin" / "mlx-serve"
    )
    # the prefix is a git repo, exactly as /opt/homebrew is
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=prefix, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "brew"],
        cwd=prefix,
        check=True,
        capture_output=True,
    )
    got = _engine_ident(str(prefix / "bin" / "mlx-serve"))
    assert "26.9.1" in got, got
    assert "(" not in got, f"a brew binary must carry no sha, got: {got}"


def test_a_source_build_is_stamped_with_its_sha(tmp_path):
    """A version string cannot distinguish two builds off the same branch.

    `--version` prints `mlx-serve 26.9.2-dev` for every build off main between
    releases -- main+PR383 and main without it are the same string. The sha is
    the only thing that separates them, and the rows already record it.
    """
    tree = tmp_path / "mlx-serve"
    binary = tree / "zig-out" / "bin"
    binary.mkdir(parents=True)
    exe = binary / "mlx-serve"
    exe.write_text("#!/bin/sh\necho 'mlx-serve 26.9.2-dev'\n")
    exe.chmod(0o755)
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "src"],
        cwd=tree,
        check=True,
        capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    got = _engine_ident(str(exe))
    assert sha in got, f"expected the sha {sha} in: {got}"
    assert "26.9.2-dev" in got, "the version string is still useful context"
