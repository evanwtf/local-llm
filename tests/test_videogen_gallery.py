"""scripts/videogen/gallery.py: cards, metadata, grouping, order, scores, links,
the CSP and the atomic index (#1413)."""

from __future__ import annotations

import base64
import hashlib
import html.parser
import json
import os
import pathlib
import re
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

REFS = {
    "clips": [
        {
            "id": "dog-a",
            "ref": "dog-a.mp4",
            "source": "IMG_0001.MOV",
            "start": "12.0",
            "duration": "8.0",
            "crop": None,
            "sha256": "9143f98fd802f761484f5d6aada3a78a980fed70d79ebb8115ad5b4096cbc034",
            "fps": 30.0,
            "video_frames": 240,
        }
    ]
}

NEGATIVE = "low quality, blurry, " * 20


def run_v1(arm: str, clip: str, seed: int, **change: Any) -> dict[str, Any]:
    """A schema-1 probe run.json, as the first probes wrote it."""
    run: dict[str, Any] = {
        "schema": 1,
        "probe": False,
        "arm": arm,
        "mode": "t2v",
        "clip": clip,
        "seed": seed,
        "model": "MiniMaxAI/MiniMax-H3" if arm.startswith("h3") else "Wan-AI/Wan",
        "revision": "0919fb68",
        "size": "primary",
        "request": {
            "fields": {
                "prompt": "sent prompt",
                "width": "1280",
                "height": "704",
                "fps": "24",
                "num_inference_steps": "50",
                "flow_shift": "12",
                "seed": str(seed),
                "extra_params": '{"audio_flow_shift":3.0,"duration":5.0,"task":"t2va"}',
            },
            "input_reference": None,
        },
        "node": {"role": "head"},
        "server": {"vllm_version": "0.32.0", "served_model": "/models/H3"},
        "start": "2026-11-17T21:55:28-0500",
        "end": "2026-11-17T22:36:06-0500",
        "wall_s": 2438.739,
        "http_status": 200,
        "server_headers": {},
        "gpu_power": {"mean_w": 40.42, "peak_w": 43.07, "samples": 2432},
        "memory": {"peak_used_gib": 86.12},
        "output": {
            "file": "clip.mp4",
            "bytes": 13932374,
            "sha256": "099907a89d5bee06eda80e5fa5bc5ed4f209f609546684094e7528a4a7e2e9c0",
        },
        "outlet": {"energy_wh": 92.002, "complete": True},
        "energy_wh": 92.002,
        "status": "ok",
        "error": None,
    }
    run.update(change)
    return run


def run_v2(arm: str, clip: str, seed: int, **change: Any) -> dict[str, Any]:
    """A schema-2 run.json from generate.run_one, with every field filled."""
    run = run_v1(arm, clip, seed)
    run.update(
        {
            "schema": 2,
            "mode": "i2v",
            "model": "Wan-AI/Wan2.2-I2V-A14B-Diffusers",
            "revision": "596658fd",
            "request": {
                "fields": {
                    "prompt": "sent prompt",
                    "negative_prompt": NEGATIVE,
                    "width": "832",
                    "height": "480",
                    "num_frames": "81",
                    "fps": "16",
                    "num_inference_steps": "40",
                    "guidance_scale": "4.0",
                    "guidance_scale_2": "3.0",
                    "boundary_ratio": "0.9",
                    "flow_shift": "12.0",
                    "seed": str(seed),
                },
                "input_reference": {
                    "name": "first.png",
                    "bytes": 10,
                    "sha256": "ab" * 32,
                },
            },
            "server_config": {
                "name": "wan-i2v",
                "served_model_name": "Wan-AI/Wan2.2-I2V-A14B-Diffusers",
                "vllm_omni": "0.32.0",
                "argv": ["vllm", "serve", "/m", "--quantization", "fp8"],
                "env": {},
                "scope": {"MemoryMax": "115G"},
            },
            "server": {
                "served_model": "Wan-AI/Wan2.2-I2V-A14B-Diffusers",
                "vllm_version": "0.32.0",
                "gpu_w_before": 11.25,
            },
            "node": {"role": "worker"},
            "server_headers": {
                "x-inference-time-s": "2301.5",
                "x-stage-durations": '{"denoise": 2200.25, "vae": 90.5}',
                "x-peak-memory-mb": "61234",
            },
            "ffprobe": {
                "frames": 81,
                "width": 832,
                "height": 480,
                "fps": 16.0,
                "audio": False,
                "video_codec": "h264",
            },
            "outlet": {
                "energy_wh": 84.853,
                "complete": True,
                "max_gap_s": 5.1,
                "mean_w": 130.0,
                "peak_w": 150.0,
            },
            "energy_wh": 84.853,
        }
    )
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


