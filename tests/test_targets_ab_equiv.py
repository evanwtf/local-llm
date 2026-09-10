"""The #146 targets A/B, shell against port (#235, #146).

`vault/targets_ab.sh` -> `scripts/targets_ab.py`, which routes its
measurement through `scripts/lib/batch.py`. Both drivers run against the same
recording fakes and what each hands its children is compared.

Three things carry this differential, and only the first is the usual one.

**The arm order, as a SEQUENCE.** The shell indexes a literal
`ORDER=(legacy sandbox sandbox legacy)` by `(n-1) % 4`; the port calls
`batchlib.order(ARMS, runs)`. Whichever arm runs first is faster in 9 of 12
reps (#130, #201), so a differential that compared the arms as a SET would be
green on a reversed order while measuring the position term instead of the
layout.

**The shim's absent variable.** The shell starts the shim under
`env -u SHIM_NO_STRIP`. That is an arm defined by an absence, which a merged
dict cannot express: a key a dict does not mention is INHERITED, so an
operator with `SHIM_NO_STRIP=1` exported would hand the off mode to the on
arm. The strip is worth 23 points of pass rate (#112) -- more than this
experiment is trying to see -- so the arm would be swamped and the run would
look valid. The port must REMOVE the key, not merely decline to set it, and
this file asserts the removal reaches the child's environment.

**`--require-harness-head`.** Both sides pin the harness commit onto every
row. A port that dropped it would still produce rows; they would just no
longer say which harness wrote them, which is the failure that cannot be
repaired after the fact.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "vault" / "targets_ab.sh"

for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(ROOT / sub))

import equiv
import targets_ab as driver
import tool_shim
import wait_ready

RUNS = 4
BATCH = "146-equiv"

_SHELL_ARTIFACTS = frozenset({"PWD", "OLDPWD", "SHLVL", "_"})


#: The variable the strip-on arm is defined by REMOVING, read out of the
#: SHELL, which is the side that states it: `env -u SHIM_NO_STRIP`.
#:
#: Taking it from the shell rather than from `tool_shim.strip_env` is the
#: whole point. The port is the thing under test, so a constant derived from
#: the port would move with it -- and worse, an earlier version derived it as
#: `strip_env(True)[1][0]`, which turned the exact defect this file exists to
#: catch into an `IndexError` during COLLECTION. Every other test in the file
#: then failed to run, and the reader got "tuple index out of range" instead
#: of a sentence about the arm. A differential must not be disarmed by the bug
#: it is hunting.
def _shell_unsets() -> tuple[str, ...]:
    """Every variable the shell removes before starting the shim."""
    found = []
    for line in SCRIPT.read_text().splitlines():
        parts = line.split()
        for i, token in enumerate(parts):
            if token == "-u" and i + 1 < len(parts) and parts[i - 1] == "env":
                found.append(parts[i + 1])
    return tuple(found)


NO_STRIP = _shell_unsets()[0]


def _meaningful_env(env: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in env.items()
        if k not in equiv.CONTROLLED_ENV_KEYS
        and k not in _SHELL_ARTIFACTS
        and k not in ("LOGDIR", "BENCH_LOGS", "RESULTS", "MANIFEST", "BATCH")
    }


#: The harness commit both sides pin. A literal, not this checkout's real
#: HEAD: the shell reads it with `git rev-parse` and the port through
#: `provenance.head`, and a differential that let each side ask the machine
#: would agree because both asked the same machine, not because they agree.
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"


def _fake_git(directory: pathlib.Path) -> pathlib.Path:
    """A `git` that reports a clean tree at HEAD_SHA, and refuses anything else.

    The shell asks git two questions -- `rev-parse HEAD` and
    `status --porcelain` -- and refuses to start on a dirty tree. THIS FILE
    makes the tree dirty while it is being written, so a differential that let
    the real git answer would pass only on a clean checkout: green in CI, red
    for the person editing it, which is precisely when it is needed.

    Fail-closed, like `write_uv_fake_running_real`: an unrecognised git
    subcommand exits non-zero rather than falling through to the real one. A
    fake that silently delegates is a fake that can reach the operator's repo.
    """
    exe = directory / "git"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "a = [x for x in sys.argv[1:] if x != '-C' ]\n"
        "args = sys.argv[1:]\n"
        "if 'rev-parse' in args and 'HEAD' in args:\n"
        f"    print({HEAD_SHA!r}); sys.exit(0)\n"
        "if 'status' in args and '--porcelain' in args:\n"
        "    sys.exit(0)\n"
        "sys.stderr.write('fake git refuses: %s\\n' % ' '.join(args))\n"
        "sys.exit(2)\n"
    )
    exe.chmod(0o755)
    return exe


def _fakes(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    out = tmp_path / "rec.jsonl"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    equiv.write_uv_fake_running_real(shim_dir / "uv", out, ROOT, shim=True)
    equiv.write_fake_pgrep(shim_dir)
    equiv.write_fake_pkill(shim_dir)
    _fake_git(shim_dir)
    equiv.write_fake_ds4_server(tmp_path / "home" / "git" / "ds4-metal", out, ROOT)
    return out, shim_dir


def _own_server_barrier(out: pathlib.Path, arm: str):
    """A readiness stub that waits for THIS side's next server, not any server.

    `equiv.wait_for_program(out, "ds4-server")` returns as soon as one record
    exists -- and by the time the port runs, the shell has already written
    four. The stub was therefore satisfied instantly and the port's grep raced
    its own fake server's log.

    In the unmutated case that race happened to be won. It was a mutation that
    exposed it: reversing the arm order made the port name a different log,
    and `ServerNeverStarted: no 'Qwen graph allocated' line` came out instead
    of the order mismatch the mutation was testing for. **A differential that
    can pass on timing is not evidence**, whatever it says when it is green.

    So the barrier counts records belonging to this arm and waits for one
    more than it has already seen.
    """
    seen = 0

    def ready(*args: object, **kwargs: object) -> bool:
        nonlocal seen
        want = seen + 1
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            got = sum(
                1 for i in equiv.load(out) if i.program == "ds4-server" and i.arm == arm
            )
            if got >= want:
                seen = got
                return True
            time.sleep(0.02)
        raise AssertionError(
            f"the {arm} side's server {want} never recorded itself; the "
            "readiness barrier would have let the driver grep an empty log"
        )

    return ready


def _paths(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    return tmp_path / "res.jsonl", tmp_path / "res-manifest.jsonl"


def _shell(tmp_path, out, shim_dir, *, no_strip_exported: bool = False) -> None:
    results, manifest = _paths(tmp_path)
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path / "home"),
            "EQUIV_OUT": str(out),
            "EQUIV_ARM": "shell",
            "LOGDIR": str(tmp_path / "logs"),
            "BENCH_LOGS": str(tmp_path / "bench-logs"),
            "RESULTS": str(results),
            "MANIFEST": str(manifest),
            "BATCH": BATCH,
        }
    )
    if no_strip_exported:
        # The operator's shell, not the driver's. The arm is defined by this
        # being absent in the CHILD.
        env[NO_STRIP] = "1"
    # Never inherited, always removed. The port sets EQUIV_SHIM_HOLD_S with
    # monkeypatch, which mutates this process's own environ, so a second
    # fixture's shell would pick up the first fixture's hold -- and did: the
    # exported fixture ran in 61.7s against the default fixture's 3.4s. The
    # shell must not hold whatever the ambient environment says.
    env.pop("EQUIV_SHIM_HOLD_S", None)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "bench-logs").mkdir(exist_ok=True)
    got = subprocess.run(
        ["bash", str(SCRIPT), str(RUNS)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert got.returncode == 0, f"shell failed:\n{got.stdout}\n{got.stderr}"


def _port(tmp_path, out, shim_dir, monkeypatch, *, no_strip_exported=False) -> None:
    home = tmp_path / "home"
    results, manifest = _paths(tmp_path)
    monkeypatch.setattr(driver, "sync_targets", lambda: None)
    monkeypatch.setattr(driver.provenance, "code_is_dirty", lambda *a, **k: False)
    monkeypatch.setattr(driver.provenance, "head", lambda *a, **k: _head())
    monkeypatch.setattr(
        driver.preflight, "acquire_lock", lambda *a, **k: (True, "ours")
    )
    monkeypatch.setattr(
        driver.preflight, "release_lock", lambda *a, **k: (True, "released")
    )
    monkeypatch.setattr(wait_ready, "ready", _own_server_barrier(out, "port"))
    # The SPAWN is real and the POLL is stubbed, which is the shape every
    # differential here uses. `tool_shim.serving` starts the shim through
    # `unitctl.start`, so the fake records the argv and env the port actually
    # hands it -- that IS the comparison. What it cannot do is answer a TCP
    # connect on :8101, and binding a real port in a test would collide with
    # a shim the machine may be running for something else.
    #
    # Only `ports.answers` is replaced. `_wait` still requires the unit to be
    # RUNNING (which is why the fake holds) and still reads the mode line out
    # of the log, so the strip-ON assertion remains a real check against the
    # recorded fixture rather than something this stub waves through.
    monkeypatch.setattr(tool_shim.ports, "answers", lambda *a, **k: True)
    monkeypatch.setattr(driver, "DS4_TREE", home / "git" / "ds4-metal")
    models = home / "models" / "qwen3.8-flash-next-ds4-q4"
    monkeypatch.setattr(
        driver,
        "DS4_MODEL",
        models
        / (
            "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-"
            "Q8QSA-Q8Shared-Q8Out.gguf"
        ),
    )
    monkeypatch.setattr(driver, "DS4_PLE", models / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf")
    monkeypatch.setattr(driver, "DS4_KV", home / ".ds4" / "server-kv")
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EQUIV_OUT", str(out))
    monkeypatch.setenv("EQUIV_ARM", "port")
    monkeypatch.setenv("BENCH_LOGS", str(tmp_path / "bench-logs"))
    # The port SUPERVISES the shim as a unit and polls it, so the fake must
    # still be running when `_wait` looks. The shell must NOT set this: it
    # backgrounds the shim, and a `capture_output` subprocess around it does
    # not return until the pipe's last holder exits -- a 60s hold made the
    # shell half of this differential take 61.5s instead of 3.1s.
    monkeypatch.setenv("EQUIV_SHIM_HOLD_S", "60")
    if no_strip_exported:
        monkeypatch.setenv(NO_STRIP, "1")
    else:
        monkeypatch.delenv(NO_STRIP, raising=False)
    rc = driver.sweep(
        RUNS,
        None,
        results,
        manifest,
        tmp_path / "logs",
        BATCH,
        dry=False,
        owner_pid=os.getpid(),
    )
    assert rc == 0, f"port sweep failed rc={rc}"


def _head() -> str:
    return HEAD_SHA


class Recording:
    """One shell run and one port run, and everything they recorded.

    Module-scoped, because a driver pair is the expensive part: each side
    starts a shim, restarts a fake server four times and spawns four fake
    measurements. Re-running that per test turned a differential into a
    two-minute one. The recording is read-only afterwards, so sharing it is
    safe and every assertion below reads the same pair of runs -- which is
    also the honest thing: they are assertions ABOUT one comparison, not seven
    independent comparisons.
    """

    def __init__(self, invs, manifest_rows):
        self.invs = invs
        self.shell_manifest, self.port_manifest = manifest_rows

    def runs(self, arm: str) -> list[equiv.Invocation]:
        return [i for i in self.invs if i.program == "run.py" and i.arm == arm]

    def servers(self, arm: str) -> list[equiv.Invocation]:
        return [i for i in self.invs if i.program == "ds4-server" and i.arm == arm]

    def shims(self) -> list[equiv.Invocation]:
        return [i for i in self.invs if "shim" in i.program]


def _record(tmp_path, monkeypatch, **kw) -> Recording:
    out, shim_dir = _fakes(tmp_path)
    _, manifest = _paths(tmp_path)

    _shell(tmp_path, out, shim_dir, **kw)
    shell_rows = _manifest_rows(manifest)
    manifest.unlink()

    _port(tmp_path, out, shim_dir, monkeypatch, **kw)
    port_rows = _manifest_rows(manifest)
    return Recording(equiv.load(out), (shell_rows, port_rows))


def _manifest_rows(manifest: pathlib.Path) -> list[dict]:
    if not manifest.exists():
        return []
    return [json.loads(x) for x in manifest.read_text().splitlines() if x.strip()]


@pytest.fixture(scope="module")
def rec(tmp_path_factory, request):
    """The default comparison: `SHIM_NO_STRIP` absent from the operator's shell."""
    return _shared(tmp_path_factory, request, no_strip_exported=False)


