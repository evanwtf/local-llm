"""PREFILL_CHUNK must be refused before the run commits the machine.

#171 added a `--prefill-chunk` passthrough to decode_ab_engine.sh. The value
comes from the environment, so a typo is one keystroke away from a sweep whose
column means something other than its label.

Two refusals are worth an artifact:

* `PREFILL_CHUNK=0`. ds4_prefill_cap_for_prompt (ds4.c:12159 at ds4 399acbbe)
  uses a requested chunk only on the `requested_chunk != 0` branch, so 0 falls
  through to the unspecified path and lands on the 4096 non-PRO default -- 0
  read as "unlimited" would quietly measure chunked prefill instead. On the
  DS4_METAL_PREFILL_CHUNK env path in that same function a value <= 0 really
  does mean unlimited, so the flag and the env var disagree about 0; that
  disagreement is why the guard refuses rather than passes it on. The first
  version accepted 0 while its own message said "not a positive integer" --
  the case pattern alone did not catch it, and `00` is what proved the numeric
  check earned its place.
* Anything non-numeric. `--prefill-chunk abc` reaches ds4-bench otherwise.

The negative case is the one that keeps the guard honest: a valid value must
NOT be refused here, or the whole feature is unreachable and nobody notices
until an overnight sweep produces no rows.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "decode_ab_engine.sh"

# Deliberately absent trees. The chunk guard runs before the ds4-bench check,
# so a rejected value stops at the guard and an accepted one falls through to
# the missing-binary refusal -- two distinguishable outcomes with no build,
# no model and no machine lock.
ARGS = ["a", "/nonexistent/tree-a", "b", "/nonexistent/tree-b", "/nonexistent/x.gguf"]


def run(chunk: str | None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
    if chunk is not None:
        env["PREFILL_CHUNK"] = chunk
    return subprocess.run(
        ["bash", str(SCRIPT), *ARGS],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        # These runs are expected to fail -- the script refuses a bad chunk
        # and exits non-zero, which is the thing under test. check=False is
        # the assertion, not an oversight (ruff PLW1510, see #197).
        check=False,
    )


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.mark.parametrize(
    "chunk", ["0", "00", "abc", "8192abc", "-1", "8.5", " ", "1 2"]
)
def test_a_bad_chunk_is_refused_before_anything_is_loaded(chunk):
    p = run(chunk)
    assert p.returncode == 1, f"PREFILL_CHUNK={chunk!r} was not refused"
    assert "REFUSING: PREFILL_CHUNK" in p.stderr, p.stderr


@pytest.mark.parametrize("chunk", ["1", "2048", "8192", "16384", "65536"])
def test_a_good_chunk_reaches_the_next_check(chunk):
    """A valid value must fall through, or the feature is unreachable."""
    p = run(chunk)
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr
    assert "ds4-bench is missing" in p.stderr, p.stderr


def test_a_large_chunk_warns_because_only_the_first_frontier_honours_it():
    """This assertion has been inverted once, and the history is the point.

    v1 of the script warned that a chunk above 8192 is clamped after the first
    frontier. That warning was substantively RIGHT, but it cited
    ds4_default_raw_cap -- the raw-KV attention cap, DS4_N_SWA clamped to ctx
    -- which has nothing to do with prefill chunking. The wrong citation was
    caught, and the whole warning was removed with it, and this test was
    written to pin its absence.

    Removing it was the error. The clamp is real; it lives in
    metal_graph_prefill_chunked (ds4.c:36867 at ds4-main 9ab70534), which sets
    chunk_cap = prefill_cap and then, for start != 0 only, cuts it to raw_cap
    -- and metal_graph_raw_cap_for_context (ds4.c:37541 at ds4-main 9ab70534)
    ceilings raw_cap at 8192. So a 65536 sweep really does report a 65536-token
    first frontier and 8192 for every other, in one run.

    A correct claim discarded because its evidence was wrong is still a
    regression. The warning is back, with the citation that holds.
    """
    p = run("65536")
    assert "WARNING" in p.stderr, p.stderr
    assert "8192" in p.stderr, p.stderr
    # Still not a refusal -- measuring the divergence on purpose is valid.
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr


def test_the_adamlawi_value_of_8192_does_not_warn():
    """At 8192 the two paths coincide, so a warning would be noise (#171)."""
    p = run("8192")
    assert "WARNING" not in p.stderr, p.stderr


def test_no_chunk_is_the_default_and_changes_nothing():
    p = run(None)
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr
    assert "ds4-bench is missing" in p.stderr, p.stderr


def test_a_chunk_above_the_raw_cap_ceiling_warns():
    """Above 8192 one run measures two quantities, and must say so.

    metal_graph_prefill_chunked clamps every prefill after the first to
    raw_cap (ds4.c:36867 at ds4-main 9ab70534), and raw_cap is ceilinged at
    8192 (ds4.c:37541 at ds4-main 9ab70534). So PREFILL_CHUNK=16384 gives a
    16384-token first frontier and 8192 for all the rest, silently. At 8192 --
    adamlawi's own value in #171 -- the two paths coincide exactly, which is
    why the ordinary sweep is the right shape there and needs no warning.
    """
    script = SCRIPT.read_text()
    assert "-gt 8192" in script, "no ceiling guard"
    assert "raw_cap" in script
    # A warning, not a refusal: the divergence is measurable on purpose.
    ceiling = script.split("-gt 8192")[1].split("fi")[0]
    assert "WARNING" in ceiling
    assert "REFUSING" not in ceiling


def test_the_prefill_comment_does_not_claim_there_is_no_ceiling():
    """The comment was corrected twice; pin the endpoint.

    v1 claimed raw_cap clamps prefill_cap to 8192 (wrong -- conflated two
    caps). v2 corrected that to "no ceiling to warn about" (also wrong -- went
    one step too far; the incremental path really is ceilinged). This pins v3.
    """
    script = SCRIPT.read_text()
    assert "no ceiling to warn about" not in script
    assert "start != 0" in script, "the incremental clamp must be documented"