Layout = tuple[pathlib.Path, pathlib.Path, pathlib.Path]


@pytest.fixture
def layout(tmp_path: pathlib.Path) -> Layout:
    """A fake video-gen root, a prompts file and a refs file."""
    root = tmp_path / "videogen"
    (root / "ref").mkdir(parents=True)
    (root / "ref" / "dog-a.mp4").write_bytes(b"reference a")
    (root / "frames" / "dog-a").mkdir(parents=True)
    (root / "frames" / "dog-a" / "first.png").write_bytes(b"\x89PNG fake")
    # Added out of order on purpose: the page must sort them.
    add_run(root, "out", "wan-t2v", "dog-a-s2", run_v1("wan-t2v", "dog-a", 2), True)
    add_run(root, "out", "h3-t2v", "dog-a-s2", run_v1("h3-t2v", "dog-a", 2), True)
    add_run(root, "out", "h3-t2v", "dog-a-s1", run_v1("h3-t2v", "dog-a", 1), True)
    add_run(root, "out", "wan-i2v", "dog-a-s1", run_v2("wan-i2v", "dog-a", 1), True)
    add_run(
        root,
        "probe",
        "wan-t2v",
        "dog-a-s1-20261117T214519",
        run_v1(
            "wan-t2v",
            "dog-a",
            1,
            probe=True,
            status="failed",
            error="request failed: closed",
            energy_wh=None,
            outlet={"energy_wh": 22.278, "complete": False, "max_gap_s": 41.0},
        ),
        False,
    )
    add_run(root, "out", "h3-t2v", "dog-b-s1", run_v1("h3-t2v", "dog-b", 1), True)
    scores = root / "scores" / "h3-t2v"
    scores.mkdir(parents=True)
    (scores / "dog-a-s1.json").write_text(
        json.dumps(
            {"schema": 1, "adherence": {"passed": 11, "total": 12}, "defects": 2}
        )
    )
    prompts = tmp_path / "v1.json"
    prompts.write_text(json.dumps(PROMPTS))
    refs = tmp_path / "refs-v1.json"
    refs.write_text(json.dumps(REFS))
    return root, prompts, refs


class Page(html.parser.HTMLParser):
    """Sections (by clip), the cards in each, and every tag the page loads."""

    def __init__(self) -> None:
        super().__init__()
        self.sections: dict[str, list[dict[str, str | None]]] = {}
        self.current: str | None = None
        self.external: list[str] = []
        self.videos: list[dict[str, str | None]] = []
        self.images: list[dict[str, str | None]] = []
        self.forms: list[dict[str, str | None]] = []
        self.tags: list[str] = []
        self.styles: list[str] = []
        self.scripts: list[str] = []
        self._in: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        a = dict(attrs)
        if tag in ("style", "script"):
            self._in = tag
        if tag == "section":
            self.current = a.get("data-clip")
            assert self.current is not None
            self.sections[self.current] = []
        if tag == "article":
            assert self.current is not None
            self.sections[self.current].append(a)
        if tag == "video":
            self.videos.append(a)
        if tag == "img":
            self.images.append(a)
        for key in ("src", "href"):
            value = a.get(key)
            if value is not None and not value.startswith("media/"):
                self.external.append(value)
        if tag in ("link", "iframe", "base"):
            self.external.append(tag)
        if tag == "form":
            # The one form is the logout button, posting to a relative URL.
            self.forms.append(a)

    def handle_endtag(self, tag: str) -> None:
        self._in = None

    def handle_data(self, data: str) -> None:
        if self._in == "style":
            self.styles.append(data)
        elif self._in == "script":
            self.scripts.append(data)


def build(layout: Layout) -> tuple[gallery.BuildResult, str, Page]:
    root, prompts, refs = layout
    result = gallery.build(root, root / "gallery", prompts_file=prompts, refs_file=refs)
    text = result.index.read_text()
    page = Page()
    page.feed(text)
    return result, text, page


