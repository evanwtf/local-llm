"""The #118 engine A/B driver, ported from shell (#235).

`decode_ab` varies the weights; this varies the TREE. That distinction is the
whole reason the script exists and the whole reason it is easy to get wrong:
each arm must run from its own worktree, because ds4-bench resolves
`metal/*.metal` relative to its own tree at runtime. Run both arms from one
tree and the A/B compares a build against itself while producing two perfectly
ordinary CSVs.
"""

from __future__ import annotations

import ast
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import decode_ab
import decode_ab_engine as eng
import equiv
from source_text import code_of


def a_tree(base: pathlib.Path, name: str) -> pathlib.Path:
    tree = base / name
    (tree / "speed-bench").mkdir(parents=True)
    binary = tree / "ds4-bench"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return tree


# ------------------------------------------------------- the arm is the tree


def test_each_arm_runs_from_its_own_tree(tmp_path) -> None:
    """The failure that produces two ordinary CSVs and no comparison.

    ds4-bench loads metal/*.metal relative to its cwd, so an arm run from the
    other arm's tree measures the other arm's shaders. Nothing in the CSV says
    so.
    """
    a, b = a_tree(tmp_path, "A"), a_tree(tmp_path, "B")
    argv_a = decode_ab.bench_argv(
        tmp_path / "m.gguf",
        tmp_path / "a.csv",
        tmp_path / "p.txt",
        binary=a / "ds4-bench",
        ctx_start=2048,
        ctx_max=4096,
        step=2048,
        gen=128,
    )
    argv_b = decode_ab.bench_argv(
        tmp_path / "m.gguf",
        tmp_path / "b.csv",
        tmp_path / "p.txt",
        binary=b / "ds4-bench",
        ctx_start=2048,
        ctx_max=4096,
        step=2048,
        gen=128,
    )
    assert argv_a[0] != argv_b[0], "the two arms must not share a binary"
    assert str(a) in argv_a[0] and str(b) in argv_b[0]


def test_the_prompt_comes_from_tree_a_for_both_arms(tmp_path) -> None:
    """A branch that touches speed-bench/ must not change the input too."""
    a = a_tree(tmp_path, "A")
    a_tree(tmp_path, "B")
    assert eng.prompt_for(a) == a / "speed-bench" / "promessi_sposi.txt"


# --------------------------------------------------- refusals, before the lock


def test_a_missing_build_is_refused(tmp_path) -> None:
    """A typo should cost a second, not a model load.

    This used to fail mid-run: the lock was held, 73 GiB was resident, and the
    first ds4-bench invocation died on "no such file".
    """
    a = a_tree(tmp_path, "A")
    b = tmp_path / "B"
    b.mkdir()
    with pytest.raises(decode_ab.Refusal) as caught:
        eng.check_binaries([a, b])
    assert "ds4-bench" in str(caught.value)


def test_a_non_executable_build_is_refused_too(tmp_path) -> None:
    a = a_tree(tmp_path, "A")
    b = a_tree(tmp_path, "B")
    (b / "ds4-bench").chmod(0o644)
    with pytest.raises(decode_ab.Refusal):
        eng.check_binaries([a, b])


def test_the_binary_check_happens_before_the_lock(tmp_path, monkeypatch) -> None:
    """Asserted through sweep, because the ORDER is the thing being claimed."""

    def explode(*a, **k):
        raise AssertionError("the lock must not be taken before the build check")

    monkeypatch.setattr(decode_ab, "run_lock", explode)
    a = a_tree(tmp_path, "A")
    b = tmp_path / "B"
    b.mkdir()
    with pytest.raises(decode_ab.Refusal):
        eng.sweep(
            [("a", a), ("b", b)],
            tmp_path / "m.gguf",
            4,
            tmp_path / "out",
            owner_pid=1,
        )


