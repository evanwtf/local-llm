"""#140: backfilled prompts are inferred, and an override is never guessed."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import backfill_prompt_meta as backfill
import prompt_meta


def _run(root: pathlib.Path, name: str, harness: str | None) -> pathlib.Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "q4-rep1.csv").write_text("ctx_tokens,prefill_tps\n2048,800\n")
    if harness is not None:
        (d / "start-state.txt").write_text(f"# harness: {harness}\n")
    return d


def _prompt(tmp_path) -> pathlib.Path:
    p = tmp_path / "promessi_sposi.txt"
    p.write_bytes(b"x" * 1329139)
    return p


def test_it_writes_an_inferred_sidecar_with_its_reasoning(tmp_path):
    root = tmp_path / "runs"
    d = _run(root, "run1", "scripts/decode_ab.sh q4 a.gguf q8 b.gguf")
    backfill.backfill(root, _prompt(tmp_path), apply=True)
    body = json.loads((d / prompt_meta.SIDECAR).read_text())
    assert body["inferred"] is True
    assert body["prompt_bytes"] == 1329139
    assert "91ca9ff" in body["why"]


def test_a_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "runs"
    d = _run(root, "run1", "scripts/decode_ab.sh q4 a.gguf q8 b.gguf")
    written, _, _ = backfill.backfill(root, _prompt(tmp_path), apply=False)
    assert written == [d]
    assert not (d / prompt_meta.SIDECAR).exists()


def test_a_run_whose_harness_line_overrides_prompt_is_skipped_not_guessed(tmp_path):
    root = tmp_path / "runs"
    d = _run(root, "run1", "PROMPT=/other/short.txt scripts/decode_ab.sh q4 a q8 b")
    written, _, skipped = backfill.backfill(root, _prompt(tmp_path), apply=True)
    assert written == []
    assert skipped == [d]
    assert not (d / prompt_meta.SIDECAR).exists()


def test_a_run_that_already_records_its_prompt_is_left_alone(tmp_path):
    root = tmp_path / "runs"
    d = _run(root, "run1", "scripts/decode_ab.sh q4 a.gguf q8 b.gguf")
    prompt_meta.write_sidecar(
        d, name="real.txt", size=7, sha256=None, inferred=False, why="recorded"
    )
    written, already, _ = backfill.backfill(root, _prompt(tmp_path), apply=True)
    assert written == [] and already == [d]
    assert (
        json.loads((d / prompt_meta.SIDECAR).read_text())["prompt_file"] == "real.txt"
    )


def test_it_finds_every_run_directory_under_the_root(tmp_path):
    root = tmp_path / "runs"
    _run(root, "a/run1", "scripts/decode_ab.sh q4 x q8 y")
    _run(root, "b/run2", "scripts/decode_ab.sh q4 x q8 y")
    assert len(backfill.run_dirs(root)) == 2