def card_html(text: str, key: str) -> str:
    """The HTML of one run card, by its data-run key."""
    start = text.index(f'data-run="{key}"')
    return text[start : text.index("</article>", start)]


def test_groups_by_prompt_with_the_reference_first_then_arm_and_seed(
    layout: Layout,
) -> None:
    _, text, page = build(layout)
    assert list(page.sections) == ["dog-a", "dog-b"]
    a = page.sections["dog-a"]
    assert a[0].get("data-ref") == "dog-a"
    assert [c.get("data-run") for c in a[1:]] == [
        "out/h3-t2v/dog-a-s1",
        "out/h3-t2v/dog-a-s2",
        "out/wan-i2v/dog-a-s1",
        "probe/wan-t2v/dog-a-s1-20261117T214519",
        "out/wan-t2v/dog-a-s2",
    ]
    assert [c.get("data-run") for c in page.sections["dog-b"][1:]] == [
        "out/h3-t2v/dog-b-s1"
    ]
    # The prompt text comes from the frozen prompts file.
    assert "Two dogs." in text and "A big dog." in text


def test_cards_carry_the_filter_keys_and_the_summary(layout: Layout) -> None:
    _, text, page = build(layout)
    card = page.sections["dog-a"][1]
    assert card["data-arm"] == "h3-t2v"
    assert card["data-model"] == "MiniMaxAI/MiniMax-H3"
    assert card["data-kind"] == "scored"
    assert card["data-seed"] == "1"
    probe = page.sections["dog-a"][4]
    assert probe["data-kind"] == "probe"
    assert "40 min 39 s" in text
    assert "92.0 Wh" in text
    assert "22.3 Wh (incomplete)" in text
    assert "86.1 GiB" in text
    assert "1280×704 (primary)" in text
    assert "2026-11-17 21:55" in text
    assert "request failed: closed" in text
    for name in ("model", "arm", "kind", "seed"):
        assert f'data-filter="{name}"' in text


def test_a_schema_2_card_shows_every_metadata_group(layout: Layout) -> None:
    _, text, _ = build(layout)
    card = card_html(text, "out/wan-i2v/dog-a-s1")
    # Each group is a collapsed <details> with its summary line.
    for title in ("Request", "Server", "Measurements", "Clip check", "Raw run.json"):
        assert f"<summary><b>{title}</b>: " in card
    assert "<details open" not in card
    # Request.
    assert "I2V, 832×480 (primary), 81 frames, 16 fps, 40 steps" in card
    assert "<dt>guidance_scale</dt><dd>4.0</dd>" in card
    assert "<dt>guidance_scale_2</dt><dd>3.0</dd>" in card
    assert "<dt>boundary_ratio</dt><dd>0.9</dd>" in card
    assert "<dt>flow_shift</dt><dd>12.0</dd>" in card
    assert "(420 chars)</summary>" in card  # the long negative prompt is folded
    assert 'src="media/frames/dog-a/first.png"' in card
    # Server.
    assert "<dt>quantization</dt><dd>fp8</dd>" in card
    assert "<dt>vLLM-Omni</dt><dd>0.32.0</dd>" in card
    assert "<dt>node role</dt><dd>worker</dd>" in card
    assert "<code>vllm serve /m --quantization fp8</code>" in card
    # Measurements.
    assert "<dt>server inference</dt><dd>2301.5 s</dd>" in card
    assert "denoise: 2200.25 s<br>vae: 90.50 s" in card
    assert "<dt>energy complete</dt><dd>yes (max gap 5.1 s)</dd>" in card
    assert "<dt>GPU before</dt><dd>11.2 W</dd>" in card
    assert "40.4 W / 43.1 W" in card
    # Clip check.
    assert "<dt>frames</dt><dd>81</dd>" in card
    assert "<dt>audio</dt><dd>no</dd>" in card
    assert "13.3 MiB (13,932,374 bytes)" in card
    assert (
        'title="099907a89d5bee06eda80e5fa5bc5ed4f209f609546684094e7528a4a7e2e9c0"'
        in card
    )
    # Raw run.json, pretty-printed.
    assert "<pre>{\n  &quot;arm&quot;: &quot;wan-i2v&quot;" in card


