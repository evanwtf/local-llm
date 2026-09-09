"""decode_ab.sh must refuse a bad PREFILL_CHUNK and record what it ran.

The weights A/B could not produce @iammac2's quantity at all: they reported
ROCm Q4-vs-Q8 at head as "large chunk (8192-token prefill)", and this script
had no way to set the chunk. An unflagged prefill above 4096 tokens is chunked
at the variant default whether or not you meant it, so a sweep that does not
set it measures the default and calls it the chunk.

The guard is the same shape as decode_ab_engine.sh's (#171) and refuses for
the same reasons -- see tests/test_decode_ab_engine.py for why 0 is a refusal
rather than "unlimited". Duplicated deliberately: two scripts commit the
machine for hours, and a guard on one of them is not a guard.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
SCRIPT = ROOT / "scripts" / "decode_ab.sh"

import decode_ab
import equiv

# Absent GGUFs. The chunk guard runs before anything is loaded, so a rejected
# value stops there and an accepted one falls through -- distinguishable with
# no model, no build and no machine lock.
ARGS = ["a", "/nonexistent/a.gguf", "b", "/nonexistent/b.gguf"]


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
        # Expected to exit non-zero: refusing is the behaviour under test.
        check=False,
    )


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.mark.parametrize("chunk", ["0", "00", "abc", "8192abc", "-1", "8.5", " "])
def test_a_bad_chunk_is_refused(chunk):
    p = run(chunk)
    assert p.returncode == 1, f"PREFILL_CHUNK={chunk!r} was not refused"
    assert "REFUSING: PREFILL_CHUNK" in p.stderr, p.stderr


def test_a_valid_chunk_is_not_refused():
    """The negative case. Without it the guard could refuse everything and
    the feature would be unreachable until an overnight sweep produced no
    rows."""
    p = run("8192")
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr


def test_the_adamlawi_and_iammac2_value_does_not_warn():
    """8192 is the raw_cap ceiling itself, so both prefill paths coincide and
    a warning there would be noise on the exact run it protects (#162, #171)."""
    p = run("8192")
    assert "WARNING" not in p.stderr, p.stderr


def test_a_chunk_above_the_ceiling_warns_but_is_not_refused():
    """Above 8192 only frontier 1 honours the flag: raw_cap cuts the rest
    (ds4.c:36867 at ds4-main 9ab70534, ds4.c:37541 at ds4-main 9ab70534).
    Measuring that on purpose is valid, so it warns rather than refuses."""
    p = run("65536")
    assert "WARNING" in p.stderr, p.stderr
    assert "REFUSING: PREFILL_CHUNK" not in p.stderr, p.stderr


def test_the_flag_array_is_bash_3_2_safe():
    """macOS ships bash 3.2, where "${arr[@]}" on an empty array aborts under
    set -u -- which would kill the run at the first arm, after the lock is
    held. The ${arr[@]+"${arr[@]}"} form is safe on both."""
    script = SCRIPT.read_text()
    assert '${prefill_flag[@]+"${prefill_flag[@]}"}' in script


def test_the_engine_build_is_recorded():
    """#192: $DS4 picks the binary AND the metal shaders, so the tree is part
    of the measurement. A sweep that does not name it cannot be cited."""
    script = SCRIPT.read_text()
    assert "engines.txt" in script
    assert "rev-parse --short HEAD" in script
    assert "engine_dirty" in script, "a dirty tree's sha does not name its binary"
    assert "prefill_chunk=" in script, "the chunk is an input to the result"


# ------------------------------------------------- the #235 retirement differential
#
# The port's claim is that, under identical inputs, it hands ds4-bench the same
# command line the shell did. A fake `uv` no-ops the lock and prompt_meta, and
# a fake `./ds4-bench` in the DS4 tree records the shell's real argv offline.


def test_the_shell_and_the_port_hand_ds4_bench_the_same_command(tmp_path) -> None:
    ds4_tree = tmp_path / "ds4"
    ds4_tree.mkdir()
    gguf_a = tmp_path / "a.gguf"
    gguf_a.write_bytes(b"GGUF")
    gguf_b = tmp_path / "b.gguf"
    gguf_b.write_bytes(b"GGUF")
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt")
    out = tmp_path / "out"
    shim = tmp_path / "shim"
    shim.mkdir()
    probe = tmp_path / "probe.jsonl"
    equiv.write_uv_fake(shim / "uv", probe)
    equiv.write_fake(ds4_tree / "ds4-bench", probe)

    env = {
        "PATH": f"{shim}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "DS4": str(ds4_tree),
        "REPS": "2",
        "CTX_START": "2048",
        "CTX_MAX": "4096",
        "STEP": "2048",
        "GEN": "128",
        "PROMPT": str(prompt),
        "EQUIV_OUT": str(probe),
        "EQUIV_PROGRAM": "ds4-bench",
        "EQUIV_ARM": "shell",
    }
    got = subprocess.run(
        ["bash", str(SCRIPT), "a", str(gguf_a), "b", str(gguf_b), str(out)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert got.returncode == 0, (
        f"shell exited {got.returncode}:\n{got.stderr}\n{got.stdout}"
    )

    invs = equiv.by_program(equiv.load(probe), "ds4-bench")
    assert len(invs) == 4, (
        f"REPS=2 x 2 arms should record 4 ds4-bench calls, got {len(invs)}"
    )
    for inv in invs:
        csv = inv.argv[inv.argv.index("--csv") + 1]
        expected = decode_ab.bench_argv(
            pathlib.Path(inv.argv[inv.argv.index("-m") + 1]),
            pathlib.Path(csv),
            prompt,
            binary=ds4_tree / "ds4-bench",
            ctx_start=2048,
            ctx_max=4096,
            step=2048,
            gen=128,
        )
        shell_only, py_only = equiv.argv_difference(inv.argv, expected, frozenset())
        assert shell_only == set(), f"{inv.argv} vs {expected}: shell-only {shell_only}"
        assert py_only == set(), f"{inv.argv} vs {expected}: port-only {py_only}"
