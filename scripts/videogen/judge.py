"""Blind the scored clips for the judge, and ingest its scores (#1406).

    judge.py blind --arms h3-t2v wan-t2v [--clips dog-a ...] --out DIR
    judge.py ingest --blind DIR --answers FILE.json --judge LABEL [--overwrite]

A Claude subagent judges every scored clip from its extracted frames, with a
fixed checklist and a fixed defect list (the method is in #1402). The judge
must not know which model made a clip, so `blind` hides it:

- Each run gets a random 6-character id from `secrets`. Nothing seeds it, so
  the public code cannot rebuild the map; the key file is the only record.
- Only `s*.jpg` and `strip.jpg` go to `DIR/<clip>/<id>/`. `first.png` and
  `meta.json` stay behind. No path or file the judge reads names the arm,
  the model or the seed, and `blind` refuses an `--out` path that names an
  arm or a model.
- The runs are copied in a random order, and every file and folder then gets
  one fixed mtime, so neither the times nor the inode order show which runs
  were copied together.
- `DIR/<clip>/PROMPT.md` is the judge prompt for one clip: its folders in a
  random order, its checklist, and the numbered fixed defect list.
- The key maps each id back to its run. It lives OUTSIDE the blind tree, at
  `<root>/judge/keys/<DIR name>.json` with mode 0600, because the judge can
  read anything under DIR.

Every expected run (each seed in arms-v1.json) must be there, unless
`--allow-missing`. The stills on disk must be exactly the stills `meta.json`
names, every copy must match the sha256 `meta.json` recorded, and the run's
`clip.mp4` must match `meta.json`'s `clip_sha256`. Stale frames would score one
clip under another clip's name.

`ingest` reads one judge reply, checks all of it, and only then writes
`<root>/scores/<arm>/<clip>-s<seed>.json` in the schema `gallery.py` reads. It
re-hashes each clip first: a run remade after `blind` must not get the old
clip's score. Any error refuses the whole file and writes nothing: a wrong id
or item would put a score on the wrong run with no error anywhere.

The blind directory, the key and the scores live outside every git work tree:
the frames are private and this repository is public.

**Image arms (#1424).** For an arm in `image-arms-v1.json` or
`edit-arms-v1.json`, the image itself is the evidence: `blind` copies the
run's `image.png` (checked against the sha256 its run.json records) to
`DIR/<prompt>/<id>/image.png`, with no frame extraction. The copy is not
the file the server sent: `blind` re-encodes it to a normalized PNG (RGB,
alpha flattened on white, LANCZOS to BLIND_SIZE for its mode, a fixed
compress level, no ancillary chunks). The server's PNG carries its encoder,
its chunks and, for HiDream-O1, a 2048x2048 size; any of these would name
the model to a judge who reads the file. The key records the source sha256
and the blind copy's own sha256, and `ingest` checks the blind copy against
it. An edit prompt's
input images go once to `DIR/<prompt>/input/image-1.jpg`, `image-2.jpg`,
..., checked against the sha256 that `prompts/edit-v1.json` pins, and the
judge prompt names them as the reference. The ids, the fixed mtime, the
random order, the key outside DIR and `ingest` are the same as for clips.
`--arms-file`, `--prompts` and `--clips` default to the file that defines
the arms, its prompts file and its prompt list.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import string
import sys
import tempfile
from collections.abc import Mapping, MutableSequence, Sequence
from typing import Any, Protocol

from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import (
    DEFAULT_ROOT,
    EDIT_ARMS_FILE,
    IMAGE_ARMS_FILE,
    InsideRepo,
    Refused,
    prompt_name,
    refuse_inside_repo,
    sha256,
)
from videogen.gallery import parse_score

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_FILE = REPO / "benchmarks" / "videogen" / "prompts" / "v1.json"
ARMS_FILE = REPO / "benchmarks" / "videogen" / "arms-v1.json"
ARMS_FILES = (ARMS_FILE, IMAGE_ARMS_FILE, EDIT_ARMS_FILE)
REFUSED = 2
IMAGE = "image.png"
#: The folder of an edit prompt's input images, beside the candidates' ids.
INPUT_DIR = "input"
OLD_KEY = "key.json"  # the first blind dirs kept the key inside; see ingest
PROMPT = "PROMPT.md"
ID_CHARS = string.ascii_lowercase + string.digits
ID_LEN = 6
#: One path component: no dot, no slash. Arm and clip names become paths.
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
STILL = re.compile(r"^s\d+\.jpg$")
STRIP = "strip.jpg"
#: The size of a blind image copy, by arm mode: every candidate of a prompt
#: looks the same size to the judge, whatever size the model drew.
BLIND_SIZE = {"t2i": (1024, 1024), "edit": (1024, 576)}
#: zlib level of a blind image copy: one level for every candidate.
BLIND_COMPRESS = 6
#: The background an image with alpha is flattened on.
BLIND_BACKGROUND = (255, 255, 255)
#: Every blinded file and folder gets this mtime (2000-01-01T00:00:00Z), so
#: the times do not show the copy order. ctime cannot be set; the random
#: copy order covers it.
FIXED_MTIME = 946684800

CONTROL_PROMPT = """\
You are a blind judge for a video evaluation. You score short video clips from \
still frames. You do not know where the clips came from. Judge only what you see \
in the images. Do not guess about anything you cannot see.

