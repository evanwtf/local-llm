"""scripts/videogen/judge.py: the blind folders for the judge, and the
ingest of its reply into the gallery's score files (#1406).

Ingest validation is tested on the negative cases first: a wrong id, item or
defect type would write a score that silently belongs to the wrong run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import subprocess
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import gallery, judge
from videogen.common import InsideRepo

DEFECTS = ["extra legs", "body morph", "objects appear"]

PROMPTS = {
    "version": "v1",
    "defects": DEFECTS,
    "clips": [
        {
            "id": "dog-a-v1",
            "ref": "dog-a.mp4",
            "prompt": "Two dogs.",
            "checklist": ["Two dogs.", "Green lawn.", "A hose."],
        },
        {
            "id": "dog-b-v1",
            "ref": "dog-b.mp4",
            "prompt": "One dog.",
            "checklist": ["One dog.", "Sun."],
        },
    ],
}

ARMS = ("h3-t2v", "wan-t2v")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_run(root: pathlib.Path, arm: str, clip: str, seed: int) -> None:
    """A scored clip under out/ and its frames under frames-out/."""
    clip_bytes = f"mp4 {arm} {clip} {seed}".encode()
    out = root / "out" / arm / f"{clip}-s{seed}"
    out.mkdir(parents=True)
    (out / "clip.mp4").write_bytes(clip_bytes)
    frames = root / "frames-out" / arm / f"{clip}-s{seed}"
    frames.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for name in ("s01.jpg", "s02.jpg", "strip.jpg", "first.png"):
        data = f"{name} {arm} {clip} {seed}".encode()
        (frames / name).write_bytes(data)
        hashes[name] = sha(data)
    meta = {
        "clip": "clip.mp4",
        "clip_sha256": sha(clip_bytes),
        "outputs_sha256": hashes,
    }
    (frames / "meta.json").write_text(json.dumps(meta))


@pytest.fixture
def prompts_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(PROMPTS))
    return path


@pytest.fixture
def vg_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "vg"
    for arm in ARMS:
        for clip in ("dog-a", "dog-b"):
            for seed in (1, 2):
                make_run(root, arm, clip, seed)
    return root


def do_blind(
    vg_root: pathlib.Path,
    prompts_file: pathlib.Path,
    out: pathlib.Path,
    seed: int = 7,
    clips: tuple[str, ...] = ("dog-a", "dog-b"),
) -> dict[str, Any]:
    judge.blind(
        list(ARMS),
        clips=list(clips),
        out=out,
        seed=seed,
        root=vg_root,
        prompts_file=prompts_file,
    )
    key: dict[str, Any] = json.loads((out / "key.json").read_text())
    return key


@pytest.fixture
def blind_dir(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> pathlib.Path:
    out = tmp_path / "blind"
    do_blind(vg_root, prompts_file, out)
    return out


def ids_for(blind_dir: pathlib.Path, clip: str) -> list[str]:
    key = json.loads((blind_dir / "key.json").read_text())
    return sorted(i for i, run in key["runs"].items() if run["clip"] == clip)


def good_entry(n_items: int, n_true: int, defects: int = 0) -> dict[str, Any]:
    return {
        "items": [
            {"n": n, "true": n <= n_true, "why": f"s01: item {n}"}
            for n in range(1, n_items + 1)
        ],
        "defects": [{"type": DEFECTS[0], "frame": "strip 0.25", "note": "five legs"}]
        * defects,
    }


def good_answers(blind_dir: pathlib.Path, clip: str = "dog-a") -> dict[str, Any]:
    n = 3 if clip == "dog-a" else 2
    return {i: good_entry(n, 2, defects=1) for i in ids_for(blind_dir, clip)}


def write_answers(tmp_path: pathlib.Path, answers: object) -> pathlib.Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers))
    return path


def do_ingest(
    blind_dir: pathlib.Path,
    answers: pathlib.Path,
    scores: pathlib.Path,
    *,
    overwrite: bool = False,
) -> list[pathlib.Path]:
    return judge.ingest(
        blind_dir,
        answers,
        judge_label="test judge",
        scores_root=scores,
        overwrite=overwrite,
    )


def all_files(path: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in path.rglob("*") if p.is_file())


# ------------------------------------------------------------------ blind ----


def test_blind_copies_only_stills_and_strip(blind_dir: pathlib.Path) -> None:
    key = json.loads((blind_dir / "key.json").read_text())
    assert len(key["runs"]) == 8
    for run_id, run in key["runs"].items():
        assert len(run_id) == 6 and run_id.isalnum()
        folder = blind_dir / run["clip"] / run_id
        assert sorted(p.name for p in folder.iterdir()) == [
            "s01.jpg",
            "s02.jpg",
            "strip.jpg",
        ]
        src = pathlib.Path(run["run_dir"])
        assert (folder / "s01.jpg").read_bytes() == (src / "s01.jpg").read_bytes()
        assert run["arm"] in ARMS
        assert (
            run["clip_sha256"]
            == json.loads((src / "meta.json").read_text())["clip_sha256"]
        )
    assert not list(blind_dir.rglob("first.png"))
    assert not list(blind_dir.rglob("meta.json"))


def test_blind_judge_files_do_not_name_the_arm_or_seed(
    blind_dir: pathlib.Path,
) -> None:
    for clip in ("dog-a", "dog-b"):
        for path in all_files(blind_dir / clip):
            rel = str(path.relative_to(blind_dir))
            for word in ("h3", "wan", "-s1", "-s2", "seed"):
                assert word not in rel
            if path.name == "PROMPT.md":
                text = path.read_text()
                for word in ("h3", "wan", "t2v", "seed", "-s1", "-s2"):
                    assert word not in text.lower()


def test_blind_prompt_lists_folders_checklist_and_defects(
    blind_dir: pathlib.Path,
) -> None:
    text = (blind_dir / "dog-a" / "PROMPT.md").read_text()
    for run_id in ids_for(blind_dir, "dog-a"):
        assert f"- clip {run_id}: {blind_dir.resolve() / 'dog-a' / run_id}/" in text
    for run_id in ids_for(blind_dir, "dog-b"):
        assert run_id not in text
    assert "1. Two dogs.\n2. Green lawn.\n3. A hose." in text
    assert "One dog." not in text
    for defect in DEFECTS:
        assert f"- {defect}" in text
    assert "You are a blind judge for a video evaluation." in text
    assert "<one line per clip" not in text and "<numbered checklist>" not in text


def test_blind_is_reproducible_by_seed(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    a = do_blind(vg_root, prompts_file, tmp_path / "a", seed=5)
    b = do_blind(vg_root, prompts_file, tmp_path / "b", seed=5)
    c = do_blind(vg_root, prompts_file, tmp_path / "c", seed=6)
    assert a["runs"] == b["runs"]
    order_a = (tmp_path / "a" / "dog-a" / "PROMPT.md").read_text()
    order_b = (tmp_path / "b" / "dog-a" / "PROMPT.md").read_text()
    assert order_a.replace(str(tmp_path / "a"), "") == order_b.replace(
        str(tmp_path / "b"), ""
    )
    assert set(a["runs"]) != set(c["runs"])


def test_blind_one_clip_only(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    key = do_blind(vg_root, prompts_file, tmp_path / "o", clips=("dog-b",))
    assert {r["clip"] for r in key["runs"].values()} == {"dog-b"}
    assert not (tmp_path / "o" / "dog-a").exists()


def test_blind_refuses_non_empty_out(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    out = tmp_path / "busy"
    out.mkdir()
    (out / "x").write_text("x")
    with pytest.raises(judge.Refused, match="not empty"):
        do_blind(vg_root, prompts_file, out)
    assert sorted(p.name for p in out.iterdir()) == ["x"]


def test_blind_accepts_an_empty_out(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    out = tmp_path / "empty"
    out.mkdir()
    do_blind(vg_root, prompts_file, out)
    assert (out / "key.json").is_file()


def test_blind_refuses_out_inside_a_git_work_tree(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(InsideRepo):
        do_blind(vg_root, prompts_file, repo / "blind")
    assert not (repo / "blind").exists()


def test_blind_refuses_out_path_that_names_an_arm(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    with pytest.raises(judge.Refused, match="names"):
        do_blind(vg_root, prompts_file, tmp_path / "h3-vs-wan")


def test_blind_refuses_frames_of_another_clip(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    (vg_root / "out" / "wan-t2v" / "dog-a-s2" / "clip.mp4").write_bytes(b"new")
    with pytest.raises(judge.Refused, match="clip_sha256"):
        do_blind(vg_root, prompts_file, tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_blind_refuses_a_frame_that_differs_from_meta(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    (vg_root / "frames-out" / "h3-t2v" / "dog-b-s1" / "s02.jpg").write_bytes(b"x")
    with pytest.raises(judge.Refused, match="s02.jpg"):
        do_blind(vg_root, prompts_file, tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_blind_refuses_an_arm_with_no_runs(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    with pytest.raises(judge.Refused, match="no scored run"):
        judge.blind(
            ["h3-t2v", "h3-i2v"],
            clips=["dog-a"],
            out=tmp_path / "o",
            seed=1,
            root=vg_root,
            prompts_file=prompts_file,
        )


def test_blind_skips_a_run_without_frames(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    (vg_root / "out" / "h3-t2v" / "dog-a-s3").mkdir()
    key = do_blind(vg_root, prompts_file, tmp_path / "o")
    assert len(key["runs"]) == 8


def test_blind_refuses_an_unknown_clip(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    with pytest.raises(judge.Refused, match="dog-z"):
        do_blind(vg_root, prompts_file, tmp_path / "o", clips=("dog-z",))


# ----------------------------------------------------------------- ingest ----


def test_ingest_writes_gallery_scores(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    scores = tmp_path / "scores"
    answers = good_answers(blind_dir)
    with caplog.at_level(logging.INFO):
        written = do_ingest(blind_dir, write_answers(tmp_path, answers), scores)
    assert len(written) == 4
    key = json.loads((blind_dir / "key.json").read_text())
    for run_id in answers:
        run = key["runs"][run_id]
        path = scores / run["arm"] / f"dog-a-s{run['seed']}.json"
        data = json.loads(path.read_text())
        score = gallery.parse_score(data)
        assert (score.passed, score.total, score.defects) == (2, 3, 1)
        assert score.judge == "test judge"
        assert data["schema"] == 1
        assert data["items"] == answers[run_id]["items"]
        assert data["defect_list"] == answers[run_id]["defects"]
        assert data["blind_id"] == run_id
    assert "h3-t2v: 2 runs, adherence 66.7%, 2 defects" in caplog.text
    assert "wan-t2v: 2 runs, adherence 66.7%, 2 defects" in caplog.text


def test_ingest_unknown_id_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(blind_dir)
    answers["zzzzzz"] = good_entry(3, 3)
    with pytest.raises(judge.Refused, match="zzzzzz"):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


def test_ingest_missing_item_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(blind_dir)
    first = min(answers)
    answers[first]["items"].pop()
    with pytest.raises(judge.Refused, match="missing item 3"):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


def test_ingest_duplicate_item_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(blind_dir)
    first = min(answers)
    answers[first]["items"][2]["n"] = 1
    with pytest.raises(judge.Refused, match="item 1 more than once"):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"n": 4}, "item 4 is outside 1..3"),
        ({"n": 0}, "item 0 is outside 1..3"),
        ({"n": True}, "n must be a whole number"),
        ({"n": "1"}, "n must be a whole number"),
        ({"true": "yes"}, "true must be true or false"),
        ({"true": 1}, "true must be true or false"),
        ({"why": None}, "why must be text"),
    ],
)
def test_ingest_bad_item_refused(
    tmp_path: pathlib.Path,
    blind_dir: pathlib.Path,
    change: dict[str, Any],
    match: str,
) -> None:
    answers = good_answers(blind_dir)
    first = min(answers)
    answers[first]["items"][0].update(change)
    with pytest.raises(judge.Refused, match=match):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


def test_ingest_bad_defect_type_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(blind_dir)
    first = min(answers)
    answers[first]["defects"][0]["type"] = "extra leg"
    with pytest.raises(judge.Refused, match="not in the fixed list"):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


@pytest.mark.parametrize(
    "answers",
    [
        [],
        {"x": 1},
    ],
)
def test_ingest_bad_shape_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path, answers: object
) -> None:
    with pytest.raises(judge.Refused):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


def test_ingest_entry_with_unknown_field_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(blind_dir)
    answers[min(answers)]["score"] = 10
    with pytest.raises(judge.Refused, match="score"):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")


def test_ingest_not_json_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    path = tmp_path / "answers.json"
    path.write_text("Here is the JSON: {")
    with pytest.raises(judge.Refused, match="JSON"):
        do_ingest(blind_dir, path, tmp_path / "scores")


def test_ingest_incomplete_clip_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(blind_dir)
    dropped = min(answers)
    del answers[dropped]
    with pytest.raises(judge.Refused, match=f"no answer for {dropped}"):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


def test_ingest_one_bad_id_writes_nothing(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = {**good_answers(blind_dir, "dog-a"), **good_answers(blind_dir, "dog-b")}
    last = ids_for(blind_dir, "dog-b")[-1]
    answers[last]["defects"].append({"type": "nope", "frame": "s01", "note": ""})
    with pytest.raises(judge.Refused):
        do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "scores")
    assert not (tmp_path / "scores").exists()


def test_ingest_overwrite_refused_and_writes_nothing(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    scores = tmp_path / "scores"
    key = json.loads((blind_dir / "key.json").read_text())
    answers = good_answers(blind_dir)
    existing_id = min(answers)
    run = key["runs"][existing_id]
    existing = scores / run["arm"] / f"dog-a-s{run['seed']}.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("old")
    with pytest.raises(judge.Refused, match="--overwrite"):
        do_ingest(blind_dir, write_answers(tmp_path, answers), scores)
    assert all_files(scores) == [existing]
    assert existing.read_text() == "old"


def test_ingest_overwrite_flag_replaces(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    scores = tmp_path / "scores"
    path = write_answers(tmp_path, good_answers(blind_dir))
    do_ingest(blind_dir, path, scores)
    answers = {i: good_entry(3, 3) for i in ids_for(blind_dir, "dog-a")}
    do_ingest(blind_dir, write_answers(tmp_path, answers), scores, overwrite=True)
    for f in all_files(scores):
        assert json.loads(f.read_text())["adherence"]["passed"] == 3
    assert not [p for p in scores.rglob("*") if p.name.startswith(".")]


def test_ingest_two_clips_in_one_file(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = {**good_answers(blind_dir, "dog-a"), **good_answers(blind_dir, "dog-b")}
    written = do_ingest(blind_dir, write_answers(tmp_path, answers), tmp_path / "s")
    assert len(written) == 8


def test_ingest_refuses_scores_root_in_git(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(InsideRepo):
        do_ingest(
            blind_dir, write_answers(tmp_path, good_answers(blind_dir)), repo / "s"
        )


def test_main_returns_2_on_refusal(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(blind_dir)
    answers["zzzzzz"] = good_entry(3, 3)
    rc = judge.main(
        [
            "ingest",
            "--blind",
            str(blind_dir),
            "--answers",
            str(write_answers(tmp_path, answers)),
            "--judge",
            "x",
            "--scores-root",
            str(tmp_path / "scores"),
        ]
    )
    assert rc == judge.REFUSED
    assert not (tmp_path / "scores").exists()


def test_main_blind_then_ingest(
    tmp_path: pathlib.Path, vg_root: pathlib.Path, prompts_file: pathlib.Path
) -> None:
    out = tmp_path / "b"
    rc = judge.main(
        [
            "blind",
            "--arms",
            *ARMS,
            "--clips",
            "dog-b",
            "--out",
            str(out),
            "--seed",
            "3",
            "--root",
            str(vg_root),
            "--prompts",
            str(prompts_file),
        ]
    )
    assert rc == 0
    answers = good_answers(out, "dog-b")
    rc = judge.main(
        [
            "ingest",
            "--blind",
            str(out),
            "--answers",
            str(write_answers(tmp_path, answers)),
            "--judge",
            "x",
            "--scores-root",
            str(tmp_path / "scores"),
        ]
    )
    assert rc == 0
    assert len(all_files(tmp_path / "scores")) == 4
