"""Build a static gallery of every video-gen clip, for a browser on the LAN (#1413).

    gallery.py [--root ~/bench-logs/videogen] [--out ROOT/gallery]

The site has one page, `index.html`. It groups the clips by prompt (clip id).
Each group shows the prompt text, the reference clip first, then every
model's output, ordered by arm, then seed. Each card shows the arm, the model,
the size, the seed, the status, probe or scored, the wall time, the outlet
energy, the peak memory, the date and the judge's scores.

The script does not copy a video. It makes a relative symlink under
`media/` for each clip, so the site stays small and a rebuild is cheap.
`gallery_serve.py` serves the site, and it refuses a link that resolves
outside the video-gen root.

Inputs, all under `--root`:

- `out/<arm>/<clip>-s<seed>/{clip.mp4,run.json}`: scored runs.
- `probe/<arm>/<clip>-s<seed>-<stamp>/{clip.mp4,run.json}`: probes.
- `ref/<clip>.mp4`: the reference clips.
- `scores/<arm>/<run dir name>.json`: optional judge scores. For a scored run
  that is `scores/<arm>/<clip>-s<seed>.json`. The schema:

      {"schema": 1, "adherence": {"passed": 11, "total": 12}, "defects": 0,
       "judge": "who scored it", "notes": "free text"}

  `adherence` and `defects` are required; `judge` and `notes` are optional.
  A run with no file shows "not scored".

A rebuild is idempotent. It writes `index.html` through a temp file and a
rename, so a browser never reads half a page, and it leaves the file alone
when the page has not changed. It removes a link whose run is gone.

The output must be outside every git work tree: the clips are private and
this repository is public.
"""

from __future__ import annotations

import argparse
import dataclasses
import html
import json
import logging
import os
import pathlib
import re
import sys
import tempfile
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import DEFAULT_ROOT, InsideRepo, refuse_inside_repo

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_FILE = REPO / "benchmarks" / "videogen" / "prompts" / "v1.json"

INDEX = "index.html"
MEDIA = "media"
SCORED, PROBE = "scored", "probe"
REFUSED = 2

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
    """One generated clip (or one failed attempt) and what its run.json says."""

    kind: str  # SCORED or PROBE
    arm: str
    clip: str
    seed: int
    run_dir: pathlib.Path
    run: Mapping[str, Any]
    score: Score | None
    score_error: str | None

    @property
    def key(self) -> str:
        """`<kind dir>/<arm>/<run dir name>`, unique per card."""
        top = "out" if self.kind == SCORED else "probe"
        return f"{top}/{self.arm}/{self.run_dir.name}"

    @property
    def video(self) -> pathlib.Path | None:
        clip = self.run_dir / "clip.mp4"
        return clip if clip.is_file() else None

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


def load_prompts(path: pathlib.Path) -> dict[str, Prompt]:
    """Clip id (the reference file's stem, `dog-a`) -> its frozen prompt."""
    data = read_json(path)
    if data is None:
        logger.warning(
            "cannot read the prompts file %s; prompts come from run.json", path
        )
        return {}
    found: dict[str, Prompt] = {}
    for item in data.get("clips") or []:
        if not isinstance(item, dict) or not item.get("ref"):
            continue
        clip = pathlib.PurePath(str(item["ref"])).stem
        seconds = item.get("seconds")
        found[clip] = Prompt(
            clip=clip,
            prompt_id=item.get("id"),
            text=item.get("prompt"),
            seconds=float(seconds) if isinstance(seconds, (int, float)) else None,
        )
    return found


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


def load_score(path: pathlib.Path) -> tuple[Score | None, str | None]:
    """(score, None), (None, None) when there is no file, or (None, why)."""
    if not path.exists():
        return None, None
    data = read_json(path)
    if data is None:
        logger.warning("cannot read the score file %s", path)
        return None, "score file unreadable"
    try:
        return parse_score(data), None
    except (ValueError, TypeError) as err:
        logger.warning("bad score file %s: %s", path, err)
        return None, f"bad score file: {err}"