For each clip, read every image in its folder with the Read tool:
- `s01.jpg`, `s02.jpg`, ... are frames at 1 frame per second, in time order.
- `strip.jpg` is a tile of 8 frames at 4 fps (2 seconds), with a timestamp in \
the top-left of each tile. Use it for the defect check.

Folders:
{folders}

Do not open any other file, and do not read any file outside those folders.

## Task 1: adherence
Mark each checklist item true or false. Give a reason of 15 words or fewer that \
points to a frame (for example "s03: hose lower left").

Checklist (the same for every clip):
{checklist}

## Task 2: defects
Use the frames and the strip. Count each defect you see from this fixed list, \
and name the frame:
{defects}

Occlusion is not a defect. A leg hidden behind another dog, or an object that \
leaves the frame because the camera moves, does not count. Count only a defect \
you can see clearly.

## Report
Reply with one JSON object and nothing else:
{{"<id>": {{"items": [{{"n": 1, "true": true, "why": "..."}}, ...], \
"defects": [{{"type": "...", "frame": "...", "note": "..."}}]}}, ...}}
"""


IMAGE_PROMPT = """\
You are a blind judge for an image evaluation. You score generated images. \
You do not know where the images came from. Judge only what you see in the \
images. Do not guess about anything you cannot see.
{inputs}
For each candidate, read `image.png` in its folder with the Read tool.

Candidates:
{folders}

Do not open any other file{other}, and do not read any file outside those \
folders.

## Task 1: adherence
Mark each checklist item true or false. Give a reason of 15 words or fewer that \
points to a part of the image (for example "bottom caption: ROOMS misspelled").

Checklist (the same for every candidate):
{checklist}

## Task 2: defects
Count each defect you see from this fixed list. Put where it is in the image \
in "frame" (for example "lower left"):
{defects}

Count only a defect you can see clearly.

