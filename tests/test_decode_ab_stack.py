"""The #138 stack A/B driver, ported from shell (#235).

`decode_ab` varies the weights and `decode_ab_engine` varies the TREE. This is
the honest third shape where each arm carries its own engine, weights and PLE
sidecar, because no single binary loads both GGUFs. An engine A/B whose rows
cannot say which engine produced them is unreadable a week later, and this is
the one shape where the engine is part of the arm rather than held constant.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import decode_ab
import decode_ab_stack as stk
from source_text import code_of


def a_tree(base: pathlib.Path, name: str) -> pathlib.Path:
    tree = base / name
    (tree / "speed-bench").mkdir(parents=True)
    binary = tree / "ds4-bench"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return tree


@contextlib.contextmanager
def _no_lock(*a, **k):
    yield


def _no_stamp(*a, **k):
    pass


def fake_arms(tmp_path):
    a = a_tree(tmp_path, "A")
    b = a_tree(tmp_path, "B")
    gguf_a, gguf_b = tmp_path / "a.gguf", tmp_path / "b.gguf"
    ple = tmp_path / "a.pte"
    gguf_a.touch()
    gguf_b.touch()
    ple.touch()
    arms = [
        ("old", a, gguf_a, str(ple)),
        ("new", b, gguf_b, "-"),
    ]
    return a, b, arms


# -------------------------------------------------- the arm is the whole stack


def test_each_arm_runs_from_its_own_tree_with_its_own_binary(
    tmp_path, monkeypatch
) -> None:
    """The failure that produces two ordinary CSVs and no comparison.

    ds4-bench loads metal/*.metal relative to its cwd, so an arm run from the
    other arm's tree measures the other arm's shaders. Here the engine is part
    of the arm: each arm must use its OWN tree's binary and run from that tree.
    """
    a, _b, arms = fake_arms(tmp_path)
    seen: list[tuple[list[str], pathlib.Path]] = []

    def capture(argv, *, cwd, log, **k):
        seen.append((argv, cwd, log))
        return 0

    monkeypatch.setattr(stk.child, "run", capture)
    monkeypatch.setattr(decode_ab, "run_lock", _no_lock)
    monkeypatch.setattr(decode_ab, "stamp_prompt", _no_stamp)
    monkeypatch.setattr(decode_ab, "git_out", lambda tree, *_: "abc1234")
    stk.sweep(arms, 2, tmp_path / "out", tmp_path / "p.txt", owner_pid=1)
    assert len(seen) == 4, "2 reps x 2 arms"
    # Every arm used ITS OWN binary and ran from ITS OWN tree. A binary/cwd
    # disagreement here is the engine A/B comparing a build against itself.
    for argv, cwd, _ in seen:
        assert (str(a / "ds4-bench") in argv) == (cwd == a), "binary and cwd must agree"
    # The per-arm log is the only evidence the two trees ran DIFFERENT code.
    old_log = next(t[2] for t in seen if "old-rep1.log" in str(t[2]))
    new_log = next(t[2] for t in seen if "new-rep1.log" in str(t[2]))
    assert old_log == tmp_path / "out" / "old-rep1.log"
    assert new_log == tmp_path / "out" / "new-rep1.log"


def test_ple_is_appended_only_when_the_arm_has_a_sidecar(tmp_path, monkeypatch) -> None:
    """`-` means no PLE sidecar; only a real sidecar gets `--ple` (#138)."""
    _a, _b, arms = fake_arms(tmp_path)
    seen: list[list[str]] = []

    def capture(argv, **k):
        seen.append(argv)
        return 0

    monkeypatch.setattr(stk.child, "run", capture)
    monkeypatch.setattr(decode_ab, "run_lock", _no_lock)
    monkeypatch.setattr(decode_ab, "stamp_prompt", _no_stamp)
    monkeypatch.setattr(decode_ab, "git_out", lambda tree, *_: "abc1234")
    stk.sweep(arms, 2, tmp_path / "out", tmp_path / "p.txt", owner_pid=1)
    arm_a = next(v for v in seen if "old-rep1.csv" in " ".join(v))
    arm_b = next(v for v in seen if "new-rep1.csv" in " ".join(v))
    assert "--ple" in arm_a and str(arms[0][3]) in arm_a
    assert "--ple" not in arm_b


# --------------------------------------------------- refusals, before the lock


def test_a_missing_build_is_refused(tmp_path) -> None:
    """A typo should cost a second, not a model load."""
    a = a_tree(tmp_path, "A")
    b = tmp_path / "B"
    b.mkdir()
    with pytest.raises(decode_ab.Refusal) as caught:
        stk.check_binaries([a, b])
    assert "ds4-bench" in str(caught.value)


def test_the_binary_check_happens_before_the_lock(tmp_path, monkeypatch) -> None:
    def explode(*a, **k):
        raise AssertionError("the lock must not be taken before the build check")

    monkeypatch.setattr(decode_ab, "run_lock", explode)
    a = a_tree(tmp_path, "A")
    b = tmp_path / "B"
    b.mkdir()
    with pytest.raises(decode_ab.Refusal):
        stk.sweep(
            [("a", a, tmp_path / "a.gguf", "-"), ("b", b, tmp_path / "b.gguf", "-")],
            2,
            tmp_path / "out",
            tmp_path / "p.txt",
            owner_pid=1,
        )


def test_an_odd_rep_count_is_refused_by_the_cli(tmp_path, monkeypatch) -> None:
    """Refused, not warned: an odd sweep produces a plausible number."""

    def explode(*a, **k):
        raise AssertionError("a refused run must not reach the machine")

    monkeypatch.setattr(stk.child, "run", explode)
    monkeypatch.setattr(decode_ab, "run_lock", explode)
    a, b, arms = fake_arms(tmp_path)
    rc = stk.main(
        [
            "old",
            str(a),
            str(arms[0][2]),
            "-",
            "new",
            str(b),
            str(arms[1][2]),
            "-",
            str(tmp_path / "out"),
            "--reps",
            "3",
        ]
    )
    assert rc != 0


def test_the_stack_is_always_reported_as_two_variables(tmp_path, monkeypatch) -> None:
    """#138: the quant and the engine move together; neither can be attributed."""
    monkeypatch.setattr(
        decode_ab, "git_out", lambda tree, *a: "abc1234" if "rev-parse" in a else ""
    )
    text = stk.stacks_text(
        [
            ("old", pathlib.Path("/t/A"), pathlib.Path("/a.gguf"), "-"),
            ("new", pathlib.Path("/t/B"), pathlib.Path("/b.gguf"), "-"),
        ],
        prompt=pathlib.Path("/p.txt"),
        ctx_start=2048,
        ctx_max=16384,
        step=2048,
        gen=128,
        reps=4,
    )
    assert "B label=new tree=/t/B @ abc1234" in text
    assert "TWO VARIABLES" in text
    assert "neither half can be attributed on its own (#138)" in text


def test_the_raw_cap_ceiling_belongs_to_decode_ab(tmp_path) -> None:
    """'8192' must not appear in code: the raw_cap ceiling has one owner."""
    source = code_of(ROOT / "scripts" / "decode_ab_stack.py")
    assert "8192" not in source, "the raw_cap ceiling belongs to decode_ab"


def test_the_measurement_child_goes_through_child_run(tmp_path) -> None:
    """#268: a driver stopped mid-sweep must take ds4-bench with it.

    The bench argv is built by `bench_argv` and handed to `child.run`, so a
    stop kills the measurement. `subprocess.run` would leave ds4-bench up, so
    the guard resolves the `argv` argument of each `child.run` call back to
    its `bench_argv` assignment rather than requiring an inline call.
    """
    import ast

    module = ast.parse((ROOT / "scripts" / "decode_ab_stack.py").read_text())
    derived = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if (getattr(func, "attr", None) or getattr(func, "id", None)) != "bench_argv":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                derived.add(target.id)
    spawners = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if ast.unparse(node.func) != "child.run":
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id in derived:
            spawners.add("child.run")
    assert spawners == {"child.run"}, "the bench child must go through child.run"


def test_the_driver_never_calls_pgrep(tmp_path) -> None:
    source = (ROOT / "scripts" / "decode_ab_stack.py").read_text()
    assert "pgrep" not in source and "pkill" not in source


def test_out_is_absolutized_before_the_arms_run(tmp_path, monkeypatch) -> None:
    """#203: each arm runs with cwd=tree, so a relative OUT lands nowhere."""
    seen: list[pathlib.Path] = []

    def capture(arms, reps, out, prompt, **kw):
        seen.append(out)
        return 0

    monkeypatch.setattr(stk, "sweep", capture)
    monkeypatch.chdir(tmp_path)
    stk.main(["a", "A", "a.gguf", "-", "b", "B", "b.gguf", "-", "relative/out"])
    assert seen and seen[0].is_absolute(), seen
