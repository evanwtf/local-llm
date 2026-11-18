"""scripts/videogen/judge.py: the blind folders for the judge, and the
ingest of its reply into the gallery's score files (#1406).

Ingest validation is tested on the negative cases first: a wrong id, item or
defect type would write a score that silently belongs to the wrong run.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
from collections.abc import MutableSequence
from typing import Any

import pytest
from PIL import Image, PngImagePlugin

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import gallery, judge
from videogen.common import InsideRepo

DEFECTS = [
    "extra legs",
    "body morph (a body changes shape)",
    "dog identity changes between frames (coat, size)",
    "objects appear",
]

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

ARMS_CFG = {
    "seeds": [1, 2],
    "arms": {
        "h3-t2v": {"model": "MiniMaxAI/MiniMax-H3"},
        "wan-t2v": {"model": "Wan-AI/Wan2.2-T2V-A14B-Diffusers"},
        "h3-i2v": {"model": "MiniMaxAI/MiniMax-H3"},
    },
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
    run = {"status": "ok", "output": {"sha256": sha(clip_bytes)}}
    (out / "run.json").write_text(json.dumps(run))
    frames = root / "frames-out" / arm / f"{clip}-s{seed}"
    frames.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for name in ("s01.jpg", "s02.jpg", "strip.jpg", "first.png"):
        data = f"{name} {arm} {clip} {seed}".encode()
        (frames / name).write_bytes(data)
        hashes[name] = sha(data)
    meta = {"clip_sha256": sha(clip_bytes), "outputs_sha256": hashes}
    (frames / "meta.json").write_text(json.dumps(meta))


@pytest.fixture
def prompts_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(PROMPTS))
    return path


@pytest.fixture
def arms_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "arms.json"
    path.write_text(json.dumps(ARMS_CFG))
    return path


@pytest.fixture
def vg_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "vg"
    for arm in ARMS:
        for clip in ("dog-a", "dog-b"):
            for seed in (1, 2):
                make_run(root, arm, clip, seed)
    return root


Env = tuple[pathlib.Path, pathlib.Path, pathlib.Path]


@pytest.fixture
def env(
    vg_root: pathlib.Path, prompts_file: pathlib.Path, arms_file: pathlib.Path
) -> Env:
    return vg_root, prompts_file, arms_file


def key_of(env: Env, out: pathlib.Path) -> pathlib.Path:
    return env[0].resolve() / "judge" / "keys" / f"{out.name}.json"


def do_blind(
    env: Env,
    out: pathlib.Path,
    *,
    arms: tuple[str, ...] = ARMS,
    clips: tuple[str, ...] = ("dog-a", "dog-b"),
    **kw: Any,
) -> dict[str, Any]:
    root, prompts, arms_file = env
    judge.blind(
        list(arms),
        clips=list(clips),
        out=out,
        root=root,
        prompts_file=prompts,
        arms_file=arms_file,
        **kw,
    )
    key: dict[str, Any] = json.loads(key_of(env, out).read_text())
    return key


@pytest.fixture
def blind_dir(tmp_path: pathlib.Path, env: Env) -> pathlib.Path:
    out = tmp_path / "blind"
    do_blind(env, out)
    return out


def load_key(env: Env, blind_dir: pathlib.Path) -> dict[str, Any]:
    key: dict[str, Any] = json.loads(key_of(env, blind_dir).read_text())
    return key


def ids_for(env: Env, blind_dir: pathlib.Path, clip: str) -> list[str]:
    runs = load_key(env, blind_dir)["runs"]
    return sorted(i for i, run in runs.items() if run["clip"] == clip)


def good_entry(n_items: int, n_true: int, defects: int = 0) -> dict[str, Any]:
    return {
        "items": [
            {"n": n, "true": n <= n_true, "why": f"s01: item {n}"}
            for n in range(1, n_items + 1)
        ],
        "defects": [{"type": DEFECTS[0], "frame": "strip 0.25", "note": "five legs"}]
        * defects,
    }


def good_answers(
    env: Env, blind_dir: pathlib.Path, clip: str = "dog-a"
) -> dict[str, Any]:
    n = 3 if clip == "dog-a" else 2
    return {i: good_entry(n, 2, defects=1) for i in ids_for(env, blind_dir, clip)}


def write_answers(tmp_path: pathlib.Path, answers: object) -> pathlib.Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers))
    return path


def do_ingest(
    blind_dir: pathlib.Path, answers: pathlib.Path, **kw: Any
) -> list[pathlib.Path]:
    # The fixtures put the root beside the blind dir. Pass it always, so no
    # test can fall back to the real ~/bench-logs root.
    kw.setdefault("root", blind_dir.parent / "vg")
    return judge.ingest(blind_dir, answers, judge_label="test judge", **kw)


def all_files(path: pathlib.Path) -> list[pathlib.Path]:
    if not path.exists():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file())


def scores_of(env: Env) -> pathlib.Path:
    return env[0] / "scores"


# ------------------------------------------------------------------ blind ----


def test_blind_copies_only_stills_and_strip(env: Env, blind_dir: pathlib.Path) -> None:
    key = load_key(env, blind_dir)
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
        clip = pathlib.Path(run["clip_path"])
        assert clip == env[0].resolve() / "out" / run["arm"] / src.name / "clip.mp4"
        assert run["clip_sha256"] == sha(clip.read_bytes())
    assert key["root"] == str(env[0].resolve())
    assert not list(blind_dir.rglob("first.png"))
    assert not list(blind_dir.rglob("meta.json"))
    assert not list(blind_dir.rglob("*.json"))


def test_blind_key_is_outside_the_tree_and_private(
    env: Env, blind_dir: pathlib.Path
) -> None:
    path = key_of(env, blind_dir)
    assert not path.is_relative_to(blind_dir)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_blind_refuses_a_key_under_out(tmp_path: pathlib.Path, env: Env) -> None:
    out = tmp_path / "o"
    with pytest.raises(judge.Refused, match="where the judge reads"):
        judge.blind(
            list(ARMS),
            clips=["dog-a"],
            out=out,
            root=env[0],
            key_path=out / "k.json",
            prompts_file=env[1],
            arms_file=env[2],
        )
    assert not out.exists()


def test_blind_refuses_an_existing_key(tmp_path: pathlib.Path, env: Env) -> None:
    out = tmp_path / "o"
    key = key_of(env, out)
    key.parent.mkdir(parents=True)
    key.write_text("{}")
    with pytest.raises(judge.Refused, match="exists"):
        do_blind(env, out)
    assert key.read_text() == "{}"


def test_blind_judge_files_do_not_name_the_arm_or_seed(
    blind_dir: pathlib.Path,
) -> None:
    for clip in ("dog-a", "dog-b"):
        for path in all_files(blind_dir / clip):
            rel = str(path.relative_to(blind_dir))
            for word in ("h3", "wan", "-s1", "-s2", "seed"):
                assert word not in rel
            if path.name == "PROMPT.md":
                text = path.read_text().lower()
                for word in ("h3", "wan", "minimax", "t2v", "seed", "-s1", "-s2"):
                    assert word not in text


def test_blind_prompt_lists_folders_checklist_and_numbered_defects(
    env: Env, blind_dir: pathlib.Path
) -> None:
    text = (blind_dir / "dog-a" / "PROMPT.md").read_text()
    for run_id in ids_for(env, blind_dir, "dog-a"):
        assert f"- clip {run_id}: {blind_dir.resolve() / 'dog-a' / run_id}/" in text
    for run_id in ids_for(env, blind_dir, "dog-b"):
        assert run_id not in text
    assert "1. Two dogs.\n2. Green lawn.\n3. A hose." in text
    assert "One dog." not in text
    for n, defect in enumerate(DEFECTS, 1):
        assert f"{n}. {defect}" in text
    assert "You are a blind judge for a video evaluation." in text
    assert "<one line per clip" not in text and "<numbered checklist>" not in text


def test_blind_ids_are_not_reproducible(tmp_path: pathlib.Path, env: Env) -> None:
    a = do_blind(env, tmp_path / "a")
    b = do_blind(env, tmp_path / "b")
    assert set(a["runs"]).isdisjoint(b["runs"])


def test_blind_sets_one_mtime_on_every_file_and_folder(
    blind_dir: pathlib.Path,
) -> None:
    paths = [blind_dir, *blind_dir.rglob("*")]
    assert len(paths) > 30
    assert {int(p.stat().st_mtime) for p in paths} == {judge.FIXED_MTIME}


def test_blind_one_clip_only(tmp_path: pathlib.Path, env: Env) -> None:
    key = do_blind(env, tmp_path / "o", clips=("dog-b",))
    assert {r["clip"] for r in key["runs"].values()} == {"dog-b"}
    assert not (tmp_path / "o" / "dog-a").exists()


def test_blind_refuses_non_empty_out(tmp_path: pathlib.Path, env: Env) -> None:
    out = tmp_path / "busy"
    out.mkdir()
    (out / "x").write_text("x")
    with pytest.raises(judge.Refused, match="not empty"):
        do_blind(env, out)
    assert sorted(p.name for p in out.iterdir()) == ["x"]


def test_blind_accepts_an_empty_out(tmp_path: pathlib.Path, env: Env) -> None:
    out = tmp_path / "t2v-v1"
    out.mkdir()
    do_blind(env, out)
    assert (out / "dog-a" / "PROMPT.md").is_file()


def test_blind_refuses_out_inside_a_git_work_tree(
    tmp_path: pathlib.Path, env: Env
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(InsideRepo):
        do_blind(env, repo / "blind")
    assert not (repo / "blind").exists()


@pytest.mark.parametrize(
    "name",
    ["h3-vs-x", "the-wan-set", "MiniMax-review", "wan2.2-check", "minimax-h3"],
)
def test_blind_refuses_out_path_that_names_a_model(
    tmp_path: pathlib.Path, env: Env, name: str
) -> None:
    with pytest.raises(judge.Refused, match="names"):
        do_blind(env, tmp_path / name)
    assert not (tmp_path / name).exists()


def test_blind_refuses_frames_of_another_clip(tmp_path: pathlib.Path, env: Env) -> None:
    (env[0] / "out" / "wan-t2v" / "dog-a-s2" / "clip.mp4").write_bytes(b"new")
    with pytest.raises(judge.Refused, match="clip_sha256"):
        do_blind(env, tmp_path / "o")
    assert not (tmp_path / "o").exists()


def test_blind_refuses_a_frame_that_differs_from_meta(
    tmp_path: pathlib.Path, env: Env
) -> None:
    (env[0] / "frames-out" / "h3-t2v" / "dog-b-s1" / "s02.jpg").write_bytes(b"x")
    with pytest.raises(judge.Refused, match="s02.jpg"):
        do_blind(env, tmp_path / "o")
    assert not (tmp_path / "o").exists()
    assert not key_of(env, tmp_path / "o").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".o.")]


@pytest.mark.parametrize("change", ["remove", "extra"])
def test_blind_refuses_stills_unlike_meta(
    tmp_path: pathlib.Path, env: Env, change: str
) -> None:
    frames = env[0] / "frames-out" / "h3-t2v" / "dog-a-s1"
    if change == "remove":
        (frames / "s02.jpg").unlink()
    else:
        (frames / "s03.jpg").write_bytes(b"extra")
    with pytest.raises(judge.Refused, match="stills on disk"):
        do_blind(env, tmp_path / "o")


def test_blind_refuses_meta_that_is_not_an_object(
    tmp_path: pathlib.Path, env: Env
) -> None:
    (env[0] / "frames-out" / "h3-t2v" / "dog-a-s1" / "meta.json").write_text("[]")
    with pytest.raises(judge.Refused, match="must hold a JSON object"):
        do_blind(env, tmp_path / "o")


def test_blind_refuses_a_missing_run(
    tmp_path: pathlib.Path, env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    (env[0] / "out" / "wan-t2v" / "dog-b-s2" / "run.json").unlink()
    with caplog.at_level(logging.WARNING), pytest.raises(judge.Refused, match="1 exp"):
        do_blind(env, tmp_path / "o")
    assert "missing run wan-t2v dog-b-s2" in caplog.text
    assert not (tmp_path / "o").exists()


def test_blind_allow_missing_blinds_the_rest(
    tmp_path: pathlib.Path, env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    (env[0] / "out" / "wan-t2v" / "dog-b-s2" / "run.json").unlink()
    with caplog.at_level(logging.WARNING):
        key = do_blind(env, tmp_path / "o", allow_missing=True)
    assert len(key["runs"]) == 7
    assert len(key["missing"]) == 1
    assert "missing run wan-t2v dog-b-s2" in caplog.text


def test_blind_counts_a_failed_run_as_missing(
    tmp_path: pathlib.Path, env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    run_json = env[0] / "out" / "h3-t2v" / "dog-a-s1" / "run.json"
    run_json.write_text(json.dumps({"status": "failed"}))
    with caplog.at_level(logging.WARNING):
        key = do_blind(env, tmp_path / "o", allow_missing=True)
    assert len(key["runs"]) == 7
    assert "'failed'" in caplog.text


def test_blind_refuses_an_arm_with_no_runs(tmp_path: pathlib.Path, env: Env) -> None:
    with pytest.raises(judge.Refused, match="no scored run"):
        do_blind(env, tmp_path / "o", arms=("h3-t2v", "h3-i2v"), allow_missing=True)


def test_blind_refuses_an_arm_not_in_the_arms_file(
    tmp_path: pathlib.Path, env: Env
) -> None:
    with pytest.raises(judge.Refused, match="not arms"):
        do_blind(env, tmp_path / "o", arms=("h3-t2v", "ltx-t2v"))


def test_blind_refuses_an_unknown_clip(tmp_path: pathlib.Path, env: Env) -> None:
    with pytest.raises(judge.Refused, match="dog-z"):
        do_blind(env, tmp_path / "o", clips=("dog-z",))


def test_load_prompts_refuses_an_empty_checklist(tmp_path: pathlib.Path) -> None:
    bad = json.loads(json.dumps(PROMPTS))
    bad["clips"][1]["checklist"] = []
    path = tmp_path / "p.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(judge.Refused, match="empty checklist"):
        judge.load_prompts(path)


# ----------------------------------------------------------------- ingest ----


def test_ingest_writes_gallery_scores(
    tmp_path: pathlib.Path,
    env: Env,
    blind_dir: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    answers = good_answers(env, blind_dir)
    with caplog.at_level(logging.INFO):
        written = do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert len(written) == 4
    key = load_key(env, blind_dir)
    for run_id in answers:
        run = key["runs"][run_id]
        path = scores_of(env) / run["arm"] / f"dog-a-s{run['seed']}.json"
        data = json.loads(path.read_text())
        score = gallery.parse_score(data)
        assert (score.passed, score.total, score.defects) == (2, 3, 1)
        assert score.judge == "test judge"
        assert data["schema"] == 1
        assert data["items"] == answers[run_id]["items"]
        assert data["defect_list"] == answers[run_id]["defects"]
        assert data["blind_id"] == run_id
        assert data["clip_sha256"] == run["clip_sha256"]
    assert "h3-t2v: 2 runs, adherence 66.7%, 2 defects" in caplog.text
    assert "wan-t2v: 2 runs, adherence 66.7%, 2 defects" in caplog.text


@pytest.mark.parametrize(
    "written",
    [DEFECTS[1], "body morph", 2, "2"],
)
def test_ingest_accepts_full_short_and_numbered_defects(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path, written: object
) -> None:
    answers = good_answers(env, blind_dir)
    first = min(answers)
    answers[first]["defects"][0]["type"] = written
    do_ingest(blind_dir, write_answers(tmp_path, answers))
    run = load_key(env, blind_dir)["runs"][first]
    path = scores_of(env) / run["arm"] / f"dog-a-s{run['seed']}.json"
    defect = json.loads(path.read_text())["defect_list"][0]
    assert defect["type"] == DEFECTS[1]
    if written == DEFECTS[1]:
        assert "type_as_written" not in defect
    else:
        assert defect["type_as_written"] == written


@pytest.mark.parametrize("written", ["extra leg", "body", 0, 5, "9", True, None])
def test_ingest_bad_defect_type_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path, written: object
) -> None:
    answers = good_answers(env, blind_dir)
    answers[min(answers)]["defects"][0]["type"] = written
    with pytest.raises(judge.Refused, match="not in the fixed list"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def test_defect_labels_drop_a_shared_short_form() -> None:
    labels = judge.defect_labels(["legs (extra)", "legs (missing)", "flicker"])
    assert "legs" not in labels
    assert labels["legs (missing)"] == "legs (missing)"
    assert labels[3] == labels["3"] == labels["flicker"] == "flicker"


def test_ingest_unknown_id_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    answers["zzzzzz"] = good_entry(3, 3)
    with pytest.raises(judge.Refused, match="zzzzzz"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def test_ingest_duplicate_id_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    first = min(answers)
    text = json.dumps(answers)
    entry = json.dumps(good_entry(3, 0))
    path = tmp_path / "answers.json"
    path.write_text(text[:-1] + f', "{first}": {entry}}}')
    with pytest.raises(judge.Refused, match=f"duplicate key '{first}'"):
        do_ingest(blind_dir, path)
    assert all_files(scores_of(env)) == []


def test_ingest_duplicate_nested_key_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    text = json.dumps(answers).replace('"true": true', '"true": true, "true": false', 1)
    path = tmp_path / "answers.json"
    path.write_text(text)
    with pytest.raises(judge.Refused, match="duplicate key 'true'"):
        do_ingest(blind_dir, path)
    assert all_files(scores_of(env)) == []


def test_ingest_missing_item_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    answers[min(answers)]["items"].pop()
    with pytest.raises(judge.Refused, match="missing item 3"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def test_ingest_duplicate_item_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    answers[min(answers)]["items"][2]["n"] = 1
    with pytest.raises(judge.Refused, match="item 1 more than once"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


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
    env: Env,
    blind_dir: pathlib.Path,
    change: dict[str, Any],
    match: str,
) -> None:
    answers = good_answers(env, blind_dir)
    answers[min(answers)]["items"][0].update(change)
    with pytest.raises(judge.Refused, match=match):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


@pytest.mark.parametrize("answers", [[], {}, {"x": 1}])
def test_ingest_bad_shape_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path, answers: object
) -> None:
    with pytest.raises(judge.Refused):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def test_ingest_entry_with_unknown_field_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    answers[min(answers)]["score"] = 10
    with pytest.raises(judge.Refused, match="score"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))


def test_ingest_not_json_refused(
    tmp_path: pathlib.Path, blind_dir: pathlib.Path
) -> None:
    path = tmp_path / "answers.json"
    path.write_text("Here is the JSON: {")
    with pytest.raises(judge.Refused, match="JSON"):
        do_ingest(blind_dir, path)


def test_ingest_incomplete_clip_refused(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    dropped = min(answers)
    del answers[dropped]
    with pytest.raises(judge.Refused, match=f"no answer for {dropped}"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def test_ingest_one_bad_id_writes_nothing(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = {
        **good_answers(env, blind_dir, "dog-a"),
        **good_answers(env, blind_dir, "dog-b"),
    }
    last = ids_for(env, blind_dir, "dog-b")[-1]
    answers[last]["defects"].append({"type": "nope", "frame": "s01", "note": ""})
    with pytest.raises(judge.Refused):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def test_ingest_refuses_a_clip_remade_after_blind(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    run = load_key(env, blind_dir)["runs"][max(answers)]
    pathlib.Path(run["clip_path"]).write_bytes(b"remade")
    with pytest.raises(judge.Refused, match="changed since the blind run"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def test_ingest_refuses_a_clip_that_is_gone(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    run = load_key(env, blind_dir)["runs"][max(answers)]
    pathlib.Path(run["clip_path"]).unlink()
    with pytest.raises(judge.Refused, match="is gone"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == []


def old_layout(env: Env, blind_dir: pathlib.Path) -> pathlib.Path:
    """The first key format: inside the blind dir, no root, no clip path."""
    key = load_key(env, blind_dir)
    del key["root"]
    for run in key["runs"].values():
        del run["clip_path"]
    path = blind_dir / "key.json"
    path.write_text(json.dumps(key))
    key_of(env, blind_dir).unlink()
    return path


def test_ingest_accepts_the_first_key_format(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = write_answers(tmp_path, good_answers(env, blind_dir))
    key = old_layout(env, blind_dir)
    with pytest.raises(judge.Refused, match="pass --key"):
        do_ingest(blind_dir, answers, root=env[0])
    written = do_ingest(blind_dir, answers, key_path=key, root=env[0])
    assert len(written) == 4
    assert all(p.is_relative_to(env[0] / "scores") for p in written)


def test_ingest_first_key_format_still_rehashes(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
    key = old_layout(env, blind_dir)
    run = json.loads(key.read_text())["runs"][min(answers)]
    clip = env[0] / "out" / run["arm"] / f"dog-a-s{run['seed']}" / "clip.mp4"
    clip.write_bytes(b"remade")
    with pytest.raises(judge.Refused, match="changed since the blind run"):
        do_ingest(
            blind_dir, write_answers(tmp_path, answers), key_path=key, root=env[0]
        )
    assert all_files(scores_of(env)) == []


def test_ingest_refuses_scores_outside_the_root(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = write_answers(tmp_path, good_answers(env, blind_dir))
    elsewhere = tmp_path / "elsewhere"
    with pytest.raises(judge.Refused, match="--force-scores-root"):
        do_ingest(blind_dir, answers, scores_root=elsewhere)
    assert not elsewhere.exists()
    written = do_ingest(
        blind_dir, answers, scores_root=elsewhere, force_scores_root=True
    )
    assert all(p.is_relative_to(elsewhere) for p in written)


def test_ingest_overwrite_refused_and_writes_nothing(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    key = load_key(env, blind_dir)
    answers = good_answers(env, blind_dir)
    run = key["runs"][min(answers)]
    existing = scores_of(env) / run["arm"] / f"dog-a-s{run['seed']}.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("old")
    with pytest.raises(judge.Refused, match="--overwrite"):
        do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert all_files(scores_of(env)) == [existing]
    assert existing.read_text() == "old"


def test_ingest_overwrite_flag_replaces(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    do_ingest(blind_dir, write_answers(tmp_path, good_answers(env, blind_dir)))
    answers = {i: good_entry(3, 3) for i in ids_for(env, blind_dir, "dog-a")}
    do_ingest(blind_dir, write_answers(tmp_path, answers), overwrite=True)
    files = all_files(scores_of(env))
    assert len(files) == 4
    for f in files:
        assert json.loads(f.read_text())["adherence"]["passed"] == 3
        assert stat.S_IMODE(f.stat().st_mode) == 0o644
    assert not [p for p in scores_of(env).rglob("*") if p.name.startswith(".")]


def test_ingest_two_clips_in_one_file(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = {
        **good_answers(env, blind_dir, "dog-a"),
        **good_answers(env, blind_dir, "dog-b"),
    }
    written = do_ingest(blind_dir, write_answers(tmp_path, answers))
    assert len(written) == 8


def test_ingest_refuses_scores_root_in_git(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    repo = env[0] / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(InsideRepo):
        do_ingest(
            blind_dir,
            write_answers(tmp_path, good_answers(env, blind_dir)),
            scores_root=repo / "s",
        )


def test_main_returns_2_on_refusal(
    tmp_path: pathlib.Path, env: Env, blind_dir: pathlib.Path
) -> None:
    answers = good_answers(env, blind_dir)
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
            "--root",
            str(env[0]),
        ]
    )
    assert rc == judge.REFUSED
    assert all_files(scores_of(env)) == []


def test_main_blind_then_ingest(tmp_path: pathlib.Path, env: Env) -> None:
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
            "--root",
            str(env[0]),
            "--prompts",
            str(env[1]),
            "--arms-file",
            str(env[2]),
        ]
    )
    assert rc == 0
    answers = good_answers(env, out, "dog-b")
    rc = judge.main(
        [
            "ingest",
            "--blind",
            str(out),
            "--answers",
            str(write_answers(tmp_path, answers)),
            "--judge",
            "x",
            "--root",
            str(env[0]),
        ]
    )
    assert rc == 0
    assert len(all_files(scores_of(env))) == 4
    assert os.access(key_of(env, out), os.R_OK)


# ----------------------------------------------------------------- images ----
# #1424: the image arms. The image itself is the evidence; an edit prompt's
# input images go once into the prompt's folder, as the reference.

IMAGE_DEFECTS = ["garbled text", "malformed body (legs)", "a watermark"]
INPUT_BYTES = {"base.jpg": b"\xff\xd8 input one", "face.jpg": b"\xff\xd8 input two"}
IMAGE_PROMPTS = {
    "version": "edit-v1",
    "media": "image",
    "defects": IMAGE_DEFECTS,
    "clips": [
        {
            "id": "swap-v1",
            "name": "swap",
            "inputs": [
                {"file": name, "sha256": sha(data)}
                for name, data in INPUT_BYTES.items()
            ],
            "prompt": "Swap it.",
            "checklist": ["Face matches image 2.", "Pose of image 1.", "Subtitle."],
        },
        {
            "id": "plain-v1",
            "name": "plain",
            "prompt": "A dog.",
            "checklist": ["One dog.", "Grass."],
        },
    ],
}
IMAGE_ARMS_CFG = {
    "media": "image",
    "prompts": "image-prompts.json",
    "seeds": [1, 2],
    "clips": ["swap", "plain"],
    "arms": {
        "qwen-image-edit": {"model": "Qwen/Qwen-Image-Edit-2511", "mode": "edit"},
        "flux2-klein-4b-edit": {
            "model": "black-forest-labs/FLUX.2-klein-4B",
            "mode": "edit",
        },
    },
}
IMAGE_ARMS = ("qwen-image-edit", "flux2-klein-4b-edit")


def png_bytes(
    size: tuple[int, int],
    color: tuple[int, ...],
    *,
    mode: str = "RGB",
    text: str | None = None,
) -> bytes:
    """A real PNG; `text` adds a tEXt chunk, as an encoder might."""
    info = PngImagePlugin.PngInfo()
    if text is not None:
        info.add_text("Software", text)
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def png_chunks(data: bytes) -> list[bytes]:
    """The chunk types of a PNG, in order."""
    pos, out = 8, []
    while pos < len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        out.append(data[pos + 4 : pos + 8])
        pos += 12 + length
    return out


def arm_png(arm: str, clip: str, seed: int) -> bytes:
    """Each arm's PNG differs the way two models' would: one is RGBA with
    an encoder chunk at the edit size, the other RGB at another size."""
    shade = 40 * seed + (0 if clip == "swap" else 7)
    if arm == "qwen-image-edit":
        return png_bytes(
            (1024, 576), (shade, 10, 20, 128), mode="RGBA", text="qwen encoder"
        )
    return png_bytes((640, 360), (shade, 90, 30))


def make_image_run(root: pathlib.Path, arm: str, clip: str, seed: int) -> bytes:
    image = arm_png(arm, clip, seed)
    out = root / "out" / arm / f"{clip}-s{seed}"
    out.mkdir(parents=True)
    (out / "image.png").write_bytes(image)
    run = {"status": "ok", "media": "image", "output": {"sha256": sha(image)}}
    (out / "run.json").write_text(json.dumps(run))
    return image


@pytest.fixture
def image_env(tmp_path: pathlib.Path) -> Env:
    root = tmp_path / "vg"
    for arm in IMAGE_ARMS:
        for clip in ("swap", "plain"):
            for seed in (1, 2):
                make_image_run(root, arm, clip, seed)
    inputs = root / "ref" / "imagegen"
    inputs.mkdir(parents=True)
    for name, data in INPUT_BYTES.items():
        (inputs / name).write_bytes(data)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "image-prompts.json").write_text(json.dumps(IMAGE_PROMPTS))
    (cfg / "image-arms.json").write_text(json.dumps(IMAGE_ARMS_CFG))
    return root, cfg / "image-prompts.json", cfg / "image-arms.json"


def image_blind(env: Env, out: pathlib.Path) -> dict[str, Any]:
    root, _, arms_file = env
    # No --prompts and no --clips: both come from the arms file.
    judge.blind(list(IMAGE_ARMS), out=out, root=root, arms_file=arms_file)
    key: dict[str, Any] = json.loads(key_of(env, out).read_text())
    return key


def test_image_blind_copies_only_the_image_and_the_inputs_once(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    out = tmp_path / "blind"
    key = image_blind(image_env, out)
    assert key["media"] == "image"
    assert len(key["runs"]) == 8
    for run_id, run in key["runs"].items():
        folder = out / run["clip"] / run_id
        assert [p.name for p in folder.iterdir()] == ["image.png"]
        source = pathlib.Path(run["clip_path"])
        assert source.name == "image.png"
        assert run["clip_sha256"] == sha(source.read_bytes())
        assert run["source_sha256"] == run["clip_sha256"]
        assert run["blind_sha256"] == sha((folder / "image.png").read_bytes())
    inputs = sorted(p.name for p in (out / "swap" / "input").iterdir())
    assert inputs == ["image-1.jpg", "image-2.jpg"]
    assert (out / "swap" / "input" / "image-2.jpg").read_bytes() == INPUT_BYTES[
        "face.jpg"
    ]
    assert not (out / "plain" / "input").exists()
    assert key["inputs"]["swap"] == [
        str(out.resolve() / "swap" / "input" / f"image-{n}.jpg") for n in (1, 2)
    ]
    assert key["inputs"]["plain"] == []
    # The same anti-leak rules as clips: fixed mtimes, the key outside, 0600.
    for path in out.rglob("*"):
        assert path.stat().st_mtime == judge.FIXED_MTIME
    assert not key_of(image_env, out).is_relative_to(out)
    assert stat.S_IMODE(key_of(image_env, out).stat().st_mode) == 0o600


def test_image_blind_prompt_names_the_inputs_and_hides_the_model(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    out = tmp_path / "blind"
    key = image_blind(image_env, out)
    text = (out / "swap" / "PROMPT.md").read_text()
    assert "You are a blind judge for an image evaluation." in text
    assert f"- image 1: {out.resolve() / 'swap' / 'input' / 'image-1.jpg'}" in text
    assert f"- image 2: {out.resolve() / 'swap' / 'input' / 'image-2.jpg'}" in text
    for run_id in key["order"]["swap"]:
        assert f"- candidate {run_id}: {out.resolve() / 'swap' / run_id}/" in text
    assert "1. Face matches image 2." in text
    for n, defect in enumerate(IMAGE_DEFECTS, 1):
        assert f"{n}. {defect}" in text
    plain = (out / "plain" / "PROMPT.md").read_text()
    assert "input image" not in plain and "- image 1" not in plain
    for path in out.rglob("*"):
        lowered = str(path.relative_to(out)).lower()
        for word in ("qwen", "flux", "edit", "-s1", "-s2"):
            assert word not in lowered
    for prompt in (text, plain):
        for word in ("qwen", "flux", "klein", "seed"):
            assert word not in prompt.lower()


def test_image_blind_refuses_an_image_unlike_its_run_json(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    image = image_env[0] / "out" / "qwen-image-edit" / "plain-s2" / "image.png"
    image.write_bytes(png_bytes((8, 8), (1, 2, 3)))
    with pytest.raises(judge.Refused, match="not the image its run.json records"):
        image_blind(image_env, tmp_path / "blind")
    assert not (tmp_path / "blind").exists()


def test_image_blind_refuses_an_input_that_is_not_the_pinned_one(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    (image_env[0] / "ref" / "imagegen" / "face.jpg").write_bytes(b"another face")
    with pytest.raises(judge.Refused, match="not the input the prompt pins"):
        image_blind(image_env, tmp_path / "blind")
    assert not (tmp_path / "blind").exists()
    assert not key_of(image_env, tmp_path / "blind").exists()


def test_image_blind_refuses_arms_from_two_files(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    with pytest.raises(judge.Refused, match="not all arms of one arms file"):
        judge.blind(
            ["h3-t2v", "qwen-image-edit"], out=tmp_path / "b", root=image_env[0]
        )


def test_image_ingest_writes_a_score_for_the_image(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    out = tmp_path / "blind"
    key = image_blind(image_env, out)
    answers = {
        i: {
            "items": [{"n": n, "true": n != 3, "why": "x"} for n in (1, 2, 3)],
            "defects": [{"type": "malformed body", "frame": "lower left", "note": ""}],
        }
        for i in key["order"]["swap"]
    }
    written = do_ingest(out, write_answers(tmp_path, answers))
    assert len(written) == 4
    for path in written:
        score = json.loads(path.read_text())
        assert score["adherence"] == {"passed": 2, "total": 3}
        assert score["defect_list"][0]["type"] == "malformed body (legs)"
        arm, name = path.parent.name, path.stem
        image = image_env[0] / "out" / arm / name / "image.png"
        assert score["clip_sha256"] == sha(image.read_bytes())


def test_image_ingest_refuses_an_unknown_id(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    out = tmp_path / "blind"
    key = image_blind(image_env, out)
    answers = {
        i: {
            "items": [{"n": n, "true": True, "why": "x"} for n in (1, 2)],
            "defects": [],
        }
        for i in key["order"]["plain"]
    }
    answers["zzzzzz"] = answers[key["order"]["plain"][0]]
    with pytest.raises(judge.Refused, match="zzzzzz: not an id"):
        do_ingest(out, write_answers(tmp_path, answers))
    assert not (image_env[0] / "scores").exists()


def test_image_ingest_refuses_an_image_remade_after_blind(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    out = tmp_path / "blind"
    key = image_blind(image_env, out)
    ids = key["order"]["plain"]
    pathlib.Path(key["runs"][ids[0]]["clip_path"]).write_bytes(b"remade")
    answers = {
        i: {
            "items": [{"n": n, "true": True, "why": "x"} for n in (1, 2)],
            "defects": [],
        }
        for i in ids
    }
    with pytest.raises(judge.Refused, match="changed since the blind run"):
        do_ingest(out, write_answers(tmp_path, answers))


def test_image_blind_copies_are_normalized_so_the_file_names_no_model(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    """#1424 review: the server's PNG (size, alpha, encoder chunk) told the
    arms apart. Every blind copy is RGB, the mode's size, and has no
    ancillary chunk."""
    out = tmp_path / "blind"
    key = image_blind(image_env, out)
    sources = {
        pathlib.Path(r["clip_path"]).read_bytes()[:40] for r in key["runs"].values()
    }
    assert len(sources) > 1  # the sources differ in their headers
    for run_id, run in key["runs"].items():
        data = (out / run["clip"] / run_id / "image.png").read_bytes()
        assert png_chunks(data)[0] == b"IHDR"
        assert set(png_chunks(data)) == {b"IHDR", b"IDAT", b"IEND"}
        with Image.open(io.BytesIO(data)) as im:
            assert im.mode == "RGB" and im.size == (1024, 576)
            assert not im.info
        assert run["blind_size"] == [1024, 576]


def test_a_normalized_copy_flattens_alpha_on_white() -> None:
    src = png_bytes((4, 4), (0, 0, 0, 0), mode="RGBA", text="x")
    with tempfile.TemporaryDirectory() as tmp:
        a, b = pathlib.Path(tmp, "a.png"), pathlib.Path(tmp, "b.png")
        a.write_bytes(src)
        digest = judge.normalize_png(a, b, (1024, 1024))
        assert digest == sha(b.read_bytes())
        with Image.open(b) as im:
            assert im.size == (1024, 1024)
            assert im.getpixel((0, 0)) == (255, 255, 255)


def test_a_normalized_copy_of_one_image_is_the_same_bytes_twice(
    tmp_path: pathlib.Path,
) -> None:
    src = tmp_path / "src.png"
    src.write_bytes(png_bytes((2048, 2048), (9, 8, 7)))
    first = judge.normalize_png(src, tmp_path / "a.png", (1024, 1024))
    second = judge.normalize_png(src, tmp_path / "b.png", (1024, 1024))
    assert first == second


def test_image_blind_refuses_an_image_pillow_cannot_read(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    out_dir = image_env[0] / "out" / "qwen-image-edit" / "plain-s1"
    junk = b"not a png"
    (out_dir / "image.png").write_bytes(junk)
    run = json.loads((out_dir / "run.json").read_text())
    run["output"]["sha256"] = sha(junk)
    (out_dir / "run.json").write_text(json.dumps(run))
    with pytest.raises(judge.Refused, match="not an image Pillow can read"):
        image_blind(image_env, tmp_path / "blind")
    assert not (tmp_path / "blind").exists()


def test_image_blind_refuses_an_arm_with_no_mode(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    cfg = json.loads(image_env[2].read_text())
    del cfg["arms"]["flux2-klein-4b-edit"]["mode"]
    image_env[2].write_text(json.dumps(cfg))
    with pytest.raises(judge.Refused, match="is not t2i or edit"):
        image_blind(image_env, tmp_path / "blind")


def test_image_ingest_refuses_a_blind_copy_changed_after_blind(
    tmp_path: pathlib.Path, image_env: Env
) -> None:
    out = tmp_path / "blind"
    key = image_blind(image_env, out)
    ids = key["order"]["plain"]
    (out / "plain" / ids[0] / "image.png").write_bytes(b"swapped")
    answers = {
        i: {
            "items": [{"n": n, "true": True, "why": "x"} for n in (1, 2)],
            "defects": [],
        }
        for i in ids
    }
    with pytest.raises(judge.Refused, match=r"image.png changed since the blind run"):
        do_ingest(out, write_answers(tmp_path, answers))
    assert not (image_env[0] / "scores").exists()


class Reverse:
    """A fake rng: shuffle reverses, so the copy order is known."""

    def shuffle(self, x: MutableSequence[Any], /) -> None:
        x.reverse()


def test_blind_shuffles_the_candidates_before_the_copy_loop(
    tmp_path: pathlib.Path, image_env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runs are found arm by arm; the copy order must not be that order."""
    copied: list[str] = []
    real = judge.normalize_png

    def spy(source: pathlib.Path, dest: pathlib.Path, size: tuple[int, int]) -> str:
        copied.append(source.parent.parent.name)
        return real(source, dest, size)

    monkeypatch.setattr(judge, "normalize_png", spy)
    root, _, arms_file = image_env
    judge.blind(
        list(IMAGE_ARMS),
        out=tmp_path / "blind",
        root=root,
        arms_file=arms_file,
        rng=Reverse(),
    )
    # Found order: every qwen run, then every flux run. Copied: reversed.
    assert copied == ["flux2-klein-4b-edit"] * 4 + ["qwen-image-edit"] * 4