## Report
Reply with one JSON object and nothing else:
{{"<id>": {{"items": [{{"n": 1, "true": true, "why": "..."}}, ...], \
"defects": [{{"type": "...", "frame": "...", "note": "..."}}]}}, ...}}
"""

INPUTS_TEXT = """
Every candidate is an edit of the same input image(s), made from the same \
instruction. Read the input images first. They are the reference, not \
candidates; "image 1" and "image 2" in the checklist mean these files:
{lines}
"""


class DuplicateKey(ValueError):
    """A JSON object holds the same key twice."""


class Shuffler(Protocol):
    def shuffle(self, x: MutableSequence[Any], /) -> None: ...


@dataclasses.dataclass(frozen=True)
class Run:
    arm: str
    clip: str
    seed: int
    frames: pathlib.Path
    clip_path: pathlib.Path
    clip_sha256: str
    hashes: Mapping[str, str]  # meta.json outputs_sha256
    files: tuple[str, ...]  # the stills and the strip, in name order


def now_iso() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def read_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as err:
        raise Refused(f"{path} is not readable JSON: {err}") from err


def read_object(path: pathlib.Path) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise Refused(f"{path} must hold a JSON object")
    return data


def refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json object_pairs_hook: a repeated key would silently drop an answer."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateKey(f"duplicate key {key!r}")
        out[key] = value
    return out


def load_prompts(path: pathlib.Path) -> tuple[str, dict[str, list[str]], list[str]]:
    """(version, run-dir clip id -> checklist, fixed defect list).

    The run directories name a clip by its reference stem (`dog-a`), and the
    prompt file names it `dog-a-v1`; the `ref` field links the two. An image
    prompt names itself (`name`).
    """
    data = read_object(path)
    checklists: dict[str, list[str]] = {}
    for item in data.get("clips") or []:
        clip = prompt_name(item)
        checklist = [str(line) for line in item.get("checklist") or []]
        if not checklist:
            raise Refused(f"{path}: {clip} has an empty checklist")
        checklists[clip] = checklist
    defects = [str(d) for d in data.get("defects") or []]
    if not checklists or not defects:
        raise Refused(f"{path} holds no checklist or no defect list")
    return str(data.get("version")), checklists, defects


def load_inputs(path: pathlib.Path) -> dict[str, list[dict[str, Any]]]:
    """Prompt name -> its input image specs ({file, sha256}), for edit prompts."""
    data = read_object(path)
    out: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("clips") or []:
        specs = item.get("inputs") or []
        if specs:
            out[prompt_name(item)] = [dict(s) for s in specs]
    return out


def resolve(
    arms: Sequence[str],
    arms_file: pathlib.Path | None,
    prompts_file: pathlib.Path | None,
) -> tuple[pathlib.Path, pathlib.Path, str]:
    """(arms file, prompts file, media) for `arms`.

    With no `--arms-file`, the one file that defines every arm. With no
    `--prompts`, the file that the arms file names (or v1.json for video).
    """
    if arms_file is None:
        holders = [
            path
            for path in ARMS_FILES
            if all(a in (read_object(path).get("arms") or {}) for a in arms)
        ]
        if len(holders) != 1:
            raise Refused(
                f"{list(arms)} are not all arms of one arms file; pass --arms-file"
            )
        arms_file = holders[0]
    data = read_object(arms_file)
    media = str(data.get("media") or "video")
    if prompts_file is None:
        prompts_file = (
            arms_file.parent / str(data["prompts"])
            if media == "image"
            else PROMPTS_FILE
        )
    return arms_file, prompts_file, media


def load_arms(path: pathlib.Path) -> tuple[dict[str, dict[str, Any]], list[int]]:
    """(arm -> its settings, the seeds every arm runs) from arms-v1.json."""
    data = read_object(path)
    arms = data.get("arms")
    seeds = data.get("seeds")
    if not isinstance(arms, dict) or not isinstance(seeds, list) or not seeds:
        raise Refused(f"{path} holds no arms or no seeds")
    return arms, [int(s) for s in seeds]


def model_names(arms: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """Lower-case names that identify a model: the arm names, the arm's model
    prefix (`h3`), the model ids, their parts and first tokens (`minimax`,
    `wan2.2`, `minimax-h3`)."""
    names: set[str] = set()
    for arm, cfg in arms.items():
        names |= {arm.lower(), arm.split("-")[0].lower()}
        model = str(cfg.get("model") or "").lower()
        if not model:
            continue
        names.add(model)
        for part in model.split("/"):
            names |= {part, part.split("-")[0]}
    return {n for n in names if n}


def names_in_path(path: pathlib.Path, names: set[str]) -> list[str]:
    """The names that `path` contains. A short plain name (`h3`, `wan`) must
    be a whole token, so `evan` does not match `wan`; a longer name matches
    anywhere."""
    text = str(path).lower()
    tokens = set(re.split(r"[^a-z0-9]+", text))
    hits = []
    for name in sorted(names):
        short = name.isalnum() and len(name) <= 4
        if (name in tokens) if short else (name in text):
            hits.append(name)
    return hits


# ------------------------------------------------------------------ blind ----


def refuse_out(out: pathlib.Path, key_path: pathlib.Path, names: set[str]) -> None:
    """Refuse an --out inside git, not empty, or with a path that names a model,
    and a key under --out or already there."""
    refuse_inside_repo(out)
    refuse_inside_repo(key_path)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refused(f"{out} is not empty; give a new directory")
    # The judge sees the absolute path of every folder, so the path must not
    # name the model.
    hits = names_in_path(out, names)
    if hits:
        raise Refused(
            f"{out} names {hits}; the judge reads this path, "
            "so it must not name an arm or a model"
        )
    if key_path.is_relative_to(out):
        raise Refused(f"the key {key_path} is under {out}, where the judge reads")
    if key_path.exists():
        raise Refused(f"the key {key_path} exists; give a new --out name")


def find_run(
    root: pathlib.Path, arm: str, clip: str, seed: int
) -> tuple[Run | None, str]:
    """(the run, "") if it is scored and has frames, else (None, why not).

    Raise Refused when the frames do not belong to the clip, or a still is
    missing: those are not "not done yet" but broken.
    """
    name = f"{clip}-s{seed}"
    out = root / "out" / arm / name
    frames = root / "frames-out" / arm / name
    if not (out / "run.json").is_file():
        return None, f"no {out / 'run.json'}"
    run = read_json(out / "run.json")
    if not isinstance(run, dict):
        raise Refused(f"{out / 'run.json'} must hold a JSON object")
    if run.get("status") != "ok":
        return None, f"{out / 'run.json'} status is {run.get('status')!r}, not 'ok'"
    clip_file = out / "clip.mp4"
    if not clip_file.is_file():
        return None, f"no {clip_file}"
    if not (frames / "meta.json").is_file():
        return None, f"no frames in {frames}"
    meta = read_object(frames / "meta.json")
    if sha256(clip_file) != meta.get("clip_sha256"):
        raise Refused(
            f"{frames}: meta.json clip_sha256 does not match {clip_file}; "
            "rerun frames.py on that clip"
        )
    hashes = meta.get("outputs_sha256")
    if not isinstance(hashes, dict):
        raise Refused(f"{frames / 'meta.json'} has no outputs_sha256 object")
    on_disk = {p.name for p in frames.iterdir() if STILL.match(p.name)}
    in_meta = {n for n in hashes if STILL.match(n)}
    if on_disk != in_meta or not on_disk:
        raise Refused(
            f"{frames}: stills on disk {sorted(on_disk)} are not the stills "
            f"meta.json names {sorted(in_meta)}"
        )
    if not (frames / STRIP).is_file() or STRIP not in hashes:
        raise Refused(f"{frames}: no {STRIP}, or meta.json has no hash for it")
    return (
        Run(
            arm=arm,
            clip=clip,
            seed=seed,
            frames=frames,
            clip_path=clip_file,
            clip_sha256=str(meta["clip_sha256"]),
            hashes={str(k): str(v) for k, v in hashes.items()},
            files=(*sorted(on_disk), STRIP),
        ),
        "",
    )


def find_image_run(
    root: pathlib.Path, arm: str, clip: str, seed: int
) -> tuple[Run | None, str]:
    """(the run, "") if it is scored and has its image, else (None, why not).

    Raise Refused when the image is not the one its run.json records: a
    stale image would be scored under the wrong run.
    """
    out = root / "out" / arm / f"{clip}-s{seed}"
    if not (out / "run.json").is_file():
        return None, f"no {out / 'run.json'}"
    run = read_json(out / "run.json")
    if not isinstance(run, dict):
        raise Refused(f"{out / 'run.json'} must hold a JSON object")
    if run.get("status") != "ok":
        return None, f"{out / 'run.json'} status is {run.get('status')!r}, not 'ok'"
    image = out / IMAGE
    if not image.is_file():
        return None, f"no {image}"
    got = sha256(image)
    output = run.get("output")
    if not isinstance(output, dict) or output.get("sha256") != got:
        raise Refused(f"{image} is not the image its run.json records; remake it")
    return (
        Run(
            arm=arm,
            clip=clip,
            seed=seed,
            frames=out,
            clip_path=image,
            clip_sha256=got,
            hashes={IMAGE: got},
            files=(IMAGE,),
        ),
        "",
    )


def normalize_png(
    source: pathlib.Path, dest: pathlib.Path, size: tuple[int, int]
) -> str:
    """Write `source` to `dest` as a normalized PNG and return its sha256.

    RGB; alpha flattened on BLIND_BACKGROUND; resized with LANCZOS to `size`
    if it is not that size; BLIND_COMPRESS; no ancillary chunk (the pixels
    go into a new image, so no text, ICC, gamma or pHYs chunk follows).
    """
    try:
        with Image.open(source) as im:
            im.load()
            if "A" in im.getbands() or "transparency" in im.info:
                rgba = im.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, BLIND_BACKGROUND)
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = im.convert("RGB")
    except (OSError, ValueError) as err:  # UnidentifiedImageError is an OSError
        raise Refused(f"{source} is not an image Pillow can read: {err}") from err
    if rgb.size != size:
        rgb = rgb.resize(size, Image.Resampling.LANCZOS)
    clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())
    clean.save(dest, format="PNG", compress_level=BLIND_COMPRESS)
    return sha256(dest)


def copy_inputs(
    specs: Sequence[Mapping[str, Any]], inputs_root: pathlib.Path, folder: pathlib.Path
) -> list[pathlib.Path]:
    """Copy an edit prompt's input images to `folder` as image-1.<ext>, ...

    Each source must match the sha256 the prompts file pins. Returns the
    copies, in the order the request sent them.
    """
    folder.mkdir(parents=True)
    copies = []
    for n, spec in enumerate(specs, 1):
        name = str(spec["file"])
        source = inputs_root / name
        suffix = pathlib.PurePath(name).suffix.lower()
        plain = pathlib.PurePath(name).name == name and not name.startswith(".")
        if not plain or suffix not in (".png", ".jpg", ".jpeg"):
            raise Refused(f"{name!r} is not a plain PNG or JPEG file name")
        if not source.is_file() or sha256(source) != spec["sha256"]:
            raise Refused(f"{source} is missing or not the input the prompt pins")
        copy = folder / f"image-{n}{suffix}"
        shutil.copyfile(source, copy)
        if sha256(copy) != spec["sha256"]:
            raise Refused(f"{source}: the copy does not match the pinned sha256")
        copies.append(copy)
    return copies


def render_image_prompt(
    folders: Sequence[tuple[str, pathlib.Path]],
    checklist: Sequence[str],
    defects: Sequence[str],
    inputs: Sequence[pathlib.Path],
) -> str:
    """The judge prompt for one image prompt; `inputs` are the edit's inputs."""
    inputs_text = ""
    if inputs:
        lines = "\n".join(f"- image {n}: {path}" for n, path in enumerate(inputs, 1))
        inputs_text = INPUTS_TEXT.format(lines=lines)
    return IMAGE_PROMPT.format(
        inputs=inputs_text,
        folders="\n".join(f"- candidate {i}: {path}/" for i, path in folders),
        other=" than the candidates and the input images" if inputs else "",
        checklist="\n".join(f"{n}. {line}" for n, line in enumerate(checklist, 1)),
        defects="\n".join(f"{n}. {d}" for n, d in enumerate(defects, 1)),
    )