@pytest.fixture(scope="module")
def rec_exported(tmp_path_factory, request):
    """The same pair, with `SHIM_NO_STRIP=1` exported by the operator."""
    return _shared(tmp_path_factory, request, no_strip_exported=True)


def _shared(tmp_path_factory, request, *, no_strip_exported: bool) -> Recording:
    # A module-scoped fixture cannot take the function-scoped `monkeypatch`,
    # and undoing by hand is how a patch leaks into the next module. This is
    # pytest's own module-scoped context, torn down with the fixture.
    mp = pytest.MonkeyPatch()
    request.addfinalizer(mp.undo)
    tmp = tmp_path_factory.mktemp(
        "targets-exported" if no_strip_exported else "targets"
    )
    return _record(tmp, mp, no_strip_exported=no_strip_exported)


def _targets_of(inv: equiv.Invocation) -> str:
    return inv.argv[inv.argv.index("--targets") + 1]


def test_the_script_parses() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_the_shell_and_the_port_hand_run_py_the_same_command(rec) -> None:
    s, p = rec.runs("shell"), rec.runs("port")
    assert len(s) == len(p) == RUNS, f"shell {len(s)} runs, port {len(p)}"
    for i, (a, b) in enumerate(zip(s, p, strict=True), 1):
        assert set(equiv.canonical(a.argv, frozenset())) == set(
            equiv.canonical(b.argv, frozenset())
        ), f"run {i}: shell {a.argv} vs port {b.argv}"
        assert _meaningful_env(a.env) == _meaningful_env(b.env), f"run {i} env differs"