def find_cards(root: pathlib.Path) -> list[Card]:
    """Every run directory under out/ and probe/ that holds a run.json."""
    cards: list[Card] = []
    for kind, top in ((SCORED, "out"), (PROBE, "probe")):
        for run_json in sorted((root / top).glob("*/*/run.json")):
            run_dir = run_json.parent
            run = read_json(run_json)
            if run is None:
                logger.warning("skipped %s: run.json is not a JSON object", run_dir)
                continue
            match = RUN_DIR.match(run_dir.name)
            clip = run.get("clip") or (match["clip"] if match else None)
            seed = run.get("seed")
            if seed is None and match:
                seed = match["seed"]
            try:
                seed_int = int(seed)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                logger.warning("skipped %s: no clip id or seed", run_dir)
                continue
            if not clip:
                logger.warning("skipped %s: no clip id or seed", run_dir)
                continue
            arm = str(run.get("arm") or run_dir.parent.name)
            score, score_error = load_score(
                root / "scores" / arm / f"{run_dir.name}.json"
            )
            cards.append(
                Card(
                    kind=kind,
                    arm=arm,
                    clip=str(clip),
                    seed=seed_int,
                    run_dir=run_dir,
                    run=run,
                    score=score,
                    score_error=score_error,
                )
            )
    return cards


# ------------------------------------------------------------- formatting ----


