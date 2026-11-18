"""scripts/videogen/gallery.py: cards, grouping, order, scores, links, atomic index (#1413)."""

from __future__ import annotations

import html.parser
import json
import os
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import gallery
from videogen.common import InsideRepo

PROMPTS = {
    "version": "v1",
    "clips": [
        {"id": "dog-a-v1", "ref": "dog-a.mp4", "seconds": 8.0, "prompt": "Two dogs."},
        {"id": "dog-b-v1", "ref": "dog-b.mp4", "seconds": 6.0, "prompt": "A big dog."},
    ],
}


def run_json(arm: str, clip: str, seed: int, **change: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "schema": 1,
        "probe": False,
        "arm": arm,
        "clip": clip,
        "seed": seed,
        "model": "MiniMaxAI/MiniMax-H3" if arm.startswith("h3") else "Wan-AI/Wan",
        "revision": "0919fb68",
        "size": "primary",
        "request": {"fields": {"width": "1280", "height": "704", "prompt": "sent"}},
        "start": "2026-11-17T21:55:28-0500",
        "wall_s": 2438.739,
        "memory": {"peak_used_gib": 86.12},
        "energy_wh": 92.002,
        "output": {"sha256": "099907a89d5bee06eda80e5fa5bc5ed4"},
        "status": "ok",
        "error": None,
    }
    run.update(change)
    return run


def add_run(
    root: pathlib.Path, top: str, arm: str, name: str, run: dict[str, Any], clip: bool
) -> pathlib.Path:
    d = root / top / arm / name
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps(run))
    if clip:
        (d / "clip.mp4").write_bytes(f"video {top}/{arm}/{name}".encode())
    return d