def test_the_arms_run_in_the_same_ORDER_not_merely_the_same_set(rec) -> None:
    """A B B A, position by position.

    Whichever arm runs first is faster in 9 of 12 reps, median +0.9% and +5.9%
    on the first rep of a cold session (#130, #201). Alternation cancels that
    only if both sides alternate the SAME way, so this compares the sequence.
    A set comparison is green on `sandbox legacy legacy sandbox`, which
    measures the position term and calls it the layout.
    """
    shell_order = [_targets_of(i) for i in rec.runs("shell")]
    port_order = [_targets_of(i) for i in rec.runs("port")]
    assert shell_order == port_order, f"shell {shell_order} vs port {port_order}"
    assert shell_order == ["legacy", "sandbox", "sandbox", "legacy"], shell_order
    assert shell_order.count("legacy") == shell_order.count("sandbox") == 2, (
        "each arm must lead equally often or the position term does not cancel"
    )


def test_both_sides_pin_the_harness_commit_onto_every_row(rec) -> None:
    """A row that does not say which harness wrote it cannot be repaired later."""
    head = _head()
    for arm in ("shell", "port"):
        for inv in rec.runs(arm):
            assert "--require-harness-head" in inv.argv, f"{arm} dropped the pin"
            got = inv.argv[inv.argv.index("--require-harness-head") + 1]
            assert got == head, f"{arm} pinned {got}, not {head}"
            assert "--no-lock" in inv.argv, f"{arm} let run.py take the lock"