def test_an_odd_rep_count_is_refused_by_the_cli(tmp_path, monkeypatch) -> None:
    """Refused, not warned: an odd sweep produces a plausible number.

    Alternation cancels the position bias only on an even count. At REPS=3
    reps 1 and 3 run A-first and only rep 2 runs B-first, so the bias lands
    2:1. Across #171's twelve reps whichever arm ran first was faster in 9,
    median +0.9%, +5.9% on a cold first rep.
    """

    def explode(*a, **k):
        raise AssertionError("a refused run must not reach the machine")

    monkeypatch.setattr(eng.child, "run", explode)
    monkeypatch.setattr(decode_ab, "run_lock", explode)
    a, b = a_tree(tmp_path, "A"), a_tree(tmp_path, "B")
    rc = eng.main(
        [
            "a",
            str(a),
            "b",
            str(b),
            str(tmp_path / "m.gguf"),
            str(tmp_path / "out"),
            "--reps",
            "3",
        ]
    )
    assert rc != 0


# ------------------------------------------------------------- the #203 path


def test_out_is_absolutized_before_the_arms_run(tmp_path, monkeypatch) -> None:
    """#203: each arm runs with cwd=tree, so a relative OUT lands nowhere.

    `mkdir` creates it under the repo and `--csv` resolves it under the ds4
    tree. The default OUT is absolute, which is why this only ever showed on a
    hand-passed relative path.
    """
    seen: list[pathlib.Path] = []

    def capture(arms, gguf, reps, out, **kw):
        seen.append(out)
        return 0

    monkeypatch.setattr(eng, "sweep", capture)
    monkeypatch.chdir(tmp_path)
    eng.main(["a", "A", "b", "B", "m.gguf", "relative/out"])
    assert seen and seen[0].is_absolute(), seen


# --------------------------------------------------------- the provenance file


def test_both_trees_commits_are_recorded(tmp_path, monkeypatch) -> None:
    """#118's arm shas live only in issue prose; rerunning means trusting it."""
    monkeypatch.setattr(
        decode_ab, "git_out", lambda tree, *a: "abc1234" if "rev-parse" in a else ""
    )
    text = eng.engines_text(
        [("old", pathlib.Path("/t/A")), ("new", pathlib.Path("/t/B"))],
        pathlib.Path("/m.gguf"),
        pathlib.Path("/p.txt"),
        ctx_start=2048,
        ctx_max=16384,
        step=2048,
        gen=128,
        reps=4,
        chunk=None,
    )
    assert "A label=old tree=/t/A @ abc1234" in text
    assert "B label=new tree=/t/B @ abc1234" in text
    # The sweep is an input to every ratio in the CSVs and was recorded
    # nowhere: a comparison against a published 32-frontier baseline once ran
    # on the 8-frontier default and read 1.152 against 1.155.
    assert "sweep ctx_start=2048 ctx_max=16384 step=2048 gen=128 reps=4" in text
    assert "prefill_chunk=<flag absent>" in text
    # An inherited cap would silently change the prefill shape of a run that
    # never mentions it.
    assert "DS4_METAL_PREFILL_CHUNK=" in text
    assert "DS4_METAL_GRAPH_RAW_CAP=" in text


def test_a_dirty_tree_is_named_as_dirty(tmp_path, monkeypatch) -> None:
    """A sha does not name a binary built from uncommitted code."""
    monkeypatch.setattr(decode_ab, "git_out", lambda tree, *a: "M x.c")
    text = eng.engines_text(
        [("old", pathlib.Path("/t/A")), ("new", pathlib.Path("/t/B"))],
        pathlib.Path("/m.gguf"),
        pathlib.Path("/p.txt"),
        ctx_start=1,
        ctx_max=2,
        step=1,
        gen=1,
        reps=2,
        chunk=None,
    )
    assert "A_dirty=true" in text and "B_dirty=true" in text


def test_the_prefill_reasoning_is_not_restated(tmp_path) -> None:
    """One owner for the 0-is-not-unlimited and 8192 raw_cap rules."""
    source = code_of(ROOT / "scripts" / "decode_ab_engine.py")
    assert "decode_ab.prefill_chunk" in source
    assert "8192" not in source, "the raw_cap ceiling belongs to decode_ab"


