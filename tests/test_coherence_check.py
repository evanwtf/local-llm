"""The check that has to run before a GGUF is trusted (#25, #48, #235).

`scripts/coherence_check.py` is the port of `scripts/coherence_check.sh`. It
starts a model, so nothing here starts one: every test drives the argv builder
and the refusals, and `child.run` is replaced by a recorder.
"""

from __future__ import annotations

import logging
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import coherence_check


@pytest.fixture
def tree(tmp_path) -> pathlib.Path:
    """A ds4 tree with an executable `ds4` in it, stated rather than assumed."""
    got = tmp_path / "ds4-tree"
    got.mkdir()
    binary = got / "ds4"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return got


@pytest.fixture
def gguf(tmp_path) -> pathlib.Path:
    got = tmp_path / "model-Q4_K.gguf"
    got.write_bytes(b"GGUF")
    return got


@pytest.fixture
def calls(monkeypatch) -> list[dict]:
    """Record what would have been spawned, and spawn nothing."""
    seen: list[dict] = []

    def fake_run(argv, *, cwd, log, timeout=None, **kw):
        seen.append({"argv": list(argv), "cwd": cwd, "log": log, "timeout": timeout})
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("def fib(n): ...\nThe iterative form avoids the call stack.\n")
        return 0

    monkeypatch.setattr(coherence_check.child, "run", fake_run)
    return seen


# --- the command line --------------------------------------------------------


def test_the_check_is_greedy(tree, gguf) -> None:
    """#25's noise was not reproducible under sampling, so a check that
    sampled could not be re-run against a fix."""
    argv = coherence_check.argv_for(tree, gguf, prompt="hi", tokens=200, ctx=8192)
    assert argv[argv.index("--temp") + 1] == "0"


def test_the_token_budget_reaches_ds4_rather_than_trimming_the_output(
    tree, gguf
) -> None:
    """The shell's defect, stated as a test.

    `TOKENS=${TOKENS:-200}` was spent on `tail -n "$TOKENS"`: it trimmed the
    OUTPUT and never bounded the generation, so `TOKENS=50` still paid for a
    full-length run. ds4 has the flag the name promised.
    """
    argv = coherence_check.argv_for(tree, gguf, prompt="hi", tokens=50, ctx=8192)
    assert argv[argv.index("--tokens") + 1] == "50"


