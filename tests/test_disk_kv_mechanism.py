"""The #112 disk-KV mechanism test, shell against port (#235, #112).

`vault/disk_kv_mechanism_test.sh` -> `scripts/disk_kv_mechanism.py`. The
differential runs both drivers against the same recording fakes and compares
what each hands its children.

**The experiment is on the SERVER command line, not run.py's.** There is one
arm here, and its whole content is `--kv-disk-space-mb 32768` where the
default is 8192. A differential that compared only `run.py` would be green
while the port started a server with the DeepSeek-sized budget -- the two
run.py argv are identical either way, because the arm is not named on that
command line at all. That is the failure mode `greedy_mtp_ab`'s differential
had until 4fd61f1: it filtered to `run.py` and the arms differed on the
ds4-server line. So the server argv is compared first here, and the budget is
asserted by value.

The name drops `_test` in the port because `testpaths = ["."]` makes pytest
collect every `*_test.py` in the tree; THIS file is the test, and it is named
for the port rather than the shell for the same reason.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "vault" / "disk_kv_mechanism_test.sh"

for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(ROOT / sub))

import disk_kv_mechanism as driver
import equiv
import tool_shim
import wait_ready

#: The shell hardcodes `--trials 3` and offers no way to change it. The port
#: takes `--trials`, defaulting to 3 -- an added capability, not a changed
#: default -- so the differential drives both at the shell's only value.
TRIALS = 3

# Bash and pytest each stamp the child env with interpreter artifacts -- PWD
# follows a `cd`, SHLVL counts nesting, `_` is the last command. They differ
# between the two sides and mean nothing to run.py.
# COLUMNS/LINES are terminal geometry, injected by bash when it has a tty and
# absent when it does not. They describe the window the comparison was run
# from, never the run, and they made these differentials fail on a machine
# whose shell reported a size ("run 1 env differs": COLUMNS=80, LINES=24).
_SHELL_ARTIFACTS = frozenset({"PWD", "OLDPWD", "SHLVL", "_", "COLUMNS", "LINES"})


def _meaningful_env(env: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in env.items()
        if k not in equiv.CONTROLLED_ENV_KEYS
        and k not in _SHELL_ARTIFACTS
        and k not in ("LOGDIR", "BENCH_LOGS")
    }


@contextlib.contextmanager
def _noop(*args, **kwargs):
    yield


def _fakes(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """The recording file and a PATH directory holding every fake child."""
    out = tmp_path / "rec.jsonl"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    equiv.write_uv_fake_running_real(shim_dir / "uv", out, ROOT)
    equiv.write_fake_pgrep(shim_dir)
    equiv.write_fake_pkill(shim_dir)
    equiv.write_fake_ds4_server(tmp_path / "home" / "git" / "ds4-metal", out, ROOT)
    return out, shim_dir


def _shell(tmp_path: pathlib.Path, out: pathlib.Path, shim_dir: pathlib.Path) -> None:
    """Run the real `.sh` against the fakes."""
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path / "home"),
            "EQUIV_OUT": str(out),
            "EQUIV_ARM": "shell",
            "LOGDIR": str(tmp_path / "logs"),
            "BENCH_LOGS": str(tmp_path / "bench-logs"),
        }
    )
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "bench-logs").mkdir(exist_ok=True)
    got = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert got.returncode == 0, f"shell failed:\n{got.stdout}\n{got.stderr}"


def _port(
    tmp_path: pathlib.Path,
    out: pathlib.Path,
    shim_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the port's `measure` against the same fakes.

    Real except where it would touch the machine. `serving` is NOT stubbed --
    it spawns the fake ds4-server, which is the child this differential is
    about. Only the shim probe and the readiness poll are replaced, and the
    home-derived constants move into tmp so the two server argv can agree.
    """
    home = tmp_path / "home"
    monkeypatch.setattr(tool_shim, "require_running", lambda port=0: "shim ok")
    monkeypatch.setattr(driver.tool_shim, "require_running", lambda port=0: "shim ok")
    monkeypatch.setattr(
        wait_ready, "ready", lambda *a, **k: equiv.wait_for_program(out, "ds4-server")
    )
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
    rc = driver.measure(
        TRIALS, driver.KV_MB, tmp_path / "logs", tmp_path / "bench-logs"
    )
    assert rc == 0, f"port measure failed rc={rc}"


def _one(invs: list[equiv.Invocation], program: str, arm: str) -> equiv.Invocation:
    got = [i for i in invs if i.program == program and i.arm == arm]
    assert len(got) == 1, f"{arm} recorded {len(got)} {program} invocations: {got}"
    return got[0]


def test_the_script_parses() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_the_shell_and_the_port_start_the_same_server(tmp_path, monkeypatch) -> None:
    """The comparison that carries the experiment.

    One arm, and its entire content is the raised budget. If the port lost
    `--kv-disk-space-mb 32768` this run would measure the 8192 default while
    reporting itself as the 32768 arm, and every other assertion in this file
    would still pass.
    """
    out, shim_dir = _fakes(tmp_path)
    _shell(tmp_path, out, shim_dir)
    _port(tmp_path, out, shim_dir, monkeypatch)
    invs = equiv.load(out)

    s = _one(invs, "ds4-server", "shell")
    p = _one(invs, "ds4-server", "port")
    assert set(equiv.canonical(s.argv, frozenset())) == set(
        equiv.canonical(p.argv, frozenset())
    ), f"shell server argv {s.argv} vs port {p.argv}"

    # By value, not just by agreement: two drivers that both dropped the flag
    # would agree with each other and measure the wrong thing.
    for argv in (s.argv, p.argv):
        i = argv.index("--kv-disk-space-mb")
        assert argv[i + 1] == str(driver.KV_MB) == "32768", (
            f"the arm is the budget, and it reads {argv[i + 1]}"
        )
    assert "--warm-weights" in s.argv and "--warm-weights" in p.argv
    assert _meaningful_env(s.env) == _meaningful_env(p.env), (
        f"shell server env {_meaningful_env(s.env)} vs port {_meaningful_env(p.env)}"
    )