def test_the_driver_never_calls_pgrep() -> None:
    source = (ROOT / "scripts" / "decode_ab_engine.py").read_text()
    assert "pgrep" not in source and "pkill" not in source


def test_the_measurement_child_goes_through_child_run() -> None:
    """#268: a driver stopped mid-sweep must take ds4-bench with it."""
    # Both files still call subprocess.run -- for `git`, a question answered
    # in milliseconds and not a measurement. The claim is about the BENCH
    # child, so walk for the call whose argv comes from bench_argv and check
    # what spawns it. A substring search would pass on the wrong call.
    for name in ("decode_ab.py", "decode_ab_engine.py"):
        tree = ast.parse((ROOT / "scripts" / name).read_text())
        spawners = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Call):
                continue
            inner = first.func
            built = getattr(inner, "attr", None) or getattr(inner, "id", None)
            if built != "bench_argv":
                continue
            spawners.add(ast.unparse(node.func))
        assert spawners == {"child.run"}, f"{name} spawns ds4-bench via {spawners}"


def test_env_is_read_at_call_time_not_import_time(monkeypatch) -> None:
    """A cap exported after import must still reach engines.txt."""
    monkeypatch.setenv("DS4_METAL_PREFILL_CHUNK", "4096")
    text = eng.engines_text(
        [("a", pathlib.Path("/A")), ("b", pathlib.Path("/B"))],
        pathlib.Path("/m"),
        pathlib.Path("/p"),
        ctx_start=1,
        ctx_max=2,
        step=1,
        gen=1,
        reps=2,
        chunk=None,
    )
    assert "DS4_METAL_PREFILL_CHUNK=4096" in text


# ------------------------------------------------- the #235 retirement differential
#
# The port's claim is that, under identical inputs, it hands ds4-bench the same
# command line the shell did. The shell cannot run on a GPU here, but it does
# not need one: a fake `uv` no-ops the lock and prompt_meta, and a fake
# `./ds4-bench` in each tree records the shell's real argv offline. The two
# sides must differ in nothing but the binary's path form (the recording drops
# argv[0], so the flags are what the recording proves equal).


def test_the_shell_and_the_port_hand_ds4_bench_the_same_command(tmp_path) -> None:
    """Run the real .sh under the shim, run the port's builder for the same
    inputs, and diff. The difference is empty."""
    import subprocess

    a = a_tree(tmp_path, "A")
    b = a_tree(tmp_path, "B")
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF")
    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt")
    out = tmp_path / "out"
    shim = tmp_path / "shim"
    shim.mkdir()
    probe = tmp_path / "probe.jsonl"
    equiv.write_uv_fake(shim / "uv", probe)
    # The fake ds4-bench records argv; the fake uv no-ops preflight/prompt_meta.
    for tree in (a, b):
        equiv.write_fake(tree / "ds4-bench", probe)

    env = {
        "PATH": f"{shim}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
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
    sh = ROOT / "vault" / "decode_ab_engine.sh"
    got = subprocess.run(
        [
            "bash",
            str(sh),
            "a",
            str(a),
            "b",
            str(b),
            str(gguf),
            str(out),
        ],
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
        # The binary is argv[0], which the recording drops and canonical()
        # ignores, so a dummy binary is fine -- the flags are what is compared.
        expected = decode_ab.bench_argv(
            gguf,
            pathlib.Path(csv),
            prompt,
            binary=a / "ds4-bench",
            ctx_start=2048,
            ctx_max=4096,
            step=2048,
            gen=128,
        )
        shell_only, py_only = equiv.argv_difference(inv.argv, expected, frozenset())
        assert shell_only == set(), f"{inv.argv} vs {expected}: shell-only {shell_only}"
        assert py_only == set(), f"{inv.argv} vs {expected}: port-only {py_only}"