@pytest.mark.skipif(
    not (coherence_check.DEFAULT_TREE / "ds4").is_file(), reason="no ds4 tree here"
)
def test_ds4_really_has_the_tokens_flag() -> None:
    """Read from the binary, not from memory.

    The port changed behaviour on the strength of `-n, --tokens N  Maximum
    generated tokens` in `./ds4 --help`. A flag this repo believes in because
    somebody typed it once is the failure this asserts against.
    """
    got = subprocess.run(
        [str(coherence_check.DEFAULT_TREE / "ds4"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert "--tokens" in got.stdout + got.stderr


def test_the_model_and_context_are_passed(tree, gguf) -> None:
    argv = coherence_check.argv_for(tree, gguf, prompt="hi", tokens=200, ctx=4096)
    assert argv[0] == str(tree / "ds4")
    assert argv[argv.index("-m") + 1] == str(gguf)
    assert argv[argv.index("--ctx") + 1] == "4096"
    assert argv[argv.index("-p") + 1] == "hi"


def test_the_prompt_asks_for_code_AND_prose() -> None:
    """They fail separately: #25's model wrote clean Python and explained it
    in word salad. Either half alone would have passed it."""
    assert "Python function" in coherence_check.PROMPT
    assert "explain in one sentence" in coherence_check.PROMPT


# --- the refusals, all of them before the first model loads ------------------


def test_a_missing_gguf_is_named(tree, tmp_path) -> None:
    got = coherence_check.check_inputs(tree, [tmp_path / "absent.gguf"])
    assert got == [f"missing {tmp_path / 'absent.gguf'}"]


def test_a_missing_tree_is_named(tmp_path, gguf) -> None:
    got = coherence_check.check_inputs(tmp_path / "no-tree", [gguf])
    assert got == [f"no ds4 tree at {tmp_path / 'no-tree'}"]


def test_a_tree_without_an_executable_ds4_is_named(tmp_path, gguf) -> None:
    """A build that failed leaves the tree and takes the binary."""
    tree = tmp_path / "ds4-tree"
    tree.mkdir()
    got = coherence_check.check_inputs(tree, [gguf])
    assert got == [f"{tree / 'ds4'} is not an executable file"]


def test_a_ds4_that_is_not_executable_is_named(tmp_path, gguf) -> None:
    tree = tmp_path / "ds4-tree"
    tree.mkdir()
    (tree / "ds4").write_text("#!/bin/sh\n")
    assert "not an executable" in coherence_check.check_inputs(tree, [gguf])[0]


def test_every_bad_path_is_reported_at_once(tree, tmp_path, gguf) -> None:
    """The shell found the fourth typo after three models had been paged in."""
    got = coherence_check.check_inputs(
        tree, [gguf, tmp_path / "a.gguf", tmp_path / "b.gguf"]
    )
    assert len(got) == 2


def test_nothing_starts_when_a_path_is_wrong(tree, tmp_path, calls, caplog) -> None:
    code = coherence_check.main(
        [str(tmp_path / "absent.gguf"), "--tree", str(tree), "--log-dir", str(tmp_path)]
    )
    assert code == 2
    assert calls == []


# --- running them ------------------------------------------------------------


def test_ds4_runs_from_its_own_tree(tree, gguf, tmp_path, calls) -> None:
    """ds4 resolves metal/*.metal relative to its own tree, so a run started
    anywhere else loads no Metal kernels."""
    coherence_check.main(
        [str(gguf), "--tree", str(tree), "--log-dir", str(tmp_path / "logs")]
    )
    assert calls[0]["cwd"] == tree


def test_every_model_gets_its_own_log(tree, tmp_path, calls) -> None:
    ggufs = []
    for name in ("one", "two"):
        got = tmp_path / f"{name}.gguf"
        got.write_bytes(b"GGUF")
        ggufs.append(str(got))
    coherence_check.main(
        [*ggufs, "--tree", str(tree), "--log-dir", str(tmp_path / "l")]
    )
    assert len(calls) == 2
    logs_written = [c["log"] for c in calls]
    assert len(set(logs_written)) == 2
    assert all("one" in str(logs_written[0]) for _ in [0])
    assert "two" in str(logs_written[1])


def test_a_model_that_fails_to_run_makes_the_check_fail(
    tree, gguf, tmp_path, monkeypatch
) -> None:
    """A ds4 that exits non-zero produced no output to read, which is not the
    same as output that read badly -- and only one of those is a pass."""

    def failing(argv, *, cwd, log, timeout=None, **kw):
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("failed to load model\n")
        return 1

    monkeypatch.setattr(coherence_check.child, "run", failing)
    code = coherence_check.main(
        [str(gguf), "--tree", str(tree), "--log-dir", str(tmp_path / "logs")]
    )
    assert code == 1


def test_a_clean_run_exits_zero(tree, gguf, tmp_path, calls) -> None:
    code = coherence_check.main(
        [str(gguf), "--tree", str(tree), "--log-dir", str(tmp_path / "logs")]
    )
    assert code == 0


def test_the_models_own_words_are_shown(tree, gguf, tmp_path, calls, capsys) -> None:
    """The whole point: a person reads this and decides prose or gibberish."""
    coherence_check.main(
        [str(gguf), "--tree", str(tree), "--log-dir", str(tmp_path / "logs")]
    )
    out = capsys.readouterr().out
    assert "The iterative form avoids the call stack." in out
    assert "MODEL: " + gguf.name in out


def test_the_transcript_carries_no_timestamp(
    tree, gguf, tmp_path, calls, capsys
) -> None:
    """200 lines of generated prose with a stamp glued to each one is the
    thing that makes reading it hard, which is what this script is for."""
    coherence_check.main(
        [str(gguf), "--tree", str(tree), "--log-dir", str(tmp_path / "logs")]
    )
    for line in capsys.readouterr().out.splitlines():
        if "iterative form" in line:
            assert not line.startswith("20"), line
    assert coherence_check.transcript.propagate is False


def test_the_drivers_own_lines_keep_the_iso_stamp(
    tree, gguf, tmp_path, calls, caplog
) -> None:
    """Only the model's words are exempt. The driver's are stamped like
    everything else this repo writes."""
    import logs

    assert logs.DATEFMT == "%Y-%m-%dT%H:%M:%S%z"
    with caplog.at_level(logging.INFO, logger="coherence_check"):
        coherence_check.main(
            [str(gguf), "--tree", str(tree), "--log-dir", str(tmp_path / "logs")]
        )
    assert any("MODEL" in r.message for r in caplog.records)
