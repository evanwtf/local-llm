"""The first ds4 MTP arm that can draft, and its control (#151, #39).

Every MTP row this project has published was taken on an arm that never
speculated: ds4 reaches its Qwen MTP path only at `temperature <= 0.0f` and
OpenCode sends no temperature. This driver runs the arm that can, beside the
control that says what pinning the temperature costs on its own.

Text invariants, in the shape `test_reps_parity.py` and
`test_iso8601_timestamps.py` already use on the shell runners: the script
needs a 74 GiB model and an M5 to execute, and what rots here is the argv and
the ordering.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "greedy_mtp_ab.sh"


def body() -> str:
    return SCRIPT.read_text()


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_both_arms_run_and_the_control_is_not_optional():
    """Pinning the temperature is itself a change of regime. A greedy MTP arm
    alone cannot separate speculation from greedy decoding, which is the same
    confound #39 exists to remove."""
    text = body()
    assert "qwen38fnds4mtp7greedy" in text
    assert "qwen38fnds4greedy" in text
    assert text.count("qwen38fnds4mtp7greedy") >= 2
    assert text.count("qwen38fnds4greedy") >= 2


def test_the_arms_alternate_and_an_odd_round_count_is_refused():
    """Whichever arm runs first is faster in 9 of 12 reps, median +0.9% and
    +5.9% on the first rep of a cold session (#130, #201). Alternation
    cancels it only on an even count."""
    text = body()
    assert "ROUNDS % 2" in text
    assert "ALLOW_ODD_ROUNDS" in text
    assert 'order=("mtp:qwen38fnds4mtp7greedy" "plain:qwen38fnds4greedy")' in text
    assert 'order=("plain:qwen38fnds4greedy" "mtp:qwen38fnds4mtp7greedy")' in text


def test_the_greedy_shim_runs_on_its_own_port():
    """:8101 serves every other backend. A shim pinning temperature there
    would silently make 262 rows' worth of history stop comparing."""
    text = body()
    assert "SHIM_TEMPERATURE=0" in text
    assert "--port 8102" in text
    # It must never start or kill a shim on :8101 -- only mention it in prose.
    commands = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in commands if "8101" in line]


def test_the_greedy_shim_is_stopped_on_every_exit_path():
    """A leftover shim pinning temperature makes the NEXT run greedy, and
    nothing in its rows would say so."""
    text = body()
    assert "stop_greedy_shim" in text
    assert "trap" in text and "stop_greedy_shim" in text.split("trap")[1]


def test_the_two_arms_use_their_own_kv_directories():
    """The MTP and non-MTP KV formats are incompatible and ds4 rejects the
    other's checkpoints, so a shared directory leaves one arm re-prefilling
    and the only symptom is that it looks slower."""
    text = body()
    assert "server-kv-mtp" in text
    assert 'KV_PLAIN="$HOME/.ds4/server-kv"' in text


def test_each_arm_asserts_its_own_graph_line_before_a_trial():
    """`MTP=off` against `MTP=Q4_K/...` is the only place the two server
    configurations differ before the counters are read."""
    text = body()
    assert "Qwen graph allocated" in text
    assert "MTP arm reports MTP=off" in text
    assert "control arm loaded an MTP head" in text


def test_both_arms_read_the_counters():
    """#148: a predecessor passed --mtp-timing and no --server-log for three
    cycles, writing every counter to a file nothing read."""
    text = body()
    assert text.count("--server-log") >= 1
    assert "--draft-log-engine ds4" in text


def test_the_lock_is_held_across_the_whole_cycle():
    """#133: the window the lock exists for is the gap between arms, where
    the server is deliberately down and a process scan reports all clear."""
    text = body()
    assert "--acquire-lock" in text
    assert "--release-lock" in text
    assert "--no-lock" in text, "run.py is told the script already holds it"


def test_the_readout_warns_that_a_declaration_is_not_a_treatment():
    """`pinned_temperature` is what someone wrote in tasks.toml. The rows are
    what the engine did."""
    assert "not an MTP arm, whatever it declared" in body()