def new_id(taken: set[str]) -> str:
    while True:
        run_id = "".join(secrets.choice(ID_CHARS) for _ in range(ID_LEN))
        if run_id not in taken:
            taken.add(run_id)
            return run_id


def render_prompt(
    folders: Sequence[tuple[str, pathlib.Path]],
    checklist: Sequence[str],
    defects: Sequence[str],
) -> str:
    return CONTROL_PROMPT.format(
        folders="\n".join(f"- clip {i}: {path}/" for i, path in folders),
        checklist="\n".join(f"{n}. {line}" for n, line in enumerate(checklist, 1)),
        defects="\n".join(f"{n}. {d}" for n, d in enumerate(defects, 1)),
    )


def write_atomic(path: pathlib.Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def fix_times(top: pathlib.Path) -> None:
    """Set every file and folder under `top` (and `top`) to FIXED_MTIME.

    Bottom-up: writing into a folder changes its mtime, so a folder is set
    after everything in it.
    """
    for dirpath, dirnames, filenames in os.walk(top, topdown=False):
        for name in (*filenames, *dirnames):
            os.utime(pathlib.Path(dirpath) / name, (FIXED_MTIME, FIXED_MTIME))
    os.utime(top, (FIXED_MTIME, FIXED_MTIME))


def default_key(root: pathlib.Path, blind_dir: pathlib.Path) -> pathlib.Path:
    return root / "judge" / "keys" / f"{blind_dir.name}.json"


def blind(
    arms: Sequence[str],
    *,
    clips: Sequence[str] | None = None,
    out: pathlib.Path,
    root: pathlib.Path = DEFAULT_ROOT,
    key_path: pathlib.Path | None = None,
    prompts_file: pathlib.Path | None = None,
    arms_file: pathlib.Path | None = None,
    inputs_root: pathlib.Path | None = None,
    allow_missing: bool = False,
    rng: Shuffler | None = None,
) -> dict[str, list[str]]:
    """Write the blind directory and its key. Return clip -> ids, in prompt order.

    `clips` None means every prompt the arms file lists. `inputs_root` None
    means `<root>/ref/imagegen` (an edit prompt's input images). `rng` is
    for tests; the default is `secrets.SystemRandom`, which nothing seeds.
    """
    for name in arms:
        if not SAFE.match(name):
            raise Refused(f"{name!r} is not a safe arm or clip name")
    arms_file, prompts_file, media = resolve(arms, arms_file, prompts_file)
    if clips is None:
        clips = [str(c) for c in read_object(arms_file).get("clips") or []]
    for name in clips:
        if not SAFE.match(name):
            raise Refused(f"{name!r} is not a safe arm or clip name")
    root = root.expanduser().resolve()
    out = out.expanduser().resolve()
    key_path = (key_path or default_key(root, out)).expanduser().resolve()
    inputs_root = (inputs_root or root / "ref" / "imagegen").expanduser()
    arm_cfg, seeds = load_arms(arms_file)
    unknown = [a for a in arms if a not in arm_cfg]
    if unknown:
        raise Refused(f"{unknown} are not arms in {arms_file}")
    refuse_out(out, key_path, model_names(arm_cfg))
    version, checklists, defects = load_prompts(prompts_file)
    input_specs = load_inputs(prompts_file) if media == "image" else {}
    for clip in clips:
        if clip not in checklists:
            raise Refused(f"{clip} has no checklist in {prompts_file}")
    finder = find_image_run if media == "image" else find_run

    runs: list[Run] = []
    missing: list[str] = []
    for arm in arms:
        found = 0
        for clip in clips:
            for seed in seeds:
                run, why = finder(root, arm, clip, seed)
                if run is None:
                    logger.warning("missing run %s %s-s%d: %s", arm, clip, seed, why)
                    missing.append(f"{arm} {clip}-s{seed}: {why}")
                else:
                    runs.append(run)
                    found += 1
        evidence = "images" if media == "image" else "frames"
        logger.info("%s: %d scored runs with %s", arm, found, evidence)
        if found == 0:
            raise Refused(f"{arm} has no scored run with {evidence} under {root}")
    if missing and not allow_missing:
        raise Refused(
            f"{len(missing)} expected runs are missing (pass --allow-missing "
            "to blind the rest):\n  " + "\n  ".join(missing)
        )

    rng = rng or secrets.SystemRandom()
    # Copy in a random order, so neither the inode order nor the ctimes
    # (which FIXED_MTIME cannot reset) show which runs are one arm's. `runs`
    # is in arm order until here; this shuffle is the last step before the
    # copy loop, for clips and images alike.
    rng.shuffle(runs)
    taken: set[str] = set()
    key: dict[str, Any] = {
        "schema": 2,
        "made": now_iso(),
        "root": str(root),
        "blind_dir": str(out),
        "prompts": version,
        "media": media,
        "arms": list(arms),
        "missing": missing,
        "checklists": {clip: checklists[clip] for clip in clips},
        "defects": defects,
        "runs": {},
    }
    ids_by_clip: dict[str, list[str]] = {clip: [] for clip in clips}
    # Build in a sibling temp directory, then rename: a failure leaves no
    # half-made blind directory that a judge could be pointed at.
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    key_written = False
    try:
        for run in runs:
            run_id = new_id(taken)
            ids_by_clip[run.clip].append(run_id)
            folder = stage / run.clip / run_id
            folder.mkdir(parents=True)
            entry: dict[str, Any] = {
                "arm": run.arm,
                "clip": run.clip,
                "seed": run.seed,
                "run_dir": str(run.frames),
                "clip_path": str(run.clip_path),
                "clip_sha256": run.clip_sha256,
            }
            if media == "image":
                mode = arm_cfg[run.arm].get("mode")
                if mode not in BLIND_SIZE:
                    raise Refused(f"{run.arm}: mode {mode!r} is not t2i or edit")
                size = BLIND_SIZE[str(mode)]
                entry["source_sha256"] = run.clip_sha256
                entry["blind_sha256"] = normalize_png(
                    run.clip_path, folder / IMAGE, size
                )
                entry["blind_size"] = list(size)
            else:
                for name in run.files:
                    # copyfile, not copy2: no mode or mtime from the source.
                    shutil.copyfile(run.frames / name, folder / name)
                    if sha256(folder / name) != run.hashes[name]:
                        raise Refused(
                            f"{run.frames / name}: the copy does not match meta.json"
                        )
            key["runs"][run_id] = entry
        order: dict[str, list[str]] = {}
        inputs: dict[str, list[str]] = {}
        for clip, ids in ids_by_clip.items():
            if not ids:
                logger.warning("%s: no runs, so no prompt", clip)
                continue
            rng.shuffle(ids)
            order[clip] = ids
            folders = [(i, out / clip / i) for i in ids]
            if media == "image":
                folder = stage / clip / INPUT_DIR
                copies = (
                    copy_inputs(input_specs[clip], inputs_root, folder)
                    if clip in input_specs
                    else []
                )
                # The judge reads the final paths, not the stage's.
                shown = [out / clip / INPUT_DIR / c.name for c in copies]
                inputs[clip] = [str(p) for p in shown]
                text = render_image_prompt(folders, checklists[clip], defects, shown)
            else:
                text = render_prompt(folders, checklists[clip], defects)
            (stage / clip / PROMPT).write_text(text)
        key["inputs"] = inputs
        key["order"] = order
        fix_times(stage)
        key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_atomic(
            key_path, json.dumps(key, indent=2, sort_keys=True) + "\n", mode=0o600
        )
        key_written = True
        if out.exists():
            out.rmdir()  # empty, checked above
        stage.rename(out)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        if key_written:
            key_path.unlink(missing_ok=True)
        raise
    for clip, ids in order.items():
        logger.info("%s: %d clips, prompt %s", clip, len(ids), out / clip / PROMPT)
    logger.info("key (do not give it to the judge): %s", key_path)
    return order


# ----------------------------------------------------------------- ingest ----


def defect_labels(defects: Sequence[str]) -> dict[object, str]:
    """What a judge may write as a defect type -> the full label.

    Judges shorten the labels: all three live judges wrote "body morph" for
    "body morph (a body changes shape in a way no dog can)". So the full
    label, the text before " (", and the list number (as int or text) all
    count. A short form two labels share is left out.
    """
    lookup: dict[object, str] = {}
    shorts: dict[str, list[str]] = {}
    for n, label in enumerate(defects, 1):
        lookup[label] = label
        lookup[n] = label
        lookup[str(n)] = label
        shorts.setdefault(label.split(" (")[0], []).append(label)
    for short, labels in shorts.items():
        if len(labels) == 1:
            lookup.setdefault(short, labels[0])
    return lookup


def check_entry(
    run_id: str,
    entry: object,
    checklist: Sequence[str],
    labels: Mapping[object, str],
) -> tuple[list[str], list[dict[str, Any]]]:
    """(every problem with one id's answer, its defects with full labels)."""
    errors: list[str] = []
    canon: list[dict[str, Any]] = []
    if not isinstance(entry, dict):
        return [f"{run_id}: the answer must be an object"], canon
    extra = set(entry) - {"items", "defects", "notes"}
    if extra:
        errors.append(f"{run_id}: unknown fields {sorted(extra)}")
    items, found = entry.get("items"), entry.get("defects")
    if not isinstance(items, list):
        errors.append(f"{run_id}: items must be a list")
        items = []
    if not isinstance(found, list):
        errors.append(f"{run_id}: defects must be a list")
        found = []
    if "notes" in entry and not isinstance(entry["notes"], str):
        errors.append(f"{run_id}: notes must be text")
    total = len(checklist)
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"{run_id}: each item must be an object")
            continue
        n = item.get("n")
        if not isinstance(n, int) or isinstance(n, bool):
            errors.append(f"{run_id}: n must be a whole number, got {n!r}")
        elif not 1 <= n <= total:
            errors.append(f"{run_id}: item {n} is outside 1..{total}")
        elif n in seen:
            errors.append(f"{run_id}: item {n} more than once")
        else:
            seen.add(n)
        if not isinstance(item.get("true"), bool):
            errors.append(f"{run_id}: true must be true or false in item {n!r}")
        if not isinstance(item.get("why"), str):
            errors.append(f"{run_id}: why must be text in item {n!r}")
    errors.extend(
        f"{run_id}: missing item {n}" for n in range(1, total + 1) if n not in seen
    )
    for defect in found:
        if not isinstance(defect, dict):
            errors.append(f"{run_id}: each defect must be an object")
            continue
        written = defect.get("type")
        label = None
        if isinstance(written, (str, int)) and not isinstance(written, bool):
            label = labels.get(written)
        if label is None:
            errors.append(f"{run_id}: defect type {written!r} is not in the fixed list")
        for field in ("frame", "note"):
            if not isinstance(defect.get(field), str):
                errors.append(f"{run_id}: defect {field} must be text")
        fixed = {**defect, "type": label}
        if written != label:
            fixed["type_as_written"] = written
        canon.append(fixed)
    return errors, canon