def test_a_schema_1_card_renders_with_dashes(layout: Layout) -> None:
    _, text, _ = build(layout)
    card = card_html(text, "out/h3-t2v/dog-a-s1")
    assert "T2V, 1280×704 (primary), 5.0 s, 24 fps, 50 steps" in card
    assert "<dt>task</dt><dd>t2va</dd>" in card
    assert "<dt>audio_flow_shift</dt><dd>3.0</dd>" in card
    assert "<dt>negative prompt</dt><dd>—</dd>" in card
    assert "<dt>quantization</dt><dd>—</dd>" in card
    # No argv in schema 1: unknown, not "no quantization".
    assert "MiniMaxAI/MiniMax-H3, vLLM-Omni 0.32.0, —, head</summary>" in card
    assert "<dt>server inference</dt><dd>—</dd>" in card
    assert "<dt>stages</dt><dd>—</dd>" in card
    assert "<dt>GPU before</dt><dd>—</dd>" in card
    assert "<dt>frames</dt><dd>—</dd>" in card
    assert "first frame" not in card  # T2V has none
    probe = card_html(text, "probe/wan-t2v/dog-a-s1-20261117T214519")
    assert "<dt>energy complete</dt><dd>no (max gap 41.0 s)</dd>" in probe


def test_an_i2v_frame_that_is_missing_shows_its_path(layout: Layout) -> None:
    root = layout[0]
    (root / "frames" / "dog-a" / "first.png").unlink()
    _, text, page = build(layout)
    card = card_html(text, "out/wan-i2v/dog-a-s1")
    assert f"<code>{root / 'frames' / 'dog-a' / 'first.png'}</code> (not found)" in card
    assert page.images == []


def test_the_reference_card_shows_its_cut(layout: Layout) -> None:
    _, text, _ = build(layout)
    start = text.index('data-ref="dog-a"')
    ref = text[start : text.index("</article>", start)]
    assert "<dt>source</dt><dd>IMG_0001.MOV</dd>" in ref
    assert "<dt>window</dt><dd>12.0 s + 8.0 s</dd>" in ref
    assert "<dt>crop</dt><dd>none</dd>" in ref
    assert "9143f98fd802…" in ref
    # dog-b has no refs entry and no clip: dashes, no crash.
    start = text.index('data-ref="dog-b"')
    assert "<dt>source</dt><dd>—</dd>" in text[start : text.index("</article>", start)]


def test_videos_play_from_symlinks_not_copies(layout: Layout) -> None:
    result, text, page = build(layout)
    root = layout[0]
    out = root / "gallery"
    link = out / "media" / "out" / "h3-t2v" / "dog-a-s1.mp4"
    assert link.is_symlink()
    assert not os.path.isabs(os.readlink(link))
    assert link.resolve() == (root / "out" / "h3-t2v" / "dog-a-s1" / "clip.mp4")
    ref = out / "media" / "ref" / "dog-a.mp4"
    assert ref.is_symlink() and ref.resolve() == root / "ref" / "dog-a.mp4"
    frame = out / "media" / "frames" / "dog-a" / "first.png"
    assert frame.is_symlink()
    assert frame.resolve() == root / "frames" / "dog-a" / "first.png"
    # Every video: controls, metadata preload, a relative src.
    assert page.videos
    for v in page.videos:
        assert "controls" in v and v.get("preload") == "metadata"
        assert (v.get("src") or "").startswith("media/")
    # The reference comes first; the failed probe has no video; dog-b has no ref.
    assert page.videos[0]["src"] == "media/ref/dog-a.mp4"
    assert text.count('class="novideo"') == 2
    assert result.cards == 6 and result.refs == 1


def test_no_external_request(layout: Layout) -> None:
    _, text, page = build(layout)
    assert page.external == []
    assert "http://" not in text and "https://" not in text


def test_the_csp_names_the_inline_css_and_js_by_hash(layout: Layout) -> None:
    result, _, page = build(layout)
    policy = (result.index.parent / ".csp").read_text().strip()
    assert policy == gallery.csp()
    assert len(page.styles) == 1 and len(page.scripts) == 1

    def sha(text: str) -> str:
        digest = hashlib.sha256(text.encode()).digest()
        return "'sha256-" + base64.b64encode(digest).decode() + "'"

    directives = dict(
        (d.split(" ", 1) + [""])[:2] for d in (x.strip() for x in policy.split(";"))
    )
    assert directives["style-src"] == sha(page.styles[0])
    assert directives["script-src"] == sha(page.scripts[0])
    assert directives["default-src"] == "'none'"
    for name in ("base-uri", "frame-ancestors"):
        assert directives[name] == "'none'"
    # The logout form posts back to this server, and nowhere else.
    assert directives["form-action"] == "'self'"
    assert "unsafe-inline" not in policy


