"""#140: a prefill figure is not well-posed without naming the prompt."""

from __future__ import annotations

import csv
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import prompt_meta

ROWS = [
    {"ctx_tokens": "2048", "prefill_tps": "812.56"},
    {"ctx_tokens": "4096", "prefill_tps": "725.52"},
]


def write_csv(path: pathlib.Path, rows=ROWS) -> pathlib.Path:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


def test_describe_uses_kib_because_that_is_what_the_quoted_number_was():
    ref = prompt_meta.PromptRef("promessi_sposi.txt", 1329139, None, False)
    assert prompt_meta.describe(ref) == "promessi_sposi.txt (1298 KiB)"


def test_describe_marks_an_inferred_prompt_as_inferred():
    ref = prompt_meta.PromptRef("promessi_sposi.txt", 1329139, None, True)
    assert "inferred" in prompt_meta.describe(ref)


def test_stamp_adds_the_prompt_to_every_row(tmp_path):
    prompt = tmp_path / "p.txt"
    prompt.write_bytes(b"x" * 500)
    csv_path = write_csv(tmp_path / "q4-rep1.csv")
    prompt_meta.stamp(csv_path, prompt)
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 2
    for row in rows:
        assert row["prompt_file"] == "p.txt"
        assert row["prompt_bytes"] == "500"
    # The measurement itself must survive the stamp untouched.
    assert [r["prefill_tps"] for r in rows] == ["812.56", "725.52"]


def test_stamp_is_idempotent(tmp_path):
    prompt = tmp_path / "p.txt"
    prompt.write_bytes(b"x" * 7)
    csv_path = write_csv(tmp_path / "q4-rep1.csv")
    prompt_meta.stamp(csv_path, prompt)
    prompt_meta.stamp(csv_path, prompt)
    header = csv_path.read_text().splitlines()[0]
    assert header.count("prompt_file") == 1


def test_stamp_refuses_to_overwrite_a_different_prompt(tmp_path):
    one, two = tmp_path / "a.txt", tmp_path / "b.txt"
    one.write_bytes(b"x")
    two.write_bytes(b"yy")
    csv_path = write_csv(tmp_path / "q4-rep1.csv")
    prompt_meta.stamp(csv_path, one)
    with pytest.raises(ValueError, match="already stamped"):
        prompt_meta.stamp(csv_path, two)


def test_for_run_reads_the_stamp_off_the_rows(tmp_path):
    prompt = tmp_path / "promessi_sposi.txt"
    prompt.write_bytes(b"x" * 1329139)
    prompt_meta.stamp(write_csv(tmp_path / "q4-rep1.csv"), prompt)
    prompt_meta.stamp(write_csv(tmp_path / "q8-rep1.csv"), prompt)
    ref = prompt_meta.for_run(tmp_path)
    assert ref is not None
    assert (ref.name, ref.size, ref.inferred) == ("promessi_sposi.txt", 1329139, False)


def test_for_run_refuses_a_directory_whose_arms_used_different_prompts(tmp_path):
    one, two = tmp_path / "a.txt", tmp_path / "b.txt"
    one.write_bytes(b"x")
    two.write_bytes(b"yy")
    prompt_meta.stamp(write_csv(tmp_path / "q4-rep1.csv"), one)
    prompt_meta.stamp(write_csv(tmp_path / "q8-rep1.csv"), two)
    with pytest.raises(ValueError, match="two prompts"):
        prompt_meta.for_run(tmp_path)


def test_for_run_is_none_when_nothing_recorded_the_prompt(tmp_path):
    write_csv(tmp_path / "q4-rep1.csv")
    assert prompt_meta.for_run(tmp_path) is None


def test_the_sidecar_is_the_fallback_for_runs_measured_before_the_stamp(tmp_path):
    write_csv(tmp_path / "q4-rep1.csv")
    prompt_meta.write_sidecar(
        tmp_path,
        name="promessi_sposi.txt",
        size=1329139,
        sha256=None,
        inferred=True,
        why="decode_ab.sh default at the time of the run",
    )
    ref = prompt_meta.for_run(tmp_path)
    assert ref is not None
    assert ref.inferred is True
    assert ref.size == 1329139
    body = json.loads((tmp_path / "run-meta.json").read_text())
    assert body["why"]


def test_the_stamp_wins_over_the_sidecar(tmp_path):
    prompt = tmp_path / "real.txt"
    prompt.write_bytes(b"x" * 42)
    prompt_meta.stamp(write_csv(tmp_path / "q4-rep1.csv"), prompt)
    prompt_meta.write_sidecar(
        tmp_path, name="guess.txt", size=999, sha256=None, inferred=True, why="guess"
    )
    ref = prompt_meta.for_run(tmp_path)
    assert ref is not None
    assert (ref.name, ref.inferred) == ("real.txt", False)


def test_one_prompt_across_runs_is_reported_as_one(tmp_path):
    refs = [prompt_meta.PromptRef("p.txt", 100, None, False)] * 3
    assert prompt_meta.agree(refs) is not None


def test_mixed_prompts_across_runs_do_not_agree(tmp_path):
    refs = [
        prompt_meta.PromptRef("p.txt", 100, None, False),
        prompt_meta.PromptRef("q.txt", 400000, None, False),
    ]
    assert prompt_meta.agree(refs) is None


def test_a_missing_prompt_among_runs_does_not_agree():
    refs = [prompt_meta.PromptRef("p.txt", 100, None, False), None]
    assert prompt_meta.agree(refs) is None