def read_answers(path: pathlib.Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(), object_pairs_hook=refuse_duplicates)
    except DuplicateKey as err:
        raise Refused(f"{path}: {err}") from err
    except (OSError, ValueError) as err:
        raise Refused(f"{path} is not readable JSON: {err}") from err
    if not isinstance(data, dict) or not data:
        raise Refused(f"{path}: the reply must be a non-empty JSON object")
    return data


def clip_path_of(run: Mapping[str, Any], root: pathlib.Path) -> pathlib.Path:
    """The run's clip. The first keys (schema 1) did not record it."""
    if run.get("clip_path"):
        return pathlib.Path(str(run["clip_path"]))
    name = f"{run['clip']}-s{int(run['seed'])}"
    return root / "out" / str(run["arm"]) / name / "clip.mp4"


def ingest(
    blind_dir: pathlib.Path,
    answers_file: pathlib.Path,
    *,
    judge_label: str,
    key_path: pathlib.Path | None = None,
    root: pathlib.Path = DEFAULT_ROOT,
    scores_root: pathlib.Path | None = None,
    force_scores_root: bool = False,
    overwrite: bool = False,
) -> list[pathlib.Path]:
    """Check one judge reply and write its score files. Return their paths.

    `root` is used only when the key does not record its own root (the first
    keys did not).
    """
    if not judge_label.strip():
        raise Refused("--judge must not be empty")
    blind_dir = blind_dir.expanduser().resolve()
    key_path = (key_path or default_key(root.expanduser(), blind_dir)).expanduser()
    if not key_path.is_file():
        raise Refused(
            f"no key at {key_path}; pass --key (a first-layout blind dir keeps "
            f"it at {blind_dir / OLD_KEY})"
        )
    key = read_object(key_path)
    root = pathlib.Path(str(key.get("root") or root)).expanduser().resolve()
    if scores_root is None:
        scores_root = root / "scores"
    scores_root = scores_root.expanduser().resolve()
    if not scores_root.is_relative_to(root) and not force_scores_root:
        raise Refused(
            f"{scores_root} is outside the blind run's root {root}; "
            "pass --force-scores-root if that is what you want"
        )
    refuse_inside_repo(scores_root)
    runs: Mapping[str, Mapping[str, Any]] = key["runs"]
    checklists: Mapping[str, list[str]] = key["checklists"]
    labels = defect_labels(key["defects"])
    answers = read_answers(answers_file)

    errors: list[str] = []
    for run_id in answers:
        if run_id not in runs:
            errors.append(f"{run_id}: not an id in {key_path}")
    # One reply covers whole clips: a judge that skips a clip, or a reply cut
    # short, must not leave some runs of a clip scored and others not.
    clips = {runs[i]["clip"] for i in answers if i in runs}
    for run_id, run in sorted(runs.items()):
        if run["clip"] in clips and run_id not in answers:
            errors.append(f"no answer for {run_id} (clip {run['clip']})")

    stamp = now_iso()
    scores: dict[pathlib.Path, dict[str, Any]] = {}
    for run_id, entry in answers.items():
        if run_id not in runs:
            continue
        run = runs[run_id]
        clip = str(run["clip"])
        found, canon = check_entry(run_id, entry, checklists[clip], labels)
        errors.extend(found)
        arm, seed = str(run["arm"]), int(run["seed"])
        if not (SAFE.match(arm) and SAFE.match(clip)):
            errors.append(f"{run_id}: the key names an unsafe arm or clip")
            continue
        # The run may have been remade since the judge saw its frames.
        clip_file = clip_path_of(run, root)
        if not clip_file.is_file():
            errors.append(f"{run_id}: the clip {clip_file} is gone")
        elif sha256(clip_file) != run["clip_sha256"]:
            errors.append(f"{run_id}: {clip_file} changed since the blind run")
        if run.get("blind_sha256"):
            # An image: the judge saw the normalized copy, so check it too.
            shown = blind_dir / clip / run_id / IMAGE
            if not shown.is_file():
                errors.append(f"{run_id}: the blind copy {shown} is gone")
            elif sha256(shown) != run["blind_sha256"]:
                errors.append(f"{run_id}: {shown} changed since the blind run")
        path = scores_root / arm / f"{clip}-s{seed}.json"
        if path.exists() and not overwrite:
            errors.append(f"{path} exists; pass --overwrite to replace it")
        if found:
            continue
        items = sorted(entry["items"], key=lambda item: item["n"])
        score: dict[str, Any] = {
            "schema": 1,
            "adherence": {
                "passed": sum(1 for item in items if item["true"]),
                "total": len(checklists[clip]),
            },
            "defects": len(canon),
            "judge": judge_label,
            "notes": entry.get("notes", ""),
            "items": items,
            "defect_list": canon,
            "blind_id": run_id,
            "blind_dir": str(blind_dir),
            "prompts": key.get("prompts"),
            "clip_sha256": run["clip_sha256"],
            "ingested": stamp,
        }
        try:
            parse_score(score)  # the gallery's own check, before any write
        except (ValueError, TypeError) as err:
            errors.append(f"{run_id}: the score is not valid: {err}")
        scores[path] = score
    if errors:
        raise Refused("refused, nothing written:\n  " + "\n  ".join(errors))

    written: list[pathlib.Path] = []
    for path, score in sorted(scores.items()):
        write_atomic(path, json.dumps(score, indent=2, sort_keys=True) + "\n")
        parse_score(json.loads(path.read_text()))  # read it back as the gallery does
        written.append(path)
    summarize(runs, answers, checklists)
    return written