def test_the_shell_and_the_port_hand_run_py_the_same_command(
    tmp_path, monkeypatch
) -> None:
    """One `run.py` invocation each: arm A, three trials, one continuous server.

    No `--restart-between-trials` on either side. That is the experiment's
    control: the whole question is whether the raised budget removes the
    trial-3 decline WITHOUT a restart, so a driver that quietly restarted
    would answer a different question.
    """
    out, shim_dir = _fakes(tmp_path)
    _shell(tmp_path, out, shim_dir)
    _port(tmp_path, out, shim_dir, monkeypatch)
    invs = equiv.load(out)

    s = _one(invs, "run.py", "shell")
    p = _one(invs, "run.py", "port")
    assert set(equiv.canonical(s.argv, frozenset())) == set(
        equiv.canonical(p.argv, frozenset())
    ), f"shell argv {s.argv} vs port argv {p.argv}"
    assert _meaningful_env(s.env) == _meaningful_env(p.env), (
        f"shell env {_meaningful_env(s.env)} vs port env {_meaningful_env(p.env)}"
    )
    # "No restart between trials" is not a flag -- `run.py` declares none, and
    # `restart_between_trials.sh` implements it by wrapping run.py in a loop
    # that restarts the server between invocations. So the invariant is a
    # COUNT: all three trials inside ONE run.py invocation, against ONE server
    # start. Asserting `"--restart-between-trials" not in argv` would have
    # looked like this check and been vacuous -- the flag cannot appear,
    # whatever either driver does.
    for arm in ("shell", "port"):
        runs = [i for i in invs if i.program == "run.py" and i.arm == arm]
        servers = [i for i in invs if i.program == "ds4-server" and i.arm == arm]
        assert len(runs) == 1, f"{arm} split the trials across {len(runs)} runs"
        assert len(servers) == 1, f"{arm} started {len(servers)} servers, not one"
        assert runs[0].argv[runs[0].argv.index("--trials") + 1] == str(TRIALS)


def test_the_ported_defaults_are_the_values_the_shell_hardcoded(monkeypatch) -> None:
    """The port gained two knobs. Neither may have moved the arm underneath it.

    `--trials 3` is not incidental: the decline this test hunts appears ON
    trial 3, and every baseline it is compared against is a per-trial triple.
    `--kv-disk-space-mb 32768` IS the treatment. A port that defaulted either
    one lower would still agree with the shell in a differential that passed
    both explicitly -- which is what the two comparisons above do -- and would
    answer a different question when run the way an operator runs it, with no
    arguments at all.
    """
    seen: dict[str, object] = {}

    def capture(trials, kv_mb, logdir, bench_logs):
        seen.update(trials=trials, kv_mb=kv_mb)
        return 0

    monkeypatch.setattr(driver, "measure", capture)
    assert driver.main([]) == 0
    assert seen == {"trials": 3, "kv_mb": 32768}, (
        f"a bare run is not the shell's arm: {seen}"
    )


def test_every_flag_the_port_passes_run_py_is_one_run_py_declares(tmp_path) -> None:
    """#264. The shell once passed `--skip-tensor-gate` after the port removed
    it: argparse exited 2 under a `|| echo returned non-zero`, and the batch
    wrote zero rows with a clean exit code."""
    equiv.assert_flags_declared(driver.run_argv(3), equiv.declared_run_flags())


def test_the_port_refuses_before_it_starts_a_server(tmp_path, monkeypatch) -> None:
    """A missing shim must cost nothing. The shell checked `pgrep -f
    qwen_tool_shim`, which says a process exists somewhere and nothing about
    :8101; the port asks who holds the port. Either way the refusal has to
    come BEFORE the 74 GiB load, or a wrong prerequisite costs ten minutes.
    """
    out, shim_dir = _fakes(tmp_path)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("EQUIV_OUT", str(out))
    monkeypatch.setenv("EQUIV_ARM", "port")

    def refuse(port: int = 0) -> str:
        raise tool_shim.NotServing("nothing is serving :8101")

    monkeypatch.setattr(driver.tool_shim, "require_running", refuse)
    assert driver.main(["--logdir", str(tmp_path / "logs")]) == 1
    assert not out.exists(), "a refusal must not have started anything"


def test_the_collect_filter_is_a_deviation_and_is_written_down() -> None:
    """The one place the port does NOT match the shell, stated rather than hidden.

    `mv "$BENCH_LOGS"/*qwen38fnds4shim-opencode-*` moves every transcript that
    matches, whenever it was written. A run killed before its own move leaves
    transcripts behind and the next run of the same arm claims them --
    `old-sweep1` once held 22 transcripts for a 15-task sweep. The port filters
    on mtime against the run's own start.

    Asserted here so the deviation cannot be quietly removed as a "fix" that
    restores the shell's behaviour.
    """
    source = (ROOT / "scripts" / "disk_kv_mechanism.py").read_text()
    assert "st_mtime <= since" in source
    assert "old-sweep1" in source, "the deviation must keep the case that motivated it"


def test_the_shell_it_replaces_is_still_here() -> None:
    """When the shell goes, this file's shell half goes with it."""
    assert SCRIPT.exists()
