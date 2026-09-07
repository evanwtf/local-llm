"""PREFILL_CHUNK must be refused before the run commits the machine.

#171 added a `--prefill-chunk` passthrough to decode_ab_engine.sh. The value
comes from the environment, so a typo is one keystroke away from a sweep whose
column means something other than its label.

Two refusals are worth an artifact:

* `PREFILL_CHUNK=0`. ds4_prefill_cap_for_prompt reads requested_chunk == 0 as
  "not specified" (ds4.c:13554) and falls back to the 4096 default, so 0 read
  as "unlimited" would quietly measure chunked prefill instead. The first
  version of the guard accepted 0 while its own message said "not a positive
  integer" -- the case pattern alone did not catch it, and the numeric check
  was added because of that.
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


@pytest.mark.parametrize("chunk", ["2048", "8192", "16384"])
def test_a_good_chunk_reaches_the_next_check(chunk):
    """A valid value must fall through, or the feature is unreachable."""
    p = run(chunk)
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr
    assert "ds4-bench is missing" in p.stderr, p.stderr


def test_a_chunk_above_the_raw_cap_warns_but_is_allowed():
    """8192 is raw_cap's ceiling (ds4.c:40442); above it the sweep is mixed."""
    p = run("16384")
    assert "exceeds raw_cap's 8192 ceiling" in p.stderr, p.stderr


def test_no_chunk_is_the_default_and_changes_nothing():
    p = run(None)
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr
    assert "ds4-bench is missing" in p.stderr, p.stderr
