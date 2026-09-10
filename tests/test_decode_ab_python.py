"""The Python decode-rate A/B driver (#48, #235 stage 4).

`test_decode_ab.py` covers the shell script this replaces and stays until
#235's rule is met: the `.sh` is deleted only after its Python replacement has
produced a run that agrees with it.

This driver is the second adopter of `ab_driver`, and it was chosen because it
is the opposite shape from the first: no server, no agent, no shim. If the
shared loop only fits drivers that start servers, that is worth finding out on
the second adopter rather than the ninth.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ab_driver
import decode_ab as driver
from source_text import code_of

SHELL = ROOT / "vault" / "decode_ab.sh"


# --- the refusals, which must happen before 84 GiB is resident ---------------


@pytest.mark.parametrize("chunk", ["0", "00", "abc", "8192abc", "-1", "8.5", " "])
def test_a_bad_chunk_is_refused(chunk: str) -> None:
    """The same parameters as the shell's own test. `"00"` is here because the
    shell's `case` guard did not catch it -- `0` matches only a single zero and
    `0*[!0-9]*` needs a non-digit that `"00"` does not have -- and a numeric
    test added afterwards is what refuses it."""
    with pytest.raises(driver.Refusal):
        driver.prefill_chunk(chunk)


def test_a_valid_chunk_is_not_refused() -> None:
    """The negative case. Without it the guard could refuse everything and the
    feature would be unreachable until an overnight sweep produced no rows."""
    assert driver.prefill_chunk("8192") == 8192


def test_an_unset_chunk_is_not_a_refusal() -> None:
    """Empty by default, so an ordinary sweep is unchanged and the flag is
    absent from the command line."""
    assert driver.prefill_chunk(None) is None
    assert driver.prefill_chunk("") is None


def test_zero_is_refused_because_ds4_reads_it_as_unspecified() -> None:
    """`ds4_prefill_cap_for_prompt` (ds4.c:12986 at ds4-main 9ab70534) takes
    the `requested_chunk != 0` branch or nothing, so 0 falls through to the
    variant default. 'Unlimited' is not a reading it supports."""
    with pytest.raises(driver.Refusal, match="unspecified"):
        driver.prefill_chunk("0")


def test_the_ceiling_value_itself_does_not_warn(caplog) -> None:
    """8192 is the raw_cap ceiling itself, so both prefill paths coincide and
    a warning there would be noise on the exact run it protects (#162, #171)."""
    with caplog.at_level("WARNING", logger=driver.logger.name):
        driver.prefill_chunk("8192")
    assert caplog.text == ""


def test_a_chunk_above_the_ceiling_warns_but_is_not_refused(caplog) -> None:
    """Above 8192 only frontier 1 honours the flag: raw_cap cuts the rest
    (ds4.c:36867 at ds4-main 9ab70534,
    ds4.c:37541 at ds4-main 9ab70534). Measuring that on purpose
    is valid, so it warns rather than refuses."""
    with caplog.at_level("WARNING", logger=driver.logger.name):
        assert driver.prefill_chunk("65536") == 65536
    assert "raw_cap" in caplog.text
    assert "One run, two quantities" in caplog.text


# --- the empty-flag case that has no representation here ---------------------


def test_an_absent_chunk_produces_no_flag() -> None:
    """The shell needed `${prefill_flag[@]+"${prefill_flag[@]}"}` because
    bash 3.2 aborts on an empty array under `set -u` -- the same defect that
    cost `greedy_mtp_ab.sh` its control arm. There is no empty-array case
    here to get wrong."""
    argv = _argv(chunk=None)
    assert "--prefill-chunk" not in argv


def test_a_present_chunk_produces_the_flag() -> None:
    argv = _argv(chunk=8192)
    assert argv[argv.index("--prefill-chunk") + 1] == "8192"


def test_the_bench_line_carries_the_sweep_and_the_csv() -> None:
    argv = _argv(chunk=None)
    assert argv[argv.index("--ctx-start") + 1] == "2048"
    assert argv[argv.index("--ctx-max") + 1] == "16384"
    assert argv[argv.index("--step-incr") + 1] == "2048"
    assert argv[argv.index("--gen-tokens") + 1] == "128"
    assert argv[argv.index("--csv") + 1].endswith("out.csv")
    assert "--metal" in argv


def _argv(*, chunk: int | None) -> list[str]:
    return driver.bench_argv(
        pathlib.Path("/m/a.gguf"),
        pathlib.Path("/o/out.csv"),
        pathlib.Path("/p/prompt.txt"),
        binary=pathlib.Path("/t/ds4-bench"),
        ctx_start=2048,
        ctx_max=16384,
        step=2048,
        gen=128,
        chunk=chunk,
    )


# --- provenance, written before the first arm --------------------------------


def test_the_engine_build_is_recorded() -> None:
    """#192: DS4 picks the binary AND the metal shaders, so the tree is part
    of the measurement. A sweep that does not name it cannot be cited."""
    text = _engines(head="abc1234", dirty=False)
    assert "abc1234" in text
    assert "engine tree=" in text
    assert "binary_mtime=" in text


def test_a_dirty_engine_tree_says_so() -> None:
    """A dirty tree's sha does not name its binary."""
    assert "engine_dirty=true" in _engines(head="abc1234", dirty=True)
    assert "engine_dirty" not in _engines(head="abc1234", dirty=False)


def test_an_unknown_head_is_recorded_as_unknown_not_omitted() -> None:
    """A missing tree must read as unknown, not as an absent line -- an absent
    line is indistinguishable from a clean build in a later reader."""
    assert "unknown" in _engines(head=None, dirty=False)


def test_both_arms_are_named_in_the_sidecar() -> None:
    text = _engines(head="abc1234", dirty=False)
    assert "A label=q4" in text
    assert "B label=q8" in text


def _engines(*, head: str | None, dirty: bool) -> str:
    return driver.engines_text(
        pathlib.Path("/t/ds4"),
        [("q4", pathlib.Path("/m/a.gguf")), ("q8", pathlib.Path("/m/b.gguf"))],
        ctx_start=2048,
        ctx_max=16384,
        step=2048,
        gen=128,
        reps=4,
        chunk=None,
        head=head,
        dirty=dirty,
        binary_mtime="2026-09-09T04:00:00-04:00",
    )


# --- the shared loop, from a driver that starts no server --------------------


def test_this_driver_declares_that_it_manages_its_own_processes() -> None:
    """`ab_driver.nothing` is the seam that says so. A driver with no server
    must be able to use the shared loop without inventing a fake one."""
    assert "ab_driver.nothing" in code_of(ROOT / "scripts" / "decode_ab.py")


def test_the_reps_refusal_matches_the_shells() -> None:
    """The shell refuses an odd REPS with exit 2. #201: at REPS=3 reps 1 and 3
    run A-first and only rep 2 runs B-first, so the bias lands 2:1 on one arm
    instead of dividing out."""
    assert driver.main(["a", "/m/a.gguf", "b", "/m/b.gguf", "--reps", "3"]) == 2


def test_an_even_rep_count_is_what_the_shared_loop_wants() -> None:
    assert ab_driver.leads_equally(4, 2)
    assert not ab_driver.leads_equally(3, 2)


# --- what the port must not have brought across ------------------------------


def test_the_driver_does_not_look_for_processes_by_name() -> None:
    code = code_of(ROOT / "scripts" / "decode_ab.py")
    assert "pgrep" not in code
    assert "pkill" not in code


def test_the_shell_it_replaces_is_still_here() -> None:
    """#235: the `.sh` is deleted only after its Python replacement has
    produced a run that agrees with it."""
    assert SHELL.exists(), "see #235 -- do not delete until a run agrees"
