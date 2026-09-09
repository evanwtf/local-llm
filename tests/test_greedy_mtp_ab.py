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