def summarize(
    runs: Mapping[str, Mapping[str, Any]],
    answers: Mapping[str, Any],
    checklists: Mapping[str, list[str]],
) -> None:
    """Log mean adherence and total defects per arm, for this reply."""
    per_arm: dict[str, list[tuple[float, int]]] = {}
    for run_id, entry in answers.items():
        run = runs[run_id]
        passed = sum(1 for item in entry["items"] if item["true"])
        total = len(checklists[run["clip"]])
        per_arm.setdefault(str(run["arm"]), []).append(
            (passed / total, len(entry["defects"]))
        )
    for arm, rows in sorted(per_arm.items()):
        mean = 100 * sum(r[0] for r in rows) / len(rows)
        logger.info(
            "%s: %d runs, adherence %.1f%%, %d defects",
            arm,
            len(rows),
            mean,
            sum(r[1] for r in rows),
        )


# -------------------------------------------------------------------- cli ----


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("blind", help="copy the frames into blinded folders")
    b.add_argument("--arms", nargs="+", required=True)
    b.add_argument(
        "--clips",
        nargs="+",
        help="clips or image prompts (default: every one the arms file lists)",
    )
    b.add_argument("--out", type=pathlib.Path, required=True)
    b.add_argument(
        "--root",
        type=pathlib.Path,
        default=DEFAULT_ROOT,
        help="the video-gen root that holds out/, frames-out/ and judge/keys/",
    )
    b.add_argument(
        "--key",
        type=pathlib.Path,
        help="where to write the key (default: ROOT/judge/keys/<OUT name>.json)",
    )
    b.add_argument(
        "--prompts",
        type=pathlib.Path,
        help="default: the prompts file of the arms file (v1.json for video)",
    )
    b.add_argument(
        "--arms-file",
        type=pathlib.Path,
        help="default: the one arms file that defines every --arms",
    )
    b.add_argument(
        "--inputs-root",
        type=pathlib.Path,
        help="an edit prompt's input images (default: ROOT/ref/imagegen)",
    )
    b.add_argument(
        "--allow-missing",
        action="store_true",
        help="blind the runs that exist when some expected runs are missing",
    )
    i = sub.add_parser("ingest", help="check a judge reply and write the scores")
    i.add_argument("--blind", type=pathlib.Path, required=True)
    i.add_argument("--answers", type=pathlib.Path, required=True)
    i.add_argument("--judge", required=True, help="the judge label in each score")
    i.add_argument(
        "--key",
        type=pathlib.Path,
        help="the key (default: ROOT/judge/keys/<BLIND name>.json)",
    )
    i.add_argument(
        "--root",
        type=pathlib.Path,
        default=DEFAULT_ROOT,
        help="the video-gen root, for a key that does not record one",
    )
    i.add_argument(
        "--scores-root",
        type=pathlib.Path,
        help="default: ROOT/scores; outside ROOT needs --force-scores-root",
    )
    i.add_argument("--force-scores-root", action="store_true")
    i.add_argument("--overwrite", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "blind":
            blind(
                args.arms,
                clips=args.clips,
                out=args.out,
                root=args.root,
                key_path=args.key,
                prompts_file=args.prompts,
                arms_file=args.arms_file,
                inputs_root=args.inputs_root,
                allow_missing=args.allow_missing,
            )
        else:
            ingest(
                args.blind,
                args.answers.expanduser(),
                judge_label=args.judge,
                key_path=args.key,
                root=args.root,
                scores_root=args.scores_root,
                force_scores_root=args.force_scores_root,
                overwrite=args.overwrite,
            )
    except (Refused, InsideRepo) as err:
        logger.error("%s", err)
        return REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