def test_the_shell_and_the_port_start_the_same_servers(rec) -> None:
    s, p = rec.servers("shell"), rec.servers("port")
    assert len(s) == len(p) == RUNS, "one fresh server per run, both sides"
    assert {equiv.canonical(i.argv, frozenset()) for i in s} == {
        equiv.canonical(i.argv, frozenset()) for i in p
    }


def test_strip_on_is_defined_by_removing_a_variable() -> None:
    """The invariant every other strip assertion in this file rests on.

    `strip_env(True)` must REMOVE a key, not merely decline to set one. Its own
    docstring says why: the shim tests the variable's presence, so an operator
    with it exported hands the off mode to the on arm.

    Stated as its own test because the module-level constant cannot state it.
    Deriving `NO_STRIP` from an empty tuple raises `IndexError` during
    collection, and a reader then sees "tuple index out of range" rather than
    the sentence above -- a real failure reported as a broken test file.
    """
    assert _shell_unsets() == ("SHIM_NO_STRIP",), (
        f"the shell's `env -u` list changed: {_shell_unsets()}"
    )
    set_env, unset = tool_shim.strip_env(True)
    assert NO_STRIP in unset, (
        f"the shell removes {NO_STRIP} and the port's strip_env does not: "
        f"set={set_env} unset={unset}. The shim tests the variable's PRESENCE, "
        "so an operator with it exported hands the off mode to the on arm."
    )
    assert NO_STRIP not in set_env, (
        "removing and setting are not the same: an empty value is still present, "
        "and the shim reads presence"
    )
    off_set, off_unset = tool_shim.strip_env(False)
    assert off_set.get(NO_STRIP) == "1" and not off_unset, (off_set, off_unset)