def test_scores_show_when_present_and_not_scored_otherwise(layout: Layout) -> None:
    _, text, _ = build(layout)
    assert "adherence 11/12 (92%), defects 2" in text
    assert text.count("not scored") == 5


def test_a_bad_score_file_is_named_not_fatal(layout: Layout) -> None:
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


def test_paths_come_from_directory_names_not_run_json(layout: Layout) -> None:
    root = layout[0]
    # run.json fields that try to steer the link, the score path and the group.
    add_run(
        root,
        "out",
        "h3-t2v",
        "dog-b-s2",
        run_v1("../../../etc", "../../x", 2),
        True,
    )
    evil = root / "scores" / "etc.json"
    evil.write_text(json.dumps({"adherence": {"passed": 1, "total": 1}, "defects": 0}))
    _, _, page = build(layout)
    card = [
        c for c in page.sections["dog-b"] if c.get("data-run") == "out/h3-t2v/dog-b-s2"
    ]
    assert len(card) == 1 and card[0]["data-arm"] == "h3-t2v"
    out = root / "gallery"
    assert (out / "media" / "out" / "h3-t2v" / "dog-b-s2.mp4").is_symlink()
    links = [p for p in out.rglob("*") if p.is_symlink()]
    assert all(p.resolve().is_relative_to(root) for p in links)
    assert all(".." not in p.relative_to(out).parts for p in links)
    assert "x" not in page.sections


@pytest.mark.parametrize(
    ("arm", "name"),
    [
        ("h3 t2v", "dog-a-s3"),  # a space
        ("h3.t2v", "dog-a-s3"),  # a dot
        ("h3-t2v", "dog a-s3"),  # a space in the clip
        ("h3-t2v", "dog-a-s3.bak"),  # a dot in the run
        ("h3-t2v", "dog-a"),  # no seed
        ("-h3", "dog-a-s3"),  # a leading dash
    ],
)
def test_an_unsafe_directory_name_is_skipped(
    layout: Layout, arm: str, name: str
) -> None:
    root = layout[0]
    before, text, _ = build(layout)
    media = root / "gallery" / "media"
    links = sorted(p.relative_to(media) for p in media.rglob("*") if p.is_symlink())
    add_run(root, "out", arm, name, run_v1("h3-t2v", "dog-a", 3), True)
    after, again, _ = build(layout)
    assert after.cards == before.cards == 6
    assert again == text
    assert links == sorted(
        p.relative_to(media) for p in media.rglob("*") if p.is_symlink()
    )


def test_a_rebuild_is_idempotent_and_prunes_a_gone_run(layout: Layout) -> None:
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
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, text, _ = build(layout)
    out = result.index.parent
    assert sorted(p.name for p in out.iterdir()) == [".csp", "index.html", "media"]
    # A new run appears, and the rename fails: the old page must survive whole,
    # and the temp file must not be left behind.
    root = layout[0]
    add_run(root, "out", "h3-t2v", "dog-a-s3", run_v1("h3-t2v", "dog-a", 3), True)
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
        gallery.build(root, out, prompts_file=layout[1], refs_file=layout[2])
    assert result.index.read_text() == text
    tmp_src = [s for s, d in seen if d.endswith("index.html")]
    assert len(tmp_src) == 1
    assert pathlib.Path(tmp_src[0]).parent == out
    assert not pathlib.Path(tmp_src[0]).exists()
    assert sorted(p.name for p in out.iterdir()) == [".csp", "index.html", "media"]


def test_refuses_an_output_inside_a_git_work_tree(layout: Layout) -> None:
    with pytest.raises(InsideRepo):
        gallery.build(
            layout[0], ROOT / "gallery-must-not-exist", prompts_file=layout[1]
        )
    assert not (ROOT / "gallery-must-not-exist").exists()
    assert gallery.main(["--root", str(layout[0]), "--out", str(ROOT / "x")]) == 2


#: Markup that would run, load or restyle if it reached the page unescaped.
EVIL = "<img src=x onerror=alert(1)>\"'&"