def test_the_control_arms_empty_argv_survives_set_u():
    """2026-09-08: the control arm never started. `set -u` on bash 3.2 calls an
    empty array's expansion unbound, and only the control arm's is empty -- so
    the treatment arm ran all 15 tasks and its pair never existed. The run cost
    an hour and produced no comparison."""
    text = body()
    assert re.search(r"^set -[a-z]*u", text, re.MULTILINE), "nounset is on"
    assert '${mtp_args[@]+"${mtp_args[@]}"}' in text, (
        "an empty array must expand to nothing, not to an unbound-variable error"
    )
    assert '"${mtp_args[@]}" \\' not in text


def test_a_missing_graph_line_is_not_reported_as_an_mtp_head():
    """The same failure printed `REFUSING: control arm loaded an MTP head`,
    which is the opposite of what happened: there was no server and no log. A
    diagnostic that names the wrong cause is worse than none."""
    text = body()
    assert '[ -n "$line" ]' in text
    absent = text.index('[ -n "$line" ]')
    loaded = text.index("control arm loaded an MTP head")
    assert absent < loaded, "check for an absent line before judging its content"


def test_the_batch_label_can_be_overridden_for_a_re_run():
    """A broken run leaves rows behind. Re-running under the same batch label
    pools them with the repaired run, and the read-out then pairs a treatment
    arm against a control from a different hour and a different server process."""
    text = body()
    assert 'BATCH="${BATCH:-greedy-mtp-ab}"' in text
    assert '--batch "$BATCH"' in text
    assert '--batch "greedy-mtp-ab"' not in text


# ------------------------------------------------- the #235 retirement differential
#
# The port's claim is that, under identical inputs, it hands the measurement
# child (`run.py`) the same argv and the same environment the shell did. The
# shell's `run_arm` and the port's `run_arm` both invoke
# `uv run python benchmarks/agent/run.py`; a fake `uv` on PATH records the
# child's argv+env. The real `.sh` and the port's `sweep` run against the same
# fake, and the recordings must agree on argv (order-free) and on env modulo
# the controlled base.
#
# The two arms differ only in argv (`--backend`, `--server-log`); there is no
# arm-specific env var, so the env assertion is that both sides hand run.py the
# same base environment. The tag lives in the `--server-log` path, so the shell
# and port recordings are matched by that token.

import contextlib
import os
import sys

import pytest

for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(ROOT / sub))

import equiv
import greedy_mtp_ab as driver
import wait_ready

BATCH = "greedy-mtp-ab"
ROUNDS = 2
TRIALS = 1

# Bash and pytest both stamp the child env with variables that are artifacts of
# the interpreter, not of the driver: PWD follows the `cd`, SHLVL counts shell
# nesting, `_` is the last command. They differ between the two sides and mean
# nothing to run.py, so the env comparison ignores them.
_SHELL_ARTIFACTS = frozenset({"PWD", "OLDPWD", "SHLVL", "_"})


@contextlib.contextmanager
def _noop(*args, **kwargs):
    """A server, shim, or lock that is already in the state the driver wants."""
    yield


def _meaningful_env(env: dict[str, str]) -> dict[str, str]:
    """The env minus the controlled base and the interpreter artifacts."""
    return {
        k: v
        for k, v in env.items()
        if k not in equiv.CONTROLLED_ENV_KEYS and k not in _SHELL_ARTIFACTS
    }


def _shell_run_invs(
    tmp_path: pathlib.Path, out: pathlib.Path, shim_dir: pathlib.Path
) -> list[equiv.Invocation]:
    """Run the real `.sh` against the fakes; return the run.py recordings."""
    tree = tmp_path / "home" / "git" / "ds4-metal"
    equiv.write_fake_ds4_server(tree, out, ROOT)
    equiv.write_fake_pgrep(shim_dir)
    equiv.write_fake_pkill(shim_dir)
    logdir = tmp_path / "logs"
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{shim_dir}:{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path / "home"),
            "EQUIV_OUT": str(out),
            "EQUIV_ARM": "shell",
            "LOGDIR": str(logdir),
            "BATCH": BATCH,
            "ROUNDS": str(ROUNDS),
            "TRIALS": str(TRIALS),
        }
    )
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
    return equiv.by_program(equiv.load(out), "run.py")


