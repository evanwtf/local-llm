"""The prefill-chunk A/B driver.

`decode_ab` varies the weights and `decode_ab_engine` varies the TREE. Both
give each arm a different binary or GGUF, so "the two arms differ" is visible
in the argv. This driver varies neither: both arms are the SAME build in the
SAME tree, and differ only in the `--prefill-chunk` value passed to ds4-bench.

That makes the failure mode invisible the way `metal_knob_ab` warns about --
if `one_arm` closes over a single shared `chunk` instead of the arm's own, the
run produces two ordinary CSVs, a plausible ratio, and no indication that both
arms ran the same chunk. The first test is the whole reason the file exists.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import decode_ab
import prefill_chunk_ab as pc


def a_tree(base: pathlib.Path, name: str = "tree") -> pathlib.Path:
    tree = base / name
    (tree / "speed-bench").mkdir(parents=True)
    (tree / "speed-bench" / "promessi_sposi.txt").write_text("prompt\n")
    binary = tree / "ds4-bench"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return tree


def capture_argvs(monkeypatch) -> list[list[str]]:
    """Record every ds4-bench argv the sweep builds, and never run one.

    The fake stands in for ds4-bench: it writes the CSV and the log the real
    binary would, so the sweep's stamp and admission steps run against real
    files. Stamping itself (prompt_meta) is a separate subsystem and is stubbed
    -- these tests are about which chunk each arm passes, nothing else.
    """
    seen: list[list[str]] = []

    def fake_run(argv, *, cwd=None, log=None, **kw):
        seen.append([str(a) for a in argv])
        argv = [str(a) for a in argv]
        chunk = argv[argv.index("--prefill-chunk") + 1]
        csv = pathlib.Path(next(a for a in argv if a.endswith(".csv")))
        csv.write_text("ctx_tokens,prefill_tps,gen_steady_tps\n8192,600,45\n")
        if log is not None:
            pathlib.Path(log).write_text(
                f"ds4-bench: context buffers (prefill_chunk={chunk})\n"
            )
        return 0

    import contextlib

    @contextlib.contextmanager
    def no_lock(*a, **kw):
        yield

    monkeypatch.setattr(pc.child, "run", fake_run)
    monkeypatch.setattr(decode_ab, "run_lock", no_lock)
    monkeypatch.setattr(decode_ab, "stamp_prompt", lambda *a, **kw: None)
    return seen


# ------------------------------------------------ the arm is the chunk value


def test_each_arm_runs_at_its_own_chunk(tmp_path, monkeypatch) -> None:
    """THE number guard: a shared chunk gives two identical arms, no signal."""
    tree = a_tree(tmp_path)
    seen = capture_argvs(monkeypatch)
    pc.sweep(
        tree,
        tmp_path / "m.gguf",
        [("chunk8192", 8192), ("chunk4096", 4096)],
        reps=2,
        out=tmp_path / "out",
        ctx_start=2048,
        ctx_max=2048,
        step=2048,
        gen=32,
        owner_pid=1234,
    )
    a_argvs = [a for a in seen if any("chunk8192-rep" in x for x in a)]
    b_argvs = [a for a in seen if any("chunk4096-rep" in x for x in a)]
    assert a_argvs and b_argvs, "both arms must run"
    for a in a_argvs:
        assert "8192" in a[a.index("--prefill-chunk") + 1]
    for b in b_argvs:
        assert "4096" in b[b.index("--prefill-chunk") + 1]


def test_both_arms_share_one_tree(tmp_path, monkeypatch) -> None:
    """The inversion of the engine A/B: one build, one tree, both arms.

    Guards against a well-meaning edit that copies decode_ab_engine's
    two-tree rule into a driver where it is wrong.
    """
    tree = a_tree(tmp_path)
    seen = capture_argvs(monkeypatch)
    pc.sweep(
        tree,
        tmp_path / "m.gguf",
        [("chunk8192", 8192), ("chunk4096", 4096)],
        reps=2,
        out=tmp_path / "out",
        ctx_start=2048,
        ctx_max=2048,
        step=2048,
        gen=32,
        owner_pid=1234,
    )
    binaries = {a[0] for a in seen}
    assert len(binaries) == 1, "both arms must run the same ds4-bench build"
    assert str(tree) in next(iter(binaries))


def test_two_equal_chunks_are_refused(tmp_path) -> None:
    """chunk_a == chunk_b is two arms wearing different labels (#162's trap)."""
    tree = a_tree(tmp_path)
    with pytest.raises(decode_ab.Refusal):
        pc.sweep(
            tree,
            tmp_path / "m.gguf",
            [("a", 8192), ("b", 8192)],
            reps=2,
            out=tmp_path / "out",
            owner_pid=1234,
        )


def test_an_odd_rep_count_is_refused(tmp_path) -> None:
    """Refused, not warned: an odd sweep produces a plausible number and
    silently drops half the position-bias design (#130, #201)."""
    tree = a_tree(tmp_path)
    with pytest.raises(decode_ab.Refusal):
        pc.sweep(
            tree,
            tmp_path / "m.gguf",
            [("chunk8192", 8192), ("chunk4096", 4096)],
            reps=3,
            out=tmp_path / "out",
            owner_pid=1234,
        )


def test_a_missing_build_is_refused(tmp_path) -> None:
    tree = tmp_path / "bare"
    (tree / "speed-bench").mkdir(parents=True)
    (tree / "speed-bench" / "promessi_sposi.txt").write_text("p\n")
    with pytest.raises(decode_ab.Refusal):
        pc.sweep(
            tree,
            tmp_path / "m.gguf",
            [("chunk8192", 8192), ("chunk4096", 4096)],
            reps=2,
            out=tmp_path / "out",
            owner_pid=1234,
        )


def test_the_binary_check_happens_before_the_lock(tmp_path, monkeypatch) -> None:
    """The ORDER is the claim: refuse a typo before 84 GiB is resident."""

    def explode(*a, **kw):
        raise AssertionError("lock taken before the build was checked")

    monkeypatch.setattr(decode_ab, "run_lock", explode)
    tree = tmp_path / "bare"
    (tree / "speed-bench").mkdir(parents=True)
    (tree / "speed-bench" / "promessi_sposi.txt").write_text("p\n")
    with pytest.raises(decode_ab.Refusal):
        pc.sweep(
            tree,
            tmp_path / "m.gguf",
            [("chunk8192", 8192), ("chunk4096", 4096)],
            reps=2,
            out=tmp_path / "out",
            owner_pid=1234,
        )


# ------------------------------------------------ admission: the chunk took


def test_effective_chunk_reads_the_prefill_cap_line(tmp_path) -> None:
    """The log states the cap ds4 actually used; a null-by-clamping shows
    here as two arms with the same effective cap."""
    log = (
        "ds4-bench: context buffers 1464.23 MiB (ctx=32897, backend=metal, "
        "prefill_chunk=8192, raw_kv_rows=8192, compressed_kv_rows=8226)\n"
    )
    assert pc.effective_chunk(log) == 8192
    assert pc.effective_chunk("no cap here\n") is None