def fmt_wall(seconds: Any) -> str:
    """`40 min 39 s` from 2438.7; `-` when unknown."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return "-"
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def fmt_energy(run: Mapping[str, Any]) -> str:
    """The outlet energy; a partial reading is marked so."""
    wh = run.get("energy_wh")
    if isinstance(wh, (int, float)):
        return f"{wh:.1f} Wh"
    partial = (run.get("outlet") or {}).get("energy_wh")
    if isinstance(partial, (int, float)):
        return f"{partial:.1f} Wh (incomplete)"
    return "-"


def fmt_peak(run: Mapping[str, Any]) -> str:
    peak = (run.get("memory") or {}).get("peak_used_gib")
    return f"{peak:.1f} GiB" if isinstance(peak, (int, float)) else "-"


def fmt_size(run: Mapping[str, Any]) -> str:
    """`1280x704 (primary)`, from the request that was sent."""
    fields = (run.get("request") or {}).get("fields") or {}
    width, height = fields.get("width"), fields.get("height")
    dims = f"{width}x{height}" if width and height else "-"
    label = run.get("size")
    return f"{dims} ({label})" if label else dims


def fmt_date(run: Mapping[str, Any]) -> str:
    """`2026-11-17 21:55` from the ISO start; the full value goes in a title."""
    start = run.get("start")
    if not isinstance(start, str) or len(start) < 16:
        return "-"
    return start[:16].replace("T", " ")


def media_url(rel: pathlib.PurePosixPath, version: str | None = None) -> str:
    url = "/".join(urllib.parse.quote(part) for part in rel.parts)
    if version:
        # A rebuilt clip keeps its path; the sha in the URL defeats the cache.
        url += "?v=" + urllib.parse.quote(version[:12])
    return url


# ---------------------------------------------------------------- links ----


def link_path(card: Card) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(MEDIA, *card.key.split("/")).with_suffix(".mp4")


def ref_link_path(clip: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(MEDIA, "ref", f"{clip}.mp4")


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
  --accent: #2456a6; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #141414; --fg: #e8e8e6; --muted: #a3a3a0; --card: #1f1f1f;
    --line: #333331; --ok: #5cc27a; --bad: #f2867e; --probe: #e0b45c; --accent: #8ab4f8; }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header, main { padding: 12px 16px; max-width: 1800px; margin: 0 auto; }
h1 { font-size: 1.3rem; margin: 0 0 8px; }
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
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.card.ref { border-color: var(--accent); }
video { width: 100%; aspect-ratio: 16 / 9; background: #000; border-radius: 4px; }
.novideo { aspect-ratio: 16 / 9; display: grid; place-items: center;
  border: 1px dashed var(--line); border-radius: 4px; color: var(--muted); }
.title { font-weight: 600; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 10px; margin: 0;
  font-size: 0.9rem; }
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; }
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
    count.textContent = shown + ' of ' + total + ' clips shown';
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


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


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


def run_card(card: Card, src: str | None) -> str:
    run = card.run
    status = str(run.get("status") or "unknown")
    status_cls = "ok" if status == "ok" else "bad"
    model = str(run.get("model") or "-")
    revision = run.get("revision")
    model_text = f"{model} @{revision}" if revision else model
    kind_tag = (
        '<span class="tag probe">probe</span>'
        if card.kind == PROBE
        else '<span class="tag ok">scored</span>'
    )
    rows = [
        ("model", esc(model_text)),
        ("size", esc(fmt_size(run))),
        ("seed", esc(card.seed)),
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
        rows.insert(4, ("error", f'<span class="bad">{esc(run["error"])}</span>'))
    body = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    return (
        f'<article class="card run" data-run="{esc(card.key)}" '
        f'data-arm="{esc(card.arm)}" data-model="{esc(model)}" '
        f'data-kind="{esc(card.kind)}" data-seed="{esc(card.seed)}">'
        f"{video_tag(src)}"
        f'<div class="title">{esc(card.arm)} · seed {esc(card.seed)}{kind_tag}</div>'
        f"<dl>{body}</dl></article>"
    )


def ref_card(clip: str, src: str | None, prompt: Prompt | None) -> str:
    length = f"{prompt.seconds:g} s" if prompt is not None and prompt.seconds else "-"
    return (
        f'<article class="card ref" data-ref="{esc(clip)}">'
        f"{video_tag(src)}"
        f'<div class="title">reference: {esc(clip)}</div>'
        f"<dl><dt>source</dt><dd>the real clip the prompt was written from</dd>"
        f"<dt>length</dt><dd>{esc(length)}</dd></dl></article>"
    )


def select(name: str, label: str, values: Iterable[str]) -> str:
    options = '<option value="">all</option>' + "".join(
        f'<option value="{esc(v)}">{esc(v)}</option>' for v in values
    )
    return (
        f'<label>{esc(label)} <select data-filter="{esc(name)}">{options}'
        "</select></label>"
    )


def render(
    cards: list[Card],
    prompts: Mapping[str, Prompt],
    refs: Mapping[str, str | None],
    srcs: Mapping[str, str | None],
) -> str:
    """The whole page. Deterministic: the same inputs give the same bytes."""
    clips = sorted({c.clip for c in cards} | set(prompts))
    sections = []
    for clip in clips:
        group = sorted((c for c in cards if c.clip == clip), key=Card.sort_key)
        prompt = prompts.get(clip)
        text = prompt.text if prompt else None
        if not text:
            for card in group:
                sent = ((card.run.get("request") or {}).get("fields") or {}).get(
                    "prompt"
                )
                if sent:
                    text = str(sent)
                    break
        pid = f" ({prompt.prompt_id})" if prompt and prompt.prompt_id else ""
        items = [ref_card(clip, refs.get(clip), prompt)]
        items += [run_card(c, srcs.get(c.key)) for c in group]
        sections.append(
            f'<section class="prompt" data-clip="{esc(clip)}">'
            f'<div class="prompt-head"><h2>{esc(clip)}{esc(pid)}</h2>'
            f'<button class="playall" type="button">play all from the start</button>'
            f"</div>"
            f'<p class="prompt-text">{esc(text or "no prompt text found")}</p>'
            f'<div class="grid">{"".join(items)}</div></section>'
        )
    models = sorted({str(c.run.get("model") or "-") for c in cards})
    arms = sorted({c.arm for c in cards})
    seeds = sorted({c.seed for c in cards})
    newest = max((fmt_date(c.run) for c in cards), default="-")
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
        "<header><h1>Video-gen gallery</h1>"
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
) -> BuildResult:
    """Build or refresh the site. Raises InsideRepo for an output in a repo."""
    root = root.expanduser().resolve()
    out = (out if out is not None else root / "gallery").expanduser()
    refuse_inside_repo(out)
    out.mkdir(parents=True, exist_ok=True)
    out = out.resolve()
    media = out / MEDIA

    cards = find_cards(root)
    prompts = load_prompts(prompts_file)
    keep: set[pathlib.Path] = set()
    links = 0

    refs: dict[str, str | None] = {}
    for clip in sorted({c.clip for c in cards} | set(prompts)):
        target = root / "ref" / f"{clip}.mp4"
        if not target.is_file():
            refs[clip] = None
            continue
        rel = ref_link_path(clip)
        links += ensure_link(out / rel, target)
        keep.add(out / rel)
        refs[clip] = media_url(rel)

    srcs: dict[str, str | None] = {}
    for card in cards:
        video = card.video
        if video is None:
            srcs[card.key] = None
            continue
        rel = link_path(card)
        links += ensure_link(out / rel, video)
        keep.add(out / rel)
        sha = (card.run.get("output") or {}).get("sha256")
        srcs[card.key] = media_url(rel, sha if isinstance(sha, str) else None)

    removed = prune_links(media, keep)
    index = out / INDEX
    changed = write_atomic(index, render(cards, prompts, refs, srcs))
    result = BuildResult(
        index=index,
        cards=len(cards),
        refs=sum(1 for v in refs.values() if v),
        links=links,
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
        help="the video-gen root that holds out/, probe/, ref/ and scores/",
    )
    p.add_argument(
        "--out",
        type=pathlib.Path,
        help="where to write the site (default: ROOT/gallery)",
    )
    p.add_argument("--prompts", type=pathlib.Path, default=PROMPTS_FILE)
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    try:
        build(args.root, args.out, prompts_file=args.prompts)
    except InsideRepo as err:
        logger.error("%s", err)
        return REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