def _port_run_invs(
    tmp_path: pathlib.Path,
    out: pathlib.Path,
    shim_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[equiv.Invocation]:
    """Run the port's `sweep` against the same fake; return the run.py recordings.

    The port's orchestration is real except where it would touch the machine:
    the lock and the shim are no-ops, and the home-derived constants move into
    tmp so the server argv matches the shell's. `serving` is NOT stubbed: it
    spawns the fake ds4-server, which records its argv+env and writes the graph
    line to the log. Only the readiness poll is stubbed -- it waits for the
    fake's record instead of polling a real port. `run_arm` still spawns
    `uv run python run.py` through the fake `uv` on PATH.
    """
    home = tmp_path / "home"
    monkeypatch.setattr(driver, "run_lock", _noop)
    monkeypatch.setattr(driver, "greedy_shim", _noop)
    monkeypatch.setattr(
        wait_ready, "ready", lambda *a, **k: equiv.wait_for_program(out, "ds4-server")
    )
    # The shell resolves these against $HOME; the port computed them at import
    # from the real home. Point them at the tmp home so the server argv agrees.
    monkeypatch.setattr(driver, "DS4_TREE", home / "git" / "ds4-metal")
    monkeypatch.setattr(
        driver,
        "DS4_MODEL",
        home
        / "models"
        / "qwen3.8-flash-next-ds4-q4"
        / "Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf",
    )
    monkeypatch.setattr(
        driver,
        "DS4_PLE",
        home
        / "models"
        / "qwen3.8-flash-next-ds4-q4"
        / "Qwen3.8-Flash-Next-PLE-Q4_1.gguf",
    )
    monkeypatch.setattr(
        driver,
        "DS4_MTP",
        home
        / "models"
        / "qwen3.8-flash-next-ds4-q4"
        / "qwen3.8-flash-next-q4-mtp.gguf",
    )
    monkeypatch.setattr(driver, "KV_MTP", home / ".ds4" / "server-kv-mtp")
    monkeypatch.setattr(driver, "KV_PLAIN", home / ".ds4" / "server-kv")
    # The port's process env must carry the same driver vars the shell's did,
    # so run.py sees the same base environment on both sides.
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EQUIV_OUT", str(out))
    monkeypatch.setenv("EQUIV_ARM", "port")
    monkeypatch.setenv("LOGDIR", str(tmp_path / "logs"))
    monkeypatch.setenv("BATCH", BATCH)
    monkeypatch.setenv("ROUNDS", str(ROUNDS))
    monkeypatch.setenv("TRIALS", str(TRIALS))
    rc = driver.sweep(ROUNDS, TRIALS, BATCH, tmp_path / "logs", os.getpid())
    assert rc == 0, f"port sweep failed rc={rc}"
    return equiv.by_program(equiv.load(out), "run.py")


def _server_log(inv: equiv.Invocation) -> str:
    return inv.argv[inv.argv.index("--server-log") + 1]


def test_the_shell_and_the_port_hand_run_py_the_same_command(
    tmp_path, monkeypatch
) -> None:
    """The measurement child's argv and env agree between the two drivers.

    The tag-bearing `--server-log` token matches a shell recording to its port
    twin. The argv must agree order-free; the env must agree modulo the
    controlled base and the interpreter artifacts.
    """
    out = tmp_path / "rec.jsonl"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    equiv.write_uv_fake_running_real(shim_dir / "uv", out, ROOT)

    shell = _shell_run_invs(tmp_path, out, shim_dir)
    port = _port_run_invs(tmp_path, out, shim_dir, monkeypatch)

    shell_by_log = {_server_log(i): i for i in shell}
    port_by_log = {_server_log(i): i for i in port}
    assert shell_by_log.keys() == port_by_log.keys(), (
        f"shell tags {sorted(shell_by_log)} vs port tags {sorted(port_by_log)}"
    )

    for tag, s in shell_by_log.items():
        p = port_by_log[tag]
        assert set(equiv.canonical(s.argv, frozenset())) == set(
            equiv.canonical(p.argv, frozenset())
        ), f"{tag}: shell argv {s.argv} vs port argv {p.argv}"
        assert _meaningful_env(s.env) == _meaningful_env(p.env), (
            f"{tag}: shell env {_meaningful_env(s.env)} vs "
            f"port env {_meaningful_env(p.env)}"
        )

    # The treatment lives on the SERVER command line, not run.py's: the mtp
    # arm carries --mtp-model/--mtp-draft/--mtp-timing, the plain arm carries
    # none. A differential that compared only run.py would be green while the
    # two drivers started different servers. The server argv is identical
    # across both rounds of an arm (same KV dir, same flags), so the two sides
    # are compared as sets of canonical argv, not round-by-round.
    servers = equiv.by_program(equiv.load(out), "ds4-server")
    srv_shell = {
        equiv.canonical(i.argv, frozenset()) for i in servers if i.arm == "shell"
    }
    srv_port = {
        equiv.canonical(i.argv, frozenset()) for i in servers if i.arm == "port"
    }
    assert len(srv_shell) == 2, f"shell recorded {len(srv_shell)} distinct server argv"
    assert len(srv_port) == 2, f"port recorded {len(srv_port)} distinct server argv"
    assert srv_shell == srv_port, f"shell server argv {srv_shell} vs port {srv_port}"
    mtp_arms = [p for p in srv_shell if any(x[0] == "--mtp-model" for x in p)]
    plain_arms = [p for p in srv_shell if not any(x[0] == "--mtp-model" for x in p)]
    assert len(mtp_arms) == 1, f"expected one mtp server argv, got {mtp_arms}"
    assert len(plain_arms) == 1, f"expected one plain server argv, got {plain_arms}"
    for flag in ("--mtp-draft", "--mtp-timing"):
        assert any(x[0] == flag for x in mtp_arms[0]), (
            f"mtp arm lost {flag}: {mtp_arms[0]}"
        )
        assert not any(x[0] == flag for x in plain_arms[0]), (
            f"plain arm carried {flag}: {plain_arms[0]}"
        )


def test_the_two_arms_differ_only_in_backend_and_server_log(tmp_path) -> None:
    """The arms are the same command except the two tokens that name them.

    This is the claim the differential rests on: there is no arm-specific env
    var, so the env comparison is about the base. If a future edit adds an
    arm-specific env var, this test fails and the differential must be extended
    to assert on it.
    """
    out = tmp_path / "rec.jsonl"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    equiv.write_uv_fake_running_real(shim_dir / "uv", out, ROOT)
    invs = _shell_run_invs(tmp_path, out, shim_dir)
    by_log = {_server_log(i): i for i in invs}
    mtp = by_log[str(tmp_path / "logs" / "ds4server-r1-mtp.log")]
    plain = by_log[str(tmp_path / "logs" / "ds4server-r1-plain.log")]
    mtp_pairs = set(equiv.canonical(mtp.argv, frozenset()))
    plain_pairs = set(equiv.canonical(plain.argv, frozenset()))
    diff = mtp_pairs ^ plain_pairs
    assert diff == {
        ("--backend", driver.TREATMENT),
        ("--backend", driver.CONTROL),
        ("--server-log", str(tmp_path / "logs" / "ds4server-r1-mtp.log")),
        ("--server-log", str(tmp_path / "logs" / "ds4server-r1-plain.log")),
    }, f"arms differ on more than backend and server-log: {diff}"