def test_the_strip_on_arm_removes_SHIM_NO_STRIP_when_nobody_exported_it(rec) -> None:
    """The easy half: neither side may SET the key."""
    assert rec.shims(), "neither side started the shim"
    for inv in rec.shims():
        assert NO_STRIP not in inv.env, f"{inv.arm} set {NO_STRIP}"


def test_the_strip_on_arm_removes_SHIM_NO_STRIP_the_operator_exported(
    rec_exported,
) -> None:
    """The half that matters, and the reason `unset` exists at all.

    With `SHIM_NO_STRIP=1` in the operator's own shell, a port that merely
    declines to SET the key INHERITS it: a merged dict has no way to say "not
    set", and a key it does not mention is passed straight through. The shim
    would then run in the off mode while the driver reported strip ON.

    The strip is worth 23 points of pass rate (#112) -- far more than the
    layout effect this experiment is trying to measure -- so the arms would be
    swamped and the batch would still look valid. The shell said
    `env -u SHIM_NO_STRIP`; this asserts the port's removal reaches the
    child's environment, which is the only place it counts.
    """
    assert rec_exported.shims(), "neither side started the shim"
    for inv in rec_exported.shims():
        assert NO_STRIP not in inv.env, (
            f"{inv.arm} handed the shim {NO_STRIP}="
            f"{inv.env.get(NO_STRIP)!r} that the operator exported; strip-on "
            "is defined by that key being ABSENT, not empty"
        )


def test_the_manifest_records_one_row_per_run_on_both_sides(rec) -> None:
    """The manifest is how a read-out finds the runs. Same count, same arms."""
    shell_rows, port_rows = rec.shell_manifest, rec.port_manifest
    assert len(shell_rows) == len(port_rows) == RUNS
    assert [r["arm"] for r in shell_rows] == [r["arm"] for r in port_rows]
    assert [r["run"] for r in shell_rows] == [r["run"] for r in port_rows]
    assert [r["run"] for r in shell_rows] == list(range(1, RUNS + 1))


def test_every_flag_the_port_passes_run_py_is_one_run_py_declares() -> None:
    """#264."""
    import batch as batchlib

    b = batchlib.Batch(
        repo=ROOT,
        results=ROOT / "x.jsonl",
        manifest=ROOT / "x-manifest.jsonl",
        logdir=ROOT,
        bench_logs=ROOT,
        batch=BATCH,
        harness_head="abc1234",
        backend=driver.BACKEND,
        prefix=driver.PREFIX,
    )
    equiv.assert_flags_declared(
        batchlib.argv(b, ["--targets", "sandbox"]), equiv.declared_run_flags()
    )


def test_the_shell_it_replaces_is_still_here() -> None:
    assert SCRIPT.exists()
