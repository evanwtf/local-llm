"""The #210 treatment-gate driver: `scripts/mtp_treatment_gate.sh`.

#210 is open because 119 MTP rows were taken with no evidence the draft head
ever engaged. Closing it needs two things at once -- rows that carry the
treatment, and a demonstration that the refusal fires on an arm that does not.

The refusal is the fragile half. It fires on a configuration that looks
correct everywhere else: `--mtp-draft 7 --mtp-timing` are accepted without
complaint when `--mtp-model` is absent, so the argv reads as an MTP arm and
`run.counters_on()` returns True on it. That is precisely why it is the broken
arm worth demonstrating, and precisely why the script's own shape has to be
held still -- a later edit that adds `--mtp-model` to the bypass branch would
turn the demonstration into a second treated arm and nothing would say so.

These are text invariants rather than a driven run, in the shape
`test_iso8601_timestamps.py` and `test_reps_parity.py` already use on the
shell runners: the script needs a 74 GiB model and an M5 to execute, and the
properties that rot are in its argv, not its control flow.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mtp_treatment_gate.sh"


def body() -> str:
    return SCRIPT.read_text()


def stage(name: str) -> str:
    """The text of one `case` branch, so an assertion cannot pass by
    matching a flag that belongs to a different stage."""
    text = body()
    start = text.index(f"\n{name})\n")
    end = text.index("\n    ;;", start)
    return text[start:end]


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_the_bypass_arm_does_not_pass_the_sidecar():
    """The whole demonstration is that this arm omits `--mtp-model` and is
    otherwise identical. If it ever gains one, the script still runs, still
    exits non-zero on nothing, and reports a gate that was never tested."""
    text = body()
    start = text.index("        # Same flags minus --mtp-model")
    end = text.index("    fi\n", start)
    # Comments name the flag too; the assertion is about the argv.
    branch = "\n".join(
        line
        for line in text[start:end].splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "--mtp-draft 7" in branch
    assert "--mtp-timing" in branch
    assert "--mtp-model" not in branch


def test_the_treated_arm_does_pass_the_sidecar():
    text = body()
    start = text.index('if [ "$want_mtp" = yes ]')
    end = text.index("        # Same flags minus --mtp-model")
    assert "--mtp-model" in text[start:end]


def test_the_two_arms_use_different_kv_directories():
    """ds4 rejects the other configuration's checkpoints, so a shared
    directory leaves the treated arm re-prefilling and the only symptom is
    that it looks slower."""
    text = body()
    assert "server-kv-mtp" in text
    assert "server-kv-210-bypass" in text
    assert stage("bypass").count("KV_BYPASS") == 1
    assert "KV_BYPASS" not in stage("treated")
    assert "KV_BYPASS" not in stage("silent")


def test_the_bypass_arm_demands_the_gate_and_treats_success_as_failure():
    """A zero exit from the broken arm means the gate did not fire. That is
    the finding, and the script has to say so rather than printing a run
    that looks like every other run."""
    branch = stage("bypass")
    assert "--require-draft" in branch
    assert "--no-require-draft" not in branch
    assert re.search(r'if \[ "\$rc" -eq 0 \]', branch)
    assert "FAILED" in branch


def test_the_bypass_arm_writes_no_row_into_the_corpus():
    """A refused trial raises before `write_row`, so nothing reaches
    results.jsonl. The claim should not rest on reading run.py correctly."""
    branch = stage("bypass")
    assert "--results" in branch
    assert "bypass-scratch.jsonl" in branch
    assert "--results" not in stage("treated")


def test_every_stage_reads_the_counters_from_a_server_log():
    """#148: this script's predecessor passed `--mtp-timing` and no
    `--server-log` for three cycles, so every counter the engine emitted was
    written to a file nothing read."""
    for name in ("bypass", "treated", "probe", "silent"):
        branch = stage(name)
        assert "--server-log" in branch, name
        if name != "probe":
            # The probe drives the engine directly; it runs no trials, so
            # there is no row for a draft engine to be stamped onto.
            assert "--draft-log-engine ds4" in branch, name


def test_only_the_silent_stage_switches_the_gate_off():
    """Stage 3 is the escape the refusal message names, not a retry. It has
    to be reachable and it has to be the only way to reach it."""
    assert "--no-require-draft" in stage("silent")
    assert "--no-require-draft" not in stage("treated")


def test_the_graph_line_is_asserted_before_a_trial_is_spent():
    """`MTP=off` against `MTP=Q4_K/Q8_0/BF16` is the only place the two
    configurations differ before the counters are read. A bypass arm that
    quietly loaded the sidecar would produce a passing run and prove
    nothing."""
    text = body()
    assert "MTP=off" in text
    assert "MTP sidecar loaded" in text
    assert "assert_graph off" in stage("bypass")
    assert "assert_graph on" in stage("treated")
    assert "assert_graph on" in stage("probe")
    assert "assert_graph on" in stage("silent")


def test_the_lock_is_held_by_the_script_and_run_py_is_told_so():
    """#133: the window the lock exists for is the gap between trials, where
    the server is deliberately down and a process scan truthfully reports
    all clear."""
    text = body()
    assert "--acquire-lock" in text
    assert "--release-lock" in text
    for name in ("bypass", "treated", "silent"):
        assert "--no-lock" in stage(name), name


def test_the_probe_stage_crosses_both_sizes_with_both_shapes():
    """The treated arm's refusal confounded tools with context length: 280
    cycles across 26 short toolless requests, zero across 16 tool-bearing
    requests at 11,760 tokens. One pad, or one shape, and the confound
    survives into the answer."""
    branch = stage("probe")
    assert "for pad in 0 11000" in branch
    assert "--arms plain tools" in branch
    assert "--pad-tokens" in branch


def test_the_probe_stage_runs_no_trials_and_writes_no_rows():
    """It is a diagnostic. A row from it would enter the corpus carrying a
    prompt no task defines."""
    branch = stage("probe")
    assert "run.py" not in branch
    assert "--results" not in branch
