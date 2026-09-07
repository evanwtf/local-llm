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
    )


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.mark.parametrize("chunk", ["0", "00", "abc", "8192abc", "-1", "8.5", " ", "1 2"])
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


def test_no_ceiling_is_claimed_for_large_chunks():
    """There is no 8192 prefill ceiling, and this file used to say there was.

    The claim was that raw_cap clamped a chunk to 8192 after the first
    frontier, so a sweep above it mixed two measurements. It is wrong.
    ds4_default_raw_cap (ds4.c:12144) is the raw-KV attention cap -- DS4_N_SWA
    clamped to ctx, and the built-in shapes set n_swa to 128 or 0 -- and has
    nothing to do with prefill chunking. In the prefill path 8192 is only the
    PRO variant's default when no chunk was requested. A large value is
    honoured uniformly, so warning about it would be telling the reader
    something untrue about their own data.
    """
    p = run("65536")
    assert "ceiling" not in p.stderr, p.stderr
    assert "clamped" not in p.stderr, p.stderr
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr


def test_no_chunk_is_the_default_and_changes_nothing():
    p = run(None)
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr
    assert "ds4-bench is missing" in p.stderr, p.stderr