def test_markup_in_any_field_is_escaped(layout: Layout) -> None:
    root, prompts_file, _ = layout
    run = run_v2(
        "wan-i2v",
        "dog-b",
        2,
        model=EVIL,
        start=EVIL,
        end=EVIL,
        error=EVIL,
        size=EVIL,
        revision=EVIL,
        mode=EVIL,
    )
    run["request"]["fields"].update(
        {k: EVIL for k in ("prompt", "negative_prompt", "width", "fps", "flow_shift")}
    )
    run["request"]["input_reference"]["sha256"] = EVIL
    run["server_config"]["argv"] = ["vllm", EVIL, "--quantization", EVIL]
    run["server"] = {"vllm_version": EVIL, "gpu_w_before": EVIL}
    run["node"] = {"role": EVIL}
    run["server_headers"] = {
        "x-inference-time-s": EVIL,
        "x-stage-durations": json.dumps({EVIL: EVIL}),
    }
    run["ffprobe"] = {"frames": EVIL, "video_codec": EVIL, "width": EVIL, "height": 1}
    run["output"] = {"sha256": EVIL, "bytes": EVIL}
    run["outlet"] = {"complete": EVIL, "max_gap_s": EVIL}
    add_run(root, "out", "wan-i2v", "dog-b-s2", run, True)
    (root / "scores" / "wan-i2v").mkdir(parents=True)
    (root / "scores" / "wan-i2v" / "dog-b-s2.json").write_text(
        json.dumps(
            {
                "adherence": {"passed": 1, "total": 2},
                "defects": 0,
                "judge": EVIL,
                "notes": EVIL,
            }
        )
    )
    data = json.loads(prompts_file.read_text())
    data["clips"][1]["prompt"] = EVIL
    data["clips"][1]["id"] = EVIL
    prompts_file.write_text(json.dumps(data))
    _, text, page = build(layout)
    assert "<img src=x" not in text
    assert "onerror=alert(1)>" not in text.replace("onerror=alert(1)&gt;", "")
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    # Only the page's own tags: no injected img, and one script and style.
    assert page.images == [] or all(
        i.get("src") == "media/frames/dog-a/first.png" for i in page.images
    )
    assert page.tags.count("script") == 1 and page.tags.count("style") == 1
    assert page.external == []
    # The raw run.json block holds the payload escaped too.
    card = card_html(text, "out/wan-i2v/dog-b-s2")
    raw = card[card.index("<pre>") :]
    assert "&lt;img src=x onerror=alert(1)&gt;" in raw
    assert re.search(r"<pre>[^<]*</pre>", raw)


@pytest.mark.parametrize(
    ("seconds", "want"),
    [(2438.739, "40 min 39 s"), (59.4, "59 s"), (60, "1 min 0 s"), (None, "—")],
)
def test_fmt_wall(seconds: Any, want: str) -> None:
    assert gallery.fmt_wall(seconds) == want


def test_the_real_prompts_and_refs_files_load() -> None:
    prompts = gallery.load_prompts(gallery.PROMPTS_FILE)
    assert {"dog-a", "dog-b", "dog-c"} <= set(prompts)
    assert all(p.text for p in prompts.values())
    refs = gallery.load_refs(gallery.REFS_FILE)
    assert {"dog-a", "dog-b", "dog-c"} <= set(refs)
    assert all(len(r["sha256"]) == 64 for r in refs.values())


def test_a_score_for_another_clip_is_refused(layout: Layout) -> None:
    """A run remade after the judge saw it must not show the old score (#1406)."""
    root = layout[0]
    run_sha = run_v1("h3-t2v", "dog-a", 2)["output"]["sha256"]
    path = root / "scores" / "h3-t2v" / "dog-a-s2.json"
    score = {"schema": 1, "adherence": {"passed": 3, "total": 4}, "defects": 1}
    path.write_text(json.dumps({**score, "clip_sha256": "ab" * 32}))
    _, text, _ = build(layout)
    assert "score is for another clip" in text
    assert "adherence 3/4" not in text
    path.write_text(json.dumps({**score, "clip_sha256": run_sha}))
    _, text, _ = build(layout)
    assert "score is for another clip" not in text
    assert "adherence 3/4 (75%), defects 1" in text


def test_the_page_has_a_relative_logout_form(layout: Layout) -> None:
    _, _, page = build(layout)
    assert page.forms == [{"class": "logout", "method": "post", "action": "logout"}]