@pytest.fixture
def layout(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A fake video-gen root, its gallery dir, and a prompts file."""
    root = tmp_path / "videogen"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "dog-a.mp4").write_bytes(b"reference a")
    # Added out of order on purpose: the page must sort them.
    add_run(root, "out", "wan-t2v", "dog-a-s2", run_json("wan-t2v", "dog-a", 2), True)
    add_run(root, "out", "h3-t2v", "dog-a-s2", run_json("h3-t2v", "dog-a", 2), True)
    add_run(root, "out", "h3-t2v", "dog-a-s1", run_json("h3-t2v", "dog-a", 1), True)
    add_run(
        root,
        "probe",
        "wan-t2v",
        "dog-a-s1-20261117T214519",
        run_json(
            "wan-t2v",
            "dog-a",
            1,
            probe=True,
            status="failed",
            error="request failed: closed",
            energy_wh=None,
            outlet={"energy_wh": 22.278},
        ),
        False,
    )
    add_run(root, "out", "h3-t2v", "dog-b-s1", run_json("h3-t2v", "dog-b", 1), True)
    scores = root / "scores" / "h3-t2v"
    scores.mkdir(parents=True)
    (scores / "dog-a-s1.json").write_text(
        json.dumps(
            {"schema": 1, "adherence": {"passed": 11, "total": 12}, "defects": 2}
        )
    )
    prompts = tmp_path / "v1.json"
    prompts.write_text(json.dumps(PROMPTS))
    return root, prompts


class Page(html.parser.HTMLParser):
    """Sections (by clip) and the cards in each, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.sections: dict[str, list[dict[str, str | None]]] = {}
        self.current: str | None = None
        self.external: list[str] = []
        self.videos: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "section":
            self.current = a.get("data-clip")
            assert self.current is not None
            self.sections[self.current] = []
        if tag == "article":
            assert self.current is not None
            self.sections[self.current].append(a)
        if tag == "video":
            self.videos.append(a)
        for key in ("src", "href"):
            value = a.get(key) or ""
            if "//" in value or value.startswith("/"):
                self.external.append(value)
        if tag in ("link", "img", "iframe"):
            self.external.append(tag)


def build(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> tuple[gallery.BuildResult, str, Page]:
    root, prompts = layout
    result = gallery.build(root, root / "gallery", prompts_file=prompts)
    text = result.index.read_text()
    page = Page()
    page.feed(text)
    return result, text, page


def test_groups_by_prompt_with_the_reference_first_then_arm_and_seed(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    _, text, page = build(layout)
    assert list(page.sections) == ["dog-a", "dog-b"]
    a = page.sections["dog-a"]
    assert a[0].get("data-ref") == "dog-a"
    assert [c.get("data-run") for c in a[1:]] == [
        "out/h3-t2v/dog-a-s1",
        "out/h3-t2v/dog-a-s2",
        "probe/wan-t2v/dog-a-s1-20261117T214519",
        "out/wan-t2v/dog-a-s2",
    ]
    assert [c.get("data-run") for c in page.sections["dog-b"][1:]] == [
        "out/h3-t2v/dog-b-s1"
    ]
    # The prompt text comes from the frozen prompts file.
    assert "Two dogs." in text and "A big dog." in text


def test_cards_carry_the_filter_keys_and_the_run_facts(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    _, text, page = build(layout)
    card = page.sections["dog-a"][1]
    assert card["data-arm"] == "h3-t2v"
    assert card["data-model"] == "MiniMaxAI/MiniMax-H3"
    assert card["data-kind"] == "scored"
    assert card["data-seed"] == "1"
    probe = page.sections["dog-a"][3]
    assert probe["data-kind"] == "probe"
    assert "40 min 39 s" in text
    assert "92.0 Wh" in text
    assert "22.3 Wh (incomplete)" in text
    assert "86.1 GiB" in text
    assert "1280x704 (primary)" in text
    assert "2026-11-17 21:55" in text
    assert "request failed: closed" in text
    for name in ("model", "arm", "kind", "seed"):
        assert f'data-filter="{name}"' in text


def test_videos_play_from_symlinks_not_copies(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    result, text, page = build(layout)
    root = layout[0]
    out = root / "gallery"
    link = out / "media" / "out" / "h3-t2v" / "dog-a-s1.mp4"
    assert link.is_symlink()
    assert not os.path.isabs(os.readlink(link))
    assert link.resolve() == (root / "out" / "h3-t2v" / "dog-a-s1" / "clip.mp4")
    ref = out / "media" / "ref" / "dog-a.mp4"
    assert ref.is_symlink() and ref.resolve() == root / "ref" / "dog-a.mp4"
    # Every video: controls, metadata preload, a relative src.
    assert page.videos
    for v in page.videos:
        assert "controls" in v and v.get("preload") == "metadata"
        assert (v.get("src") or "").startswith("media/")
    # The reference comes first; the failed probe has no video; dog-b has no ref.
    assert page.videos[0]["src"] == "media/ref/dog-a.mp4"
    assert text.count('class="novideo"') == 2
    assert result.cards == 5 and result.refs == 1


def test_no_external_request(layout: tuple[pathlib.Path, pathlib.Path]) -> None:
    _, text, page = build(layout)
    assert page.external == []
    assert "http://" not in text and "https://" not in text


def test_scores_show_when_present_and_not_scored_otherwise(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    _, text, _ = build(layout)
    assert "adherence 11/12 (92%), defects 2" in text
    assert text.count("not scored") == 4


def test_a_bad_score_file_is_named_not_fatal(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    root = layout[0]
    (root / "scores" / "h3-t2v" / "dog-a-s2.json").write_text(
        json.dumps({"adherence": {"passed": 13, "total": 12}, "defects": 0})
    )
    _, text, _ = build(layout)
    assert "bad score file" in text


@pytest.mark.parametrize(
    "data",
    [
        {"defects": 0},
        {"adherence": {"passed": 1, "total": 0}, "defects": 0},
        {"adherence": {"passed": 1, "total": 2}, "defects": -1},
        {"adherence": {"passed": True, "total": 2}, "defects": 0},
        {"adherence": {"passed": 1, "total": 2}},
    ],
)
def test_parse_score_refuses_a_bad_shape(data: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        gallery.parse_score(data)


def test_a_rebuild_is_idempotent_and_prunes_a_gone_run(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    first, text, _ = build(layout)
    assert first.changed
    second, again, _ = build(layout)
    assert not second.changed and second.links == 0 and again == text
    root = layout[0]
    gone = root / "out" / "wan-t2v" / "dog-a-s2"
    for f in gone.iterdir():
        f.unlink()
    gone.rmdir()
    third, after, _ = build(layout)
    assert third.changed and third.removed == 1
    assert not (root / "gallery" / "media" / "out" / "wan-t2v").exists()
    assert "out/wan-t2v/dog-a-s2" not in after


def test_the_index_is_written_atomically(
    layout: tuple[pathlib.Path, pathlib.Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    result, text, _ = build(layout)
    out = result.index.parent
    assert sorted(p.name for p in out.iterdir()) == ["index.html", "media"]
    # A new run appears, and the rename fails: the old page must survive whole,
    # and the temp file must not be left behind.
    root = layout[0]
    add_run(root, "out", "h3-t2v", "dog-a-s3", run_json("h3-t2v", "dog-a", 3), True)
    seen: list[tuple[str, str]] = []

    def broken_replace(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> None:
        seen.append((str(src), str(dst)))
        if str(dst).endswith("index.html"):
            raise OSError("disk full")
        os.rename(src, dst)

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        gallery.build(root, out, prompts_file=layout[1])
    assert result.index.read_text() == text
    tmp_src = [s for s, d in seen if d.endswith("index.html")]
    assert len(tmp_src) == 1
    assert pathlib.Path(tmp_src[0]).parent == out
    assert not pathlib.Path(tmp_src[0]).exists()
    assert not [p for p in out.iterdir() if p.name.startswith(".")]


def test_refuses_an_output_inside_a_git_work_tree(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    with pytest.raises(InsideRepo):
        gallery.build(
            layout[0], ROOT / "gallery-must-not-exist", prompts_file=layout[1]
        )
    assert not (ROOT / "gallery-must-not-exist").exists()
    assert gallery.main(["--root", str(layout[0]), "--out", str(ROOT / "x")]) == 2


def test_markup_in_run_json_is_escaped(
    layout: tuple[pathlib.Path, pathlib.Path],
) -> None:
    root = layout[0]
    add_run(
        root,
        "out",
        "h3-t2v",
        "dog-b-s2",
        run_json("h3-t2v", "dog-b", 2, error="<script>alert(1)</script>"),
        True,
    )
    _, text, _ = build(layout)
    assert "<script>alert(1)" not in text
    assert "&lt;script&gt;alert(1)" in text


@pytest.mark.parametrize(
    ("seconds", "want"),
    [(2438.739, "40 min 39 s"), (59.4, "59 s"), (60, "1 min 0 s"), (None, "-")],
)
def test_fmt_wall(seconds: Any, want: str) -> None:
    assert gallery.fmt_wall(seconds) == want


def test_the_real_prompts_file_loads() -> None:
    prompts = gallery.load_prompts(gallery.PROMPTS_FILE)
    assert {"dog-a", "dog-b", "dog-c"} <= set(prompts)
    assert all(p.text for p in prompts.values())
