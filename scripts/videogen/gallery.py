"""Build a static gallery of every video-gen clip, for a browser on the LAN (#1413).

    gallery.py [--root ~/bench-logs/videogen] [--out ROOT/gallery]

The site has one page, `index.html`. It groups the clips by prompt (clip id).
Each group shows the prompt text, the reference clip first, then every
model's output, ordered by arm, then seed. Each card shows a short summary
under the player, then collapsed groups with the full metadata from its
`run.json`: the request, the server, the measurements, the clip check, the
judge's scores and the raw `run.json`. A field that `run.json` does not hold
shows "—", so an older (schema 1) probe still renders. The reference card
shows its cut (source, window, crop, sha256) from `refs-v1.json`.

The script does not copy a video. It makes a relative symlink under
`media/` for each clip (and for each I2V first frame), so the site stays
small and a rebuild is cheap. `gallery_serve.py` serves the site, and it
refuses a link that resolves outside the video-gen root.

Inputs, all under `--root`:

- `out/<arm>/<clip>-s<seed>/{clip.mp4,run.json}`: scored runs.
- `probe/<arm>/<clip>-s<seed>-<stamp>/{clip.mp4,run.json}`: probes.
- `ref/<clip>.mp4`: the reference clips.
- `frames/<clip>/first.png`: the I2V input frame.
- `scores/<arm>/<run dir name>.json`: optional judge scores. For a scored run
  that is `scores/<arm>/<clip>-s<seed>.json`. The schema:

      {"schema": 1, "adherence": {"passed": 11, "total": 12}, "defects": 0,
       "judge": "who scored it", "notes": "free text"}

  `adherence` and `defects` are required; `judge` and `notes` are optional.
  A run with no file shows "not scored". A file whose optional `clip_sha256`
  differs from run.json's output sha256 shows "score is for another clip":
  the run was remade after the judge saw it.

The arm, clip and seed of a card come from its directory names, never from
`run.json`, and a name with any character outside `[A-Za-z0-9_-]` is
skipped: the names become link paths, and a field in a file must not choose
where a link goes.

A rebuild is idempotent. It writes `index.html` through a temp file and a
rename, so a browser never reads half a page, and it leaves the file alone
when the page has not changed. It removes a link whose run is gone. It also
writes `.csp`, the Content-Security-Policy that names the sha256 of the
page's inline CSS and JS; `gallery_serve.py` sends it with every response.

The output must be outside every git work tree: the clips are private and
this repository is public.

**Images (#1424).** A run whose run.json says `media: image` is an image
card: `out/<arm>/<prompt>-s<seed>/image.png`, linked as
`media/out/<arm>/<run dir>.png`. Image prompts come from
`prompts/image-v1.json` and `prompts/edit-v1.json`, and the page puts them
after the video prompts, under their own headings ("Images", "Image edits").
An image prompt gets a section only once it has a run. An edit card shows
its input images beside the output, linked from `ref/imagegen/<file>` as
`media/inputs/<file>`. Each image card shows the same summary as a clip:
the seconds, the Wh, the peak memory, the steps, the seed and the score.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import html
import json
import logging
import os
import pathlib
import re
import shlex
import sys
import tempfile
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import (
    BENCH_DIR,
    DEFAULT_ROOT,
    InsideRepo,
    prompt_name,
    refuse_inside_repo,
)

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_FILE = REPO / "benchmarks" / "videogen" / "prompts" / "v1.json"
REFS_FILE = REPO / "benchmarks" / "videogen" / "refs-v1.json"
#: The image prompts (#1424): text-to-image, then edits.
IMAGE_PROMPTS_FILES = (
    BENCH_DIR / "prompts" / "image-v1.json",
    BENCH_DIR / "prompts" / "edit-v1.json",
)

INDEX = "index.html"
#: The CSP for the page, beside it. A hidden name, so the server never
#: serves it as a file; it reads it and sends it as a header.
CSP_FILE = ".csp"
MEDIA = "media"
SCORED, PROBE = "scored", "probe"
REFUSED = 2
DASH = "—"
#: A negative prompt longer than this is folded into its own <details>.
LONG_TEXT = 120

#: One path component we are willing to put in a link: no dot, no slash.
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: An edit's input image name: one plain PNG or JPEG file name.
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.(png|jpg|jpeg)$")
VIDEO, IMAGE, EDIT = "video", "image", "edit"
#: The page's parts, in order, and their headings.
PARTS = ((VIDEO, "Video"), (IMAGE, "Images"), (EDIT, "Image edits"))
#: `<clip>-s<seed>` (scored) or `<clip>-s<seed>-<stamp>` (probe).
RUN_DIR = re.compile(r"^(?P<clip>.+?)-s(?P<seed>\d+)(?:-(?P<stamp>[0-9T]+))?$")


@dataclasses.dataclass(frozen=True)
class Score:
    passed: int
    total: int
    defects: int
    judge: str | None = None
    notes: str | None = None


@dataclasses.dataclass(frozen=True)
class Card:
    """One generated clip (or one failed attempt) and what its run.json says.

    `arm`, `clip` and `seed` come from the directory names (see `find_cards`).
    """

    kind: str  # SCORED or PROBE
    arm: str
    clip: str
    seed: int
    run_dir: pathlib.Path
    run: Mapping[str, Any]
    score: Score | None
    score_error: str | None
    #: An image arm's block.json (batch.py), when there is one (#1424).
    block: Mapping[str, Any] | None = None

    @property
    def key(self) -> str:
        """`<out|probe>/<arm dir>/<run dir>`, unique per card."""
        top = "out" if self.kind == SCORED else "probe"
        return f"{top}/{self.arm}/{self.run_dir.name}"

    @property
    def is_image(self) -> bool:
        return self.run.get("media") == "image"

    @property
    def video(self) -> pathlib.Path | None:
        clip = self.run_dir / "clip.mp4"
        return clip if clip.is_file() and not self.is_image else None

    @property
    def image(self) -> pathlib.Path | None:
        image = self.run_dir / "image.png"
        return image if image.is_file() and self.is_image else None

    @property
    def part(self) -> str:
        """VIDEO, IMAGE (text-to-image) or EDIT: where the card goes."""
        if not self.is_image:
            return VIDEO
        return EDIT if self.run.get("mode") == "edit" else IMAGE

    def sort_key(self) -> tuple[str, int, int, str]:
        # Scored before probe for the same arm and seed; then oldest first.
        return (
            self.arm,
            self.seed,
            0 if self.kind == SCORED else 1,
            str(self.run.get("start") or ""),
        )


@dataclasses.dataclass(frozen=True)
class Prompt:
    clip: str
    prompt_id: str | None
    text: str | None
    seconds: float | None
    part: str = VIDEO
    #: An edit's input image file names, in the order they are sent.
    inputs: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class BuildResult:
    index: pathlib.Path
    cards: int
    refs: int
    links: int
    removed: int
    changed: bool


# ---------------------------------------------------------------- reading ----


def read_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def as_dict(value: Any) -> dict[str, Any]:
    """`value` if it is a dict, else {}: run.json fields can be null or absent."""
    return value if isinstance(value, dict) else {}


def load_prompts(path: pathlib.Path) -> dict[str, Prompt]:
    """Clip id (the reference file's stem, `dog-a`) -> its frozen prompt."""
    data = read_json(path)
    if data is None:
        logger.warning(
            "cannot read the prompts file %s; prompts come from run.json", path
        )
        return {}
    found: dict[str, Prompt] = {}
    image_file = data.get("media") == "image"
    for item in data.get("clips") or []:
        if not isinstance(item, dict) or not (item.get("ref") or item.get("name")):
            continue
        clip = prompt_name(item)
        if not SAFE.match(clip):
            logger.warning("skipped prompt %r: not a safe clip id", clip)
            continue
        inputs = tuple(
            str(spec.get("file"))
            for spec in item.get("inputs") or []
            if isinstance(spec, dict)
        )
        bad = [name for name in inputs if not SAFE_FILE.match(name)]
        if bad:
            logger.warning("skipped prompt %s: unsafe input names %s", clip, bad)
            continue
        seconds = item.get("seconds")
        found[clip] = Prompt(
            clip=clip,
            prompt_id=item.get("id"),
            text=item.get("prompt"),
            seconds=float(seconds) if isinstance(seconds, (int, float)) else None,
            part=(EDIT if inputs else IMAGE) if image_file else VIDEO,
            inputs=inputs,
        )
    return found


def load_refs(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    """Clip id -> its entry in refs-v1.json (how the reference was cut)."""
    data = read_json(path)
    if data is None:
        logger.warning("cannot read the refs file %s", path)
        return {}
    return {
        str(item["id"]): item
        for item in data.get("clips") or []
        if isinstance(item, dict) and item.get("id")
    }


def parse_score(data: Mapping[str, Any]) -> Score:
    """A Score from a scores file, or ValueError/TypeError naming what is wrong."""
    adherence = data.get("adherence")
    if not isinstance(adherence, dict):
        raise TypeError("adherence must be an object with passed and total")
    passed, total, defects = (
        adherence.get("passed"),
        adherence.get("total"),
        data.get("defects"),
    )
    for name, value in (("passed", passed), ("total", total), ("defects", defects)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a whole number, 0 or more")
    assert isinstance(passed, int) and isinstance(total, int)
    assert isinstance(defects, int)
    if total == 0 or passed > total:
        raise ValueError("adherence needs 0 <= passed <= total and total > 0")
    judge, notes = data.get("judge"), data.get("notes")
    return Score(
        passed=passed,
        total=total,
        defects=defects,
        judge=str(judge) if judge is not None else None,
        notes=str(notes) if notes is not None else None,
    )


def load_score(
    path: pathlib.Path, clip_sha256: object = None
) -> tuple[Score | None, str | None]:
    """(score, None), (None, None) when there is no file, or (None, why).

    `clip_sha256` is the run's clip hash from run.json. A score that records
    another clip's hash was judged on an earlier clip of the same run, which
    has since been remade, so it is refused (#1406). A score with no hash
    (written by hand) is shown as is.
    """
    if not path.exists():
        return None, None
    data = read_json(path)
    if data is None:
        logger.warning("cannot read the score file %s", path)
        return None, "score file unreadable"
    try:
        score = parse_score(data)
    except (ValueError, TypeError) as err:
        logger.warning("bad score file %s: %s", path, err)
        return None, f"bad score file: {err}"
    scored = data.get("clip_sha256")
    if scored is not None and clip_sha256 is not None and scored != clip_sha256:
        logger.warning("score %s is for another clip than run.json", path)
        return None, "score is for another clip"
    return score, None


def find_cards(root: pathlib.Path) -> list[Card]:
    """Every run directory under out/ and probe/ that holds a run.json.

    The arm, clip and seed come from the directory names, which also build
    the link and score paths; a name that is not SAFE is skipped.
    """
    cards: list[Card] = []
    blocks: dict[pathlib.Path, Mapping[str, Any] | None] = {}
    for kind, top in ((SCORED, "out"), (PROBE, "probe")):
        for run_json in sorted((root / top).glob("*/*/run.json")):
            run_dir = run_json.parent
            arm = run_dir.parent.name
            match = RUN_DIR.match(run_dir.name)
            if not (SAFE.match(arm) and SAFE.match(run_dir.name) and match):
                logger.warning("skipped %s: not an <arm>/<clip>-s<seed> name", run_dir)
                continue
            clip = match["clip"]
            if not SAFE.match(clip):
                logger.warning("skipped %s: not a safe clip id", run_dir)
                continue
            run = read_json(run_json)
            if run is None:
                logger.warning("skipped %s: run.json is not a JSON object", run_dir)
                continue
            score, score_error = load_score(
                root / "scores" / arm / f"{run_dir.name}.json",
                as_dict(run.get("output")).get("sha256"),
            )
            block = None
            if run.get("media") == "image" and kind == SCORED:
                if run_dir.parent not in blocks:
                    blocks[run_dir.parent] = read_json(run_dir.parent / "block.json")
                block = blocks[run_dir.parent]
            cards.append(
                Card(
                    kind=kind,
                    arm=arm,
                    clip=clip,
                    seed=int(match["seed"]),
                    run_dir=run_dir,
                    run=run,
                    score=score,
                    score_error=score_error,
                    block=block,
                )
            )
    return cards


# ------------------------------------------------------------- formatting ----


def is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fmt_wall(seconds: Any) -> str:
    """`40 min 39 s` from 2438.7; a dash when unknown."""
    if not is_num(seconds):
        return DASH
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def fmt_num(value: Any, unit: str = "", digits: int = 1) -> str:
    """A number with a unit; a numeric string is accepted; else a dash."""
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value or DASH
    if not is_num(value):
        return DASH
    text = f"{value:.{digits}f}"
    return f"{text} {unit}" if unit else text


def fmt_energy(run: Mapping[str, Any]) -> str:
    """The outlet energy; a partial reading is marked so."""
    wh = run.get("energy_wh")
    if is_num(wh):
        return f"{wh:.1f} Wh"
    partial = as_dict(run.get("outlet")).get("energy_wh")
    if is_num(partial):
        return f"{partial:.1f} Wh (incomplete)"
    return DASH


def fmt_block_energy(block: Mapping[str, Any] | None) -> str:
    """An image arm's energy: its block's Wh per image (block.json). One
    image is too short for the outlet, so a run has no energy of its own."""
    if block is None:
        return f"{DASH} (no block yet)"
    per = block.get("wh_per_image")
    images = block.get("images")
    if is_num(per):
        return f"{per:.2f} Wh per image (block of {images})"
    return f"{DASH} (block of {images}: incomplete)"


def fmt_peak(run: Mapping[str, Any]) -> str:
    return fmt_num(as_dict(run.get("memory")).get("peak_used_gib"), "GiB")


def fields_of(run: Mapping[str, Any]) -> dict[str, Any]:
    return as_dict(as_dict(run.get("request")).get("fields"))


def fmt_size(run: Mapping[str, Any]) -> str:
    """`1280×704 (primary)`, from the request that was sent.

    An image request sends one `WxH` string (`size`) instead.
    """
    fields = fields_of(run)
    width, height = fields.get("width"), fields.get("height")
    size = fields.get("size")
    if not (width and height) and isinstance(size, str) and "x" in size:
        width, _, height = size.partition("x")
    dims = f"{width}×{height}" if width and height else DASH
    label = run.get("size")
    return f"{dims} ({label})" if label else dims


def fmt_date(run: Mapping[str, Any]) -> str:
    """`2026-11-17 21:55` from the ISO start; the full value goes in a title."""
    start = run.get("start")
    if not isinstance(start, str) or len(start) < 16:
        return DASH
    return start[:16].replace("T", " ")


def fmt_bytes(value: Any) -> str:
    if not is_num(value):
        return DASH
    return f"{value / (1 << 20):.1f} MiB ({value:,} bytes)"


def extra_params(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The request's extra_params, a JSON string in the form; {} if absent."""
    raw = fields.get("extra_params")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def mode_of(run: Mapping[str, Any]) -> str:
    mode = run.get("mode")
    if isinstance(mode, str) and mode:
        return mode.upper()
    return "I2V" if as_dict(run.get("request")).get("input_reference") else "T2V"


def flag_value(argv: Any, flag: str) -> str | None:
    """The value after `flag` in an argv list, or None."""
    if not isinstance(argv, list):
        return None
    for i, arg in enumerate(argv[:-1]):
        if arg == flag:
            return str(argv[i + 1])
    return None


def media_url(rel: pathlib.PurePosixPath, version: str | None = None) -> str:
    url = "/".join(urllib.parse.quote(part) for part in rel.parts)
    if version:
        # A rebuilt clip keeps its path; the sha in the URL defeats the cache.
        url += "?v=" + urllib.parse.quote(version[:12])
    return url


# ---------------------------------------------------------------- links ----


def link_path(card: Card) -> pathlib.PurePosixPath:
    top = "out" if card.kind == SCORED else "probe"
    suffix = ".png" if card.is_image else ".mp4"
    return pathlib.PurePosixPath(MEDIA, top, card.arm, card.run_dir.name + suffix)


def input_link_path(name: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(MEDIA, "inputs", name)


def ref_link_path(clip: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(MEDIA, "ref", f"{clip}.mp4")


def frame_link_path(clip: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(MEDIA, "frames", clip, "first.png")


def ensure_link(link: pathlib.Path, target: pathlib.Path) -> bool:
    """Point `link` at `target` with a relative symlink. True if it changed."""
    rel = os.path.relpath(target, link.parent)
    if link.is_symlink() and os.readlink(link) == rel:
        return False
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(rel, tmp)
    os.replace(tmp, link)
    return True


def prune_links(media: pathlib.Path, keep: set[pathlib.Path]) -> int:
    """Remove symlinks under `media` that are not in `keep`, then empty dirs.

    Only symlinks go: a regular file here is not ours, so it stays.
    """
    removed = 0
    if not media.is_dir():
        return 0
    for dirpath, _dirnames, filenames in os.walk(media, topdown=False):
        here = pathlib.Path(dirpath)
        for name in filenames:
            path = here / name
            if path.is_symlink() and path not in keep:
                path.unlink()
                removed += 1
        if here != media and not any(here.iterdir()):
            here.rmdir()
    return removed


# ----------------------------------------------------------------- render ----

CSS = """
:root { color-scheme: light dark; --bg: #f6f6f4; --fg: #1c1c1c; --muted: #5e5e5e;
  --card: #ffffff; --line: #d9d9d4; --ok: #1d7a3a; --bad: #b3261e; --probe: #8a5a00;
  --accent: #2456a6; --code: #efefec; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #141414; --fg: #e8e8e6; --muted: #a3a3a0; --card: #1f1f1f;
    --line: #333331; --ok: #5cc27a; --bad: #f2867e; --probe: #e0b45c; --accent: #8ab4f8;
    --code: #2a2a2a; }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, main { padding: 12px 16px; max-width: 1800px; margin: 0 auto; }
h1 { font-size: 1.3rem; margin: 0 0 8px; }
.top { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
  justify-content: space-between; }
form.logout { margin: 0; }
h2 { font-size: 1.1rem; margin: 0 0 4px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
.filters label { display: flex; gap: 6px; align-items: center; color: var(--muted); }
select, button { font: inherit; color: var(--fg); background: var(--card);
  border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; }
button { cursor: pointer; }
section.prompt { border-top: 1px solid var(--line); padding: 16px 0; }
.prompt-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
  justify-content: space-between; }
.prompt-text { color: var(--muted); margin: 4px 0 12px; max-width: 110ch; }
.grid { display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 360px), 1fr)); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.card.ref { border-color: var(--accent); }
video { width: 100%; aspect-ratio: 16 / 9; background: #000; border-radius: 4px; }
img.output { width: 100%; height: auto; background: #000; border-radius: 4px; }
.pair { display: grid; grid-template-columns: 3fr 1fr; gap: 6px; align-items: start; }
.inputs { display: flex; flex-direction: column; gap: 4px; font-size: 0.8rem; }
.inputs img { width: 100%; height: auto; border-radius: 4px; }
h2.part { font-size: 1.25rem; margin: 20px 0 0; }
img.frame { width: 100%; height: auto; border-radius: 4px; }
.novideo { aspect-ratio: 16 / 9; display: grid; place-items: center;
  border: 1px dashed var(--line); border-radius: 4px; color: var(--muted); }
.title { font-weight: 600; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 10px; margin: 0;
  font-size: 0.9rem; }
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; min-width: 0; }
details { border-top: 1px solid var(--line); padding-top: 4px; font-size: 0.9rem; }
details details { border: 0; padding: 0; }
summary { cursor: pointer; overflow-wrap: anywhere; }
summary b { font-weight: 600; }
details > dl, details > pre { margin-top: 4px; }
pre, code { font: 0.8rem/1.35 ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--code); border-radius: 4px; }
pre { padding: 6px; overflow-x: auto; max-height: 60vh; white-space: pre-wrap;
  overflow-wrap: anywhere; }
code { padding: 0 3px; overflow-wrap: anywhere; }
.tag { display: inline-block; font-size: 0.8rem; padding: 0 6px; border-radius: 4px;
  border: 1px solid currentColor; margin-left: 4px; }
.ok { color: var(--ok); } .bad { color: var(--bad); } .probe { color: var(--probe); }
.muted { color: var(--muted); }
"""

JS = """
(function () {
  var selects = document.querySelectorAll('select[data-filter]');
  var count = document.getElementById('count');
  function apply() {
    var want = {};
    selects.forEach(function (s) { if (s.value) { want[s.dataset.filter] = s.value; } });
    var active = Object.keys(want).length > 0;
    var shown = 0, total = 0;
    document.querySelectorAll('article.run').forEach(function (card) {
      total++;
      var ok = Object.keys(want).every(function (k) { return card.dataset[k] === want[k]; });
      card.hidden = !ok;
      if (ok) { shown++; }
    });
    document.querySelectorAll('section.prompt').forEach(function (sec) {
      sec.hidden = active && !sec.querySelector('article.run:not([hidden])');
    });
    // A part heading (videos, images, edits) goes with its last section.
    document.querySelectorAll('h2.part').forEach(function (h) {
      var sel = 'section.prompt[data-part="' + h.dataset.part + '"]:not([hidden])';
      h.hidden = active && !document.querySelector(sel);
    });
    count.textContent = shown + ' of ' + total + ' items shown';
  }
  selects.forEach(function (s) { s.addEventListener('change', apply); });
  document.querySelectorAll('button.playall').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var sec = btn.closest('section');
      sec.querySelectorAll('article:not([hidden]) video').forEach(function (v) {
        v.currentTime = 0;
        var p = v.play();
        if (p && p.catch) { p.catch(function () {}); }
      });
    });
  });
  apply();
})();
"""


def csp_hash(text: str) -> str:
    """`'sha256-...'` for one inline <style> or <script> body."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def csp() -> str:
    """The page's Content-Security-Policy.

    Only the page's own inline CSS and JS run, named by hash; media and the
    first frames load from this server; nothing loads from anywhere else,
    and no injected <base> or frame can work. A form may post only to this
    server: the page's logout button is a form (gallery_serve.py, #1413).
    """
    return (
        "default-src 'none'; "
        f"style-src {csp_hash(CSS)}; "
        f"script-src {csp_hash(JS)}; "
        "media-src 'self'; img-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def val(value: Any) -> str:
    """An escaped value, or a dash for a missing one."""
    if value is None or value == "":
        return DASH
    if isinstance(value, bool):
        return "yes" if value else "no"
    return esc(value)


def dl(rows: Iterable[tuple[str, str]]) -> str:
    """Rows of (label, already-escaped html)."""
    return "<dl>" + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"


def group(title: str, summary: str, body: str) -> str:
    """A collapsed group whose summary line stays visible."""
    return f"<details><summary><b>{esc(title)}</b>: {summary}</summary>{body}</details>"


def fold(text: Any) -> str:
    """Escaped text; long text is folded into its own <details>."""
    if text is None or text == "":
        return DASH
    text = str(text)
    if len(text) <= LONG_TEXT:
        return esc(text)
    return (
        f"<details><summary>{esc(text[:60])}… ({len(text)} chars)</summary>"
        f"{esc(text)}</details>"
    )


def short_sha(sha: Any) -> str:
    """The first 12 hex of a sha256; the full value folds out on a tap."""
    if not isinstance(sha, str) or not sha:
        return DASH
    return (
        f'<details><summary><code title="{esc(sha)}">{esc(sha[:12])}…</code>'
        f"</summary><code>{esc(sha)}</code></details>"
    )


def video_tag(src: str | None) -> str:
    if src is None:
        return '<div class="novideo">no video</div>'
    return f'<video controls preload="metadata" playsinline src="{esc(src)}"></video>'


def score_html(card: Card) -> str:
    if card.score_error:
        return f'<span class="bad">{esc(card.score_error)}</span>'
    if card.score is None:
        return '<span class="muted">not scored</span>'
    s = card.score
    pct = round(100 * s.passed / s.total)
    text = f"adherence {s.passed}/{s.total} ({pct}%), defects {s.defects}"
    if s.judge:
        text += f", judge {s.judge}"
    out = esc(text)
    if s.notes:
        out += f'<br><span class="muted">{esc(s.notes)}</span>'
    return out


def request_group(card: Card, frame_src: str | None, frames_root: pathlib.Path) -> str:
    run = card.run
    fields = fields_of(run)
    extra = extra_params(fields)
    mode = mode_of(run)
    frames = fields.get("num_frames")
    duration = extra.get("duration")
    length = (
        f"{frames} frames"
        if frames
        else (f"{duration} s" if duration not in (None, "") else None)
    )
    rows = [
        ("mode", esc(mode)),
        ("prompt", val(fields.get("prompt"))),
        ("negative prompt", fold(fields.get("negative_prompt"))),
        ("size", esc(fmt_size(run))),
        ("frames or length", val(length)),
        ("fps", val(fields.get("fps"))),
        ("steps", val(fields.get("num_inference_steps"))),
        ("guidance_scale", val(fields.get("guidance_scale"))),
        ("guidance_scale_2", val(fields.get("guidance_scale_2"))),
        ("flow_shift", val(fields.get("flow_shift"))),
        ("boundary_ratio", val(fields.get("boundary_ratio"))),
        ("task", val(extra.get("task"))),
        ("audio_flow_shift", val(extra.get("audio_flow_shift"))),
        ("seed", val(fields.get("seed", run.get("seed")))),
    ]
    image = as_dict(as_dict(run.get("request")).get("input_reference"))
    if mode == "I2V" or image:
        name = image.get("name") or "first.png"
        if frame_src is not None:
            shown = (
                f'<img class="frame" loading="lazy" alt="the first frame sent" '
                f'src="{esc(frame_src)}">'
            )
        else:
            shown = (
                f"<code>{esc(frames_root / card.clip / str(name))}</code> (not found)"
            )
        rows.append(("first frame", shown))
        rows.append(("frame sha256", short_sha(image.get("sha256"))))
    steps = fields.get("num_inference_steps")
    summary = esc(
        f"{mode}, {fmt_size(run)}, {length or DASH}, "
        f"{fields.get('fps') or DASH} fps, {steps or DASH} steps"
    )
    return group("Request", summary, dl(rows))


def server_group(run: Mapping[str, Any]) -> str:
    seen = as_dict(run.get("server"))
    config = as_dict(run.get("server_config"))
    argv = config.get("argv")
    served = seen.get("served_model") or config.get("served_model_name")
    version = seen.get("vllm_version") or config.get("vllm_omni")
    quant = flag_value(argv, "--quantization")
    # No argv recorded (schema 1) is unknown, not "no quantization".
    if quant is None and isinstance(argv, list) and argv:
        quant_text = "no quantization flag"
    else:
        quant_text = quant or DASH
    role = as_dict(run.get("node")).get("role")
    argv_text = (
        shlex.join(str(a) for a in argv) if isinstance(argv, list) and argv else None
    )
    scope = as_dict(config.get("scope"))
    rows = [
        ("model", val(run.get("model"))),
        ("revision", val(run.get("revision"))),
        ("served model", val(served)),
        ("vLLM-Omni", val(version)),
        ("quantization", esc(quant_text)),
        ("node role", val(role)),
        ("server block", val(config.get("name"))),
        ("memory cap", val(scope.get("MemoryMax"))),
        ("argv", f"<code>{esc(argv_text)}</code>" if argv_text else DASH),
    ]
    summary = esc(
        f"{run.get('model') or DASH}, vLLM-Omni {version or DASH}, "
        f"{quant_text}, {role or DASH}"
    )
    return group("Server", summary, dl(rows))


def stage_rows(raw: Any) -> str:
    """The x-stage-durations header: a JSON object of stage -> seconds, or text."""
    if raw is None or raw == "":
        return DASH
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return esc(raw)
    if isinstance(parsed, dict):
        return "<br>".join(
            f"{esc(k)}: {esc(fmt_num(v, 's', 2))}" for k, v in parsed.items()
        )
    return esc(raw)


def measure_group(run: Mapping[str, Any]) -> str:
    headers = as_dict(run.get("server_headers"))
    # An image reply carries these in its JSON body, not in headers (#1424).
    server_metrics = as_dict(run.get("server_metrics"))
    outlet = as_dict(run.get("outlet"))
    gpu = as_dict(run.get("gpu_power"))
    seen = as_dict(run.get("server"))
    complete = outlet.get("complete")
    wall = run.get("wall_s")
    wall_text = fmt_wall(wall) + (f" ({wall} s)" if is_num(wall) else "")
    gap = outlet.get("max_gap_s")
    rows = [
        ("wall", esc(wall_text)),
        (
            "server inference",
            # An image run's server_s is the sum of its stage durations.
            esc(fmt_num(headers.get("x-inference-time-s") or run.get("server_s"), "s")),
        ),
        (
            "stages",
            stage_rows(
                headers.get("x-stage-durations")
                or server_metrics.get("stage_durations")
            ),
        ),
        ("energy", esc(fmt_energy(run))),
        (
            "energy complete",
            val(complete) + (f" (max gap {esc(gap)} s)" if gap is not None else ""),
        ),
        (
            "outlet mean / peak",
            esc(
                f"{fmt_num(outlet.get('mean_w'), 'W')} / "
                f"{fmt_num(outlet.get('peak_w'), 'W')}"
            ),
        ),
        (
            "GPU mean / peak",
            esc(
                f"{fmt_num(gpu.get('mean_w'), 'W')} / {fmt_num(gpu.get('peak_w'), 'W')}"
            ),
        ),
        ("GPU before", esc(fmt_num(seen.get("gpu_w_before"), "W"))),
        ("peak memory", esc(fmt_peak(run))),
        (
            "server peak memory",
            esc(
                fmt_num(
                    headers.get("x-peak-memory-mb")
                    or server_metrics.get("peak_memory_mb"),
                    "MB",
                    0,
                )
            ),
        ),
        ("HTTP status", val(run.get("http_status"))),
        ("start", val(run.get("start"))),
        ("end", val(run.get("end"))),
    ]
    summary = esc(f"{fmt_wall(wall)}, {fmt_energy(run)}, {fmt_peak(run)}")
    return group("Measurements", summary, dl(rows))


def clip_group(run: Mapping[str, Any]) -> str:
    probe = as_dict(run.get("ffprobe"))
    output = as_dict(run.get("output"))
    width, height = probe.get("width"), probe.get("height")
    size = f"{width}×{height}" if width and height else None
    rows = [
        ("frames", val(probe.get("frames"))),
        ("size", val(size)),
        ("fps", val(probe.get("fps"))),
        ("audio", val(probe.get("audio"))),
        ("codec", val(probe.get("video_codec"))),
        ("sha256", short_sha(output.get("sha256"))),
        ("file size", esc(fmt_bytes(output.get("bytes")))),
    ]
    frames = probe.get("frames")
    summary = esc(
        f"{frames if frames is not None else DASH} frames, {size or DASH}, "
        f"{fmt_bytes(output.get('bytes')).split(' (')[0]}"
    )
    return group("Clip check", summary, dl(rows))


def input_names(card: Card) -> list[str]:
    """The input image names an edit's run.json records, in order; unsafe
    names are left out, because they become link paths."""
    request = as_dict(card.run.get("request"))
    inputs = request.get("inputs")
    if not isinstance(inputs, list):
        return []
    names = [str(as_dict(i).get("name") or "") for i in inputs]
    return [n for n in names if SAFE_FILE.match(n)]


def inputs_html(names: list[str], srcs: Mapping[str, str | None]) -> str:
    """The input images of an edit, one under the other, labeled image 1, 2."""
    items = []
    for n, name in enumerate(names, 1):
        src = srcs.get(name)
        shown = (
            f'<img loading="lazy" alt="input image {n}" src="{esc(src)}">'
            if src
            else f"<code>{esc(name)}</code> (not found)"
        )
        items.append(f"<div>image {n}: {esc(name)}{shown}</div>")
    return f'<div class="inputs">{"".join(items)}</div>'


def output_tag(src: str | None) -> str:
    if src is None:
        return '<div class="novideo">no image</div>'
    return (
        f'<img class="output" loading="lazy" alt="the output image" src="{esc(src)}">'
    )


def image_request_group(card: Card) -> str:
    run = card.run
    fields = fields_of(run)
    request = as_dict(run.get("request"))
    inputs = [as_dict(i) for i in request.get("inputs") or [] if isinstance(i, dict)]
    rows = [
        ("mode", esc(mode_of(run))),
        ("prompt", val(fields.get("prompt"))),
        ("negative prompt", fold(fields.get("negative_prompt"))),
        ("size", esc(fmt_size(run))),
        ("steps", val(fields.get("num_inference_steps"))),
        ("guidance_scale", val(fields.get("guidance_scale"))),
        ("true_cfg_scale", val(fields.get("true_cfg_scale"))),
        ("seed", val(fields.get("seed", run.get("seed")))),
    ]
    for n, item in enumerate(inputs, 1):
        rows.append((f"input {n}", val(item.get("path") or item.get("name"))))
        rows.append((f"input {n} sha256", short_sha(item.get("sha256"))))
    steps = fields.get("num_inference_steps")
    summary = esc(
        f"{mode_of(run)}, {fmt_size(run)}, {steps or DASH} steps, "
        f"{len(inputs)} input image(s)"
    )
    return group("Request", summary, dl(rows))


def image_check_group(run: Mapping[str, Any]) -> str:
    found = as_dict(run.get("found"))
    output = as_dict(run.get("output"))
    width, height = found.get("width"), found.get("height")
    size = f"{width}×{height}" if width and height else None
    rows = [
        ("size", val(size)),
        ("format", val(found.get("format"))),
        ("sha256", short_sha(output.get("sha256"))),
        ("file size", esc(fmt_bytes(output.get("bytes")))),
    ]
    summary = esc(f"{size or DASH}, {fmt_bytes(output.get('bytes')).split(' (')[0]}")
    return group("Image check", summary, dl(rows))


def image_card(
    card: Card, src: str | None, input_srcs: Mapping[str, str | None]
) -> str:
    run = card.run
    status = str(run.get("status") or "unknown")
    status_cls = "ok" if status == "ok" else "bad"
    model = str(run.get("model") or DASH)
    kind_tag = (
        '<span class="tag probe">probe</span>'
        if card.kind == PROBE
        else '<span class="tag ok">scored</span>'
    )
    fields = fields_of(run)
    rows = [
        ("model", esc(model)),
        ("size", esc(fmt_size(run))),
        ("status", f'<span class="{status_cls}">{esc(status)}</span>'),
        ("wall", esc(fmt_wall(run.get("wall_s")))),
        ("block energy", esc(fmt_block_energy(card.block))),
        ("peak", esc(fmt_peak(run))),
        ("steps", val(fields.get("num_inference_steps"))),
        ("seed", val(fields.get("seed", run.get("seed")))),
        (
            "date",
            f'<span title="{esc(run.get("start") or "")}">{esc(fmt_date(run))}</span>',
        ),
        ("scores", score_html(card)),
    ]
    if run.get("error"):
        rows.insert(3, ("error", f'<span class="bad">{esc(run["error"])}</span>'))
    names = input_names(card)
    shown = output_tag(src)
    if names:
        shown = f'<div class="pair">{shown}{inputs_html(names, input_srcs)}</div>'
    groups = "".join(
        [
            image_request_group(card),
            server_group(run),
            measure_group(run),
            image_check_group(run),
            raw_group(run),
        ]
    )
    return (
        f'<article class="card run" data-run="{esc(card.key)}" '
        f'data-arm="{esc(card.arm)}" data-model="{esc(model)}" '
        f'data-kind="{esc(card.kind)}" data-seed="{esc(card.seed)}">'
        f"{shown}"
        f'<div class="title">{esc(card.arm)} · seed {esc(card.seed)}{kind_tag}</div>'
        f"{dl(rows)}{groups}</article>"
    )


def raw_group(run: Mapping[str, Any]) -> str:
    text = json.dumps(run, indent=2, ensure_ascii=False, sort_keys=True)
    return group("Raw run.json", esc(f"{len(text):,} chars"), f"<pre>{esc(text)}</pre>")


def run_card(
    card: Card, src: str | None, frame_src: str | None, frames_root: pathlib.Path
) -> str:
    run = card.run
    status = str(run.get("status") or "unknown")
    status_cls = "ok" if status == "ok" else "bad"
    model = str(run.get("model") or DASH)
    kind_tag = (
        '<span class="tag probe">probe</span>'
        if card.kind == PROBE
        else '<span class="tag ok">scored</span>'
    )
    rows = [
        ("model", esc(model)),
        ("size", esc(fmt_size(run))),
        ("status", f'<span class="{status_cls}">{esc(status)}</span>'),
        ("wall", esc(fmt_wall(run.get("wall_s")))),
        ("energy", esc(fmt_energy(run))),
        ("peak", esc(fmt_peak(run))),
        (
            "date",
            f'<span title="{esc(run.get("start") or "")}">{esc(fmt_date(run))}</span>',
        ),
        ("scores", score_html(card)),
    ]
    if run.get("error"):
        rows.insert(3, ("error", f'<span class="bad">{esc(run["error"])}</span>'))
    groups = "".join(
        [
            request_group(card, frame_src, frames_root),
            server_group(run),
            measure_group(run),
            clip_group(run),
            raw_group(run),
        ]
    )
    return (
        f'<article class="card run" data-run="{esc(card.key)}" '
        f'data-arm="{esc(card.arm)}" data-model="{esc(model)}" '
        f'data-kind="{esc(card.kind)}" data-seed="{esc(card.seed)}">'
        f"{video_tag(src)}"
        f'<div class="title">{esc(card.arm)} · seed {esc(card.seed)}{kind_tag}</div>'
        f"{dl(rows)}{groups}</article>"
    )


def ref_card(
    clip: str, src: str | None, prompt: Prompt | None, ref: Mapping[str, Any]
) -> str:
    length = f"{prompt.seconds:g} s" if prompt is not None and prompt.seconds else None
    start, duration = ref.get("start"), ref.get("duration")
    window = f"{start} s + {duration} s" if start is not None and duration else None
    rows = [
        ("source", val(ref.get("source"))),
        ("window", val(window)),
        ("crop", val(ref.get("crop") or ("none" if "crop" in ref else None))),
        ("length", val(length)),
        ("frames", val(ref.get("video_frames"))),
        ("fps", val(ref.get("fps"))),
        ("sha256", short_sha(ref.get("sha256"))),
    ]
    return (
        f'<article class="card ref" data-ref="{esc(clip)}">'
        f"{video_tag(src)}"
        f'<div class="title">reference: {esc(clip)}</div>'
        f"{dl(rows)}</article>"
    )


def select(name: str, label: str, values: Iterable[str]) -> str:
    options = '<option value="">all</option>' + "".join(
        f'<option value="{esc(v)}">{esc(v)}</option>' for v in values
    )
    return (
        f'<label>{esc(label)} <select data-filter="{esc(name)}">{options}'
        "</select></label>"
    )


@dataclasses.dataclass(frozen=True)
class Links:
    """The site-relative URL of each linked file, or None where none exists."""

    refs: Mapping[str, str | None]
    videos: Mapping[str, str | None]
    frames: Mapping[str, str | None]
    #: An edit's input image name -> its URL (#1424).
    inputs: Mapping[str, str | None] = dataclasses.field(default_factory=dict)


def render(
    cards: list[Card],
    prompts: Mapping[str, Prompt],
    refs_info: Mapping[str, Mapping[str, Any]],
    links: Links,
    frames_root: pathlib.Path,
) -> str:
    """The whole page. Deterministic: the same inputs give the same bytes.

    The video prompts come first, each with a section even before its first
    run. Then the image prompts and the edit prompts, under their own
    headings, each once it has a run (#1424).
    """
    by_part: dict[str, list[str]] = {
        VIDEO: sorted(
            {c.clip for c in cards if c.part == VIDEO}
            | {k for k, p in prompts.items() if p.part == VIDEO}
        ),
        IMAGE: sorted({c.clip for c in cards if c.part == IMAGE}),
        EDIT: sorted({c.clip for c in cards if c.part == EDIT}),
    }
    headed = bool(by_part[IMAGE] or by_part[EDIT])
    clips = [clip for part, _ in PARTS for clip in by_part[part]]
    sections = []
    for part, heading in PARTS:
        if headed and by_part[part]:
            sections.append(
                f'<h2 class="part" data-part="{esc(part)}">{esc(heading)}</h2>'
            )
        for clip in by_part[part]:
            group_cards = sorted(
                (c for c in cards if c.clip == clip and c.part == part),
                key=Card.sort_key,
            )
            prompt = prompts.get(clip)
            text = prompt.text if prompt else None
            if not text:
                for card in group_cards:
                    sent = fields_of(card.run).get("prompt")
                    if sent:
                        text = str(sent)
                        break
            pid = f" ({prompt.prompt_id})" if prompt and prompt.prompt_id else ""
            if part == VIDEO:
                ref = refs_info.get(clip, {})
                items = [ref_card(clip, links.refs.get(clip), prompt, ref)]
                frame = links.frames.get(clip)
                items += [
                    run_card(c, links.videos.get(c.key), frame, frames_root)
                    for c in group_cards
                ]
                button = (
                    '<button class="playall" type="button">'
                    "play all from the start</button>"
                )
            else:
                items = [
                    image_card(c, links.videos.get(c.key), links.inputs)
                    for c in group_cards
                ]
                button = ""
            sections.append(
                f'<section class="prompt" data-part="{esc(part)}" '
                f'data-clip="{esc(clip)}">'
                f'<div class="prompt-head"><h2>{esc(clip)}{esc(pid)}</h2>'
                f"{button}</div>"
                f'<p class="prompt-text">{esc(text or "no prompt text found")}</p>'
                f'<div class="grid">{"".join(items)}</div></section>'
            )
    models = sorted({str(c.run.get("model") or DASH) for c in cards})
    arms = sorted({c.arm for c in cards})
    seeds = sorted({c.seed for c in cards})
    newest = max((fmt_date(c.run) for c in cards), default=DASH)
    filters = "".join(
        [
            select("model", "model", models),
            select("arm", "arm", arms),
            select("kind", "probe or scored", [SCORED, PROBE]),
            select("seed", "seed", [str(s) for s in seeds]),
        ]
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Video-gen gallery</title>"
        f"<style>{CSS}</style></head><body>"
        '<header><div class="top"><h1>Video-gen gallery</h1>'
        # A relative action: it works under /videogen/ and at the root.
        '<form class="logout" method="post" action="logout">'
        '<button type="submit">log out</button></form></div>'
        f'<p class="muted">{len(cards)} runs across {len(clips)} prompts. '
        f"Newest run: {esc(newest)}. Times are America/New_York.</p>"
        f'<div class="filters">{filters}<span id="count" class="muted"></span></div>'
        "</header>"
        f"<main>{''.join(sections)}</main>"
        f"<script>{JS}</script></body></html>\n"
    )


# ------------------------------------------------------------------ build ----


def write_atomic(path: pathlib.Path, text: str) -> bool:
    """Write `text` to `path` through a temp file and a rename.

    Returns False and leaves the file alone when it already holds `text`, so
    an idle rebuild does not touch the page.
    """
    try:
        if path.read_text() == text:
            return False
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return True


def build(
    root: pathlib.Path = DEFAULT_ROOT,
    out: pathlib.Path | None = None,
    *,
    prompts_file: pathlib.Path = PROMPTS_FILE,
    refs_file: pathlib.Path = REFS_FILE,
    image_prompts_files: Iterable[pathlib.Path] = IMAGE_PROMPTS_FILES,
    inputs_root: pathlib.Path | None = None,
) -> BuildResult:
    """Build or refresh the site. Raises InsideRepo for an output in a repo."""
    root = root.expanduser().resolve()
    out = (out if out is not None else root / "gallery").expanduser()
    refuse_inside_repo(out)
    out.mkdir(parents=True, exist_ok=True)
    out = out.resolve()
    media = out / MEDIA
    frames_root = root / "frames"

    cards = find_cards(root)
    prompts = load_prompts(prompts_file)
    for path in image_prompts_files:
        for clip, prompt in load_prompts(path).items():
            prompts.setdefault(clip, prompt)
    # The edits' input images (common.IMAGE_INPUTS_ROOT under the default root).
    inputs_root = inputs_root or root / "ref" / "imagegen"
    refs_info = load_refs(refs_file)
    keep: set[pathlib.Path] = set()
    made = 0

    def link(rel: pathlib.PurePosixPath, target: pathlib.Path) -> None:
        nonlocal made
        made += ensure_link(out / rel, target)
        keep.add(out / rel)

    refs: dict[str, str | None] = {}
    for clip in sorted({c.clip for c in cards} | set(prompts)):
        target = root / "ref" / f"{clip}.mp4"
        refs[clip] = None
        if target.is_file():
            link(ref_link_path(clip), target)
            refs[clip] = media_url(ref_link_path(clip))

    frames: dict[str, str | None] = {}
    for clip in sorted({c.clip for c in cards if mode_of(c.run) == "I2V"}):
        target = frames_root / clip / "first.png"
        frames[clip] = None
        if target.is_file():
            link(frame_link_path(clip), target)
            frames[clip] = media_url(frame_link_path(clip))

    inputs: dict[str, str | None] = {}
    for name in sorted({n for c in cards if c.part == EDIT for n in input_names(c)}):
        target = inputs_root / name
        inputs[name] = None
        if target.is_file():
            link(input_link_path(name), target)
            inputs[name] = media_url(input_link_path(name))

    videos: dict[str, str | None] = {}
    for card in cards:
        # A clip, or an image (#1424); the link's suffix says which.
        video = card.image if card.is_image else card.video
        videos[card.key] = None
        if video is None:
            continue
        link(link_path(card), video)
        sha = as_dict(card.run.get("output")).get("sha256")
        videos[card.key] = media_url(
            link_path(card), sha if isinstance(sha, str) else None
        )

    removed = prune_links(media, keep)
    # The policy first: a page whose hashes the server does not yet send
    # would load with its CSS and JS blocked.
    write_atomic(out / CSP_FILE, csp() + "\n")
    index = out / INDEX
    page = render(
        cards, prompts, refs_info, Links(refs, videos, frames, inputs), frames_root
    )
    changed = write_atomic(index, page)
    result = BuildResult(
        index=index,
        cards=len(cards),
        refs=sum(1 for v in refs.values() if v),
        links=made,
        removed=removed,
        changed=changed,
    )
    logger.info(
        "gallery %s: %d runs, %d reference clips, %d links made, %d removed, index %s",
        out,
        result.cards,
        result.refs,
        result.links,
        result.removed,
        "rewritten" if changed else "unchanged",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--root",
        type=pathlib.Path,
        default=DEFAULT_ROOT,
        help="the video-gen root that holds out/, probe/, ref/, frames/ and scores/",
    )
    p.add_argument(
        "--out",
        type=pathlib.Path,
        help="where to write the site (default: ROOT/gallery)",
    )
    p.add_argument("--prompts", type=pathlib.Path, default=PROMPTS_FILE)
    p.add_argument("--refs", type=pathlib.Path, default=REFS_FILE)
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    try:
        build(args.root, args.out, prompts_file=args.prompts, refs_file=args.refs)
    except InsideRepo as err:
        logger.error("%s", err)
        return REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
