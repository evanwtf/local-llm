"""Blind the scored clips for the judge, and ingest its scores (#1406).

    judge.py blind --arms h3-t2v wan-t2v [--clips dog-a ...] --out DIR --seed N
    judge.py ingest --blind DIR --answers FILE.json --judge LABEL [--overwrite]

A Claude subagent judges every scored clip from its extracted frames, with a
fixed checklist and a fixed defect list (the method is in #1402). The judge
must not know which model made a clip, so `blind` hides it:

- Each run gets a random 6-character id from a seeded RNG, so the same
  `--seed` gives the same ids again.
- Only `s*.jpg` and `strip.jpg` go to `DIR/<clip>/<id>/`. `first.png` and
  `meta.json` stay behind, and no path or file the judge reads names the arm,
  the model or the seed. `blind` refuses an `--out` path that names an arm.
- `DIR/<clip>/PROMPT.md` is the judge prompt for one clip: its folders in a
  shuffled order, its checklist, and the fixed defect list.
- `DIR/key.json` maps each id back to its run. The judge must not read it.

Every copied file must match the sha256 that `frames.py` recorded in
`meta.json`, and the run's `clip.mp4` must match `meta.json`'s
`clip_sha256`. Stale frames would score one clip under another clip's name.

`ingest` reads one judge reply, checks all of it, and only then writes
`scores/<arm>/<clip>-s<seed>.json` in the schema `gallery.py` reads. Any
error refuses the whole file and writes nothing: a wrong id or item would
put a score on the wrong run with no error anywhere.

The blind directory and the scores live outside every git work tree: the
frames are private and this repository is public.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import random
import re
import shutil
import string
import sys
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen.common import DEFAULT_ROOT, InsideRepo, refuse_inside_repo, sha256
from videogen.gallery import parse_score

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_FILE = REPO / "benchmarks" / "videogen" / "prompts" / "v1.json"
DEFAULT_CLIPS = ("dog-a", "dog-b", "dog-c")
REFUSED = 2
KEY = "key.json"
PROMPT = "PROMPT.md"
ID_CHARS = string.ascii_lowercase + string.digits
ID_LEN = 6
ID_RE = re.compile(rf"^[a-z0-9]{{{ID_LEN}}}$")
#: One path component: no dot, no slash. Arm and clip names become paths.
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
STILL = re.compile(r"^s\d+\.jpg$")
STRIP = "strip.jpg"

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


class Refused(Exception):
    """The input is wrong, so the command writes nothing."""


@dataclasses.dataclass(frozen=True)
class Run:
    arm: str
    clip: str
    seed: int
    frames: pathlib.Path
    clip_sha256: str
    files: tuple[str, ...]  # the stills and the strip, in name order


def now_iso() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def read_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as err:
        raise Refused(f"{path} is not readable JSON: {err}") from err


def load_prompts(path: pathlib.Path) -> tuple[str, dict[str, list[str]], list[str]]:
    """(version, run-dir clip id -> checklist, fixed defect list).

    The run directories name a clip by its reference stem (`dog-a`), and the
    prompt file names it `dog-a-v1`; the `ref` field links the two.
    """
    data = read_json(path)
    checklists: dict[str, list[str]] = {}
    for item in data.get("clips") or []:
        clip = pathlib.PurePath(str(item["ref"])).stem
        checklists[clip] = [str(line) for line in item["checklist"]]
    defects = [str(d) for d in data.get("defects") or []]
    if not checklists or not defects:
        raise Refused(f"{path} holds no checklist or no defect list")
    return str(data.get("version")), checklists, defects


# ------------------------------------------------------------------ blind ----


def refuse_out(out: pathlib.Path, arms: Sequence[str]) -> None:
    """Refuse an --out inside git, not empty, or with a path that names an arm."""
    refuse_inside_repo(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refused(f"{out} is not empty; give a new directory")
    # The judge sees the absolute path of every folder, so the path must not
    # name the model. Check each component's tokens: "h3-vs-wan" names both.
    names = {a.lower() for a in arms} | {a.split("-")[0].lower() for a in arms}
    for part in out.expanduser().resolve().parts:
        tokens = set(re.split(r"[-_.]", part.lower()))
        hit = (names & tokens) or {a for a in names if a in part.lower() and "-" in a}
        if hit:
            raise Refused(
                f"{out} names {sorted(hit)}; the judge reads this path, "
                "so it must not name an arm or a model"
            )


def find_runs(root: pathlib.Path, arm: str, clip: str) -> list[Run]:
    """Every scored run of `arm` for `clip` that has frames, checked by sha256."""
    pattern = re.compile(rf"^{re.escape(clip)}-s(\d+)$")
    runs: list[Run] = []
    arm_frames = root / "frames-out" / arm
    if not arm_frames.is_dir():
        return runs
    for frames in sorted(arm_frames.iterdir()):
        match = pattern.match(frames.name)
        if not match or not frames.is_dir():
            continue
        meta_path = frames / "meta.json"
        if not meta_path.is_file():
            logger.warning("skipped %s: no meta.json", frames)
            continue
        meta = read_json(meta_path)
        clip_file = root / "out" / arm / frames.name / "clip.mp4"
        if not clip_file.is_file():
            logger.warning("skipped %s: no scored clip at %s", frames, clip_file)
            continue
        if sha256(clip_file) != meta.get("clip_sha256"):
            raise Refused(
                f"{frames}: meta.json clip_sha256 does not match {clip_file}; "
                "rerun frames.py on that clip"
            )
        files = sorted(p.name for p in frames.iterdir() if STILL.match(p.name))
        if not files or not (frames / STRIP).is_file():
            raise Refused(f"{frames}: no stills or no {STRIP}")
        files.append(STRIP)
        hashes = meta.get("outputs_sha256") or {}
        for name in files:
            if sha256(frames / name) != hashes.get(name):
                raise Refused(f"{frames / name} does not match its meta.json sha256")
        runs.append(
            Run(
                arm=arm,
                clip=clip,
                seed=int(match.group(1)),
                frames=frames,
                clip_sha256=str(meta["clip_sha256"]),
                files=tuple(files),
            )
        )
    return runs


def new_id(rng: random.Random, taken: set[str]) -> str:
    while True:
        run_id = "".join(rng.choice(ID_CHARS) for _ in range(ID_LEN))
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
        defects="\n".join(f"- {d}" for d in defects),
    )


def blind(
    arms: Sequence[str],
    *,
    clips: Sequence[str],
    out: pathlib.Path,
    seed: int,
    root: pathlib.Path = DEFAULT_ROOT,
    prompts_file: pathlib.Path = PROMPTS_FILE,
) -> dict[str, list[str]]:
    """Write the blind directory. Return clip -> its ids, in prompt order."""
    for name in (*arms, *clips):
        if not SAFE.match(name):
            raise Refused(f"{name!r} is not a safe arm or clip name")
    out = out.expanduser().resolve()
    refuse_out(out, arms)
    version, checklists, defects = load_prompts(prompts_file)
    for clip in clips:
        if clip not in checklists:
            raise Refused(f"{clip} has no checklist in {prompts_file}")

    runs: dict[str, list[Run]] = {clip: [] for clip in clips}
    for arm in arms:
        found = 0
        for clip in clips:
            arm_runs = find_runs(root, arm, clip)
            runs[clip].extend(arm_runs)
            found += len(arm_runs)
            logger.info("%s %s: %d scored runs with frames", arm, clip, len(arm_runs))
        if found == 0:
            raise Refused(f"{arm} has no scored run with frames under {root}")

    rng = random.Random(seed)
    taken: set[str] = set()
    key: dict[str, Any] = {
        "schema": 1,
        "made": now_iso(),
        "seed": seed,
        "prompts": version,
        "arms": list(arms),
        "checklists": {clip: checklists[clip] for clip in clips},
        "defects": defects,
        "runs": {},
    }
    order: dict[str, list[str]] = {}
    # Build in a sibling temp directory, then rename: a failure leaves no
    # half-made blind directory that a judge could be pointed at.
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        for clip in clips:
            ids: list[str] = []
            for run in runs[clip]:
                run_id = new_id(rng, taken)
                ids.append(run_id)
                folder = stage / clip / run_id
                folder.mkdir(parents=True)
                for name in run.files:
                    # copyfile, not copy2: no mode or mtime from the source.
                    shutil.copyfile(run.frames / name, folder / name)
                key["runs"][run_id] = {
                    "arm": run.arm,
                    "clip": run.clip,
                    "seed": run.seed,
                    "run_dir": str(run.frames),
                    "clip_sha256": run.clip_sha256,
                }
            if not ids:
                logger.warning("%s: no runs, so no prompt", clip)
                continue
            rng.shuffle(ids)
            order[clip] = ids
            text = render_prompt(
                [(i, out / clip / i) for i in ids], checklists[clip], defects
            )
            (stage / clip / PROMPT).write_text(text)
        key["order"] = order
        (stage / KEY).write_text(json.dumps(key, indent=2, sort_keys=True) + "\n")
        if out.exists():
            out.rmdir()  # empty, checked above
        stage.rename(out)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    for clip, ids in order.items():
        logger.info("%s: %d clips, prompt %s", clip, len(ids), out / clip / PROMPT)
    return order


# ----------------------------------------------------------------- ingest ----


def check_entry(
    run_id: str, entry: object, checklist: Sequence[str], defects: Sequence[str]
) -> list[str]:
    """Every problem with one id's answer, as text. Empty means good."""
    errors: list[str] = []
    if not isinstance(entry, dict):
        return [f"{run_id}: the answer must be an object"]
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
        if defect.get("type") not in defects:
            errors.append(
                f"{run_id}: defect type {defect.get('type')!r} is not in the fixed list"
            )
        for field in ("frame", "note"):
            if not isinstance(defect.get(field), str):
                errors.append(f"{run_id}: defect {field} must be text")
    return errors


def write_atomic(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def ingest(
    blind_dir: pathlib.Path,
    answers_file: pathlib.Path,
    *,
    judge_label: str,
    scores_root: pathlib.Path = DEFAULT_ROOT / "scores",
    overwrite: bool = False,
) -> list[pathlib.Path]:
    """Check one judge reply and write its score files. Return their paths."""
    scores_root = scores_root.expanduser().resolve()
    refuse_inside_repo(scores_root)
    if not judge_label.strip():
        raise Refused("--judge must not be empty")
    key = read_json(blind_dir / KEY)
    runs: Mapping[str, Mapping[str, Any]] = key["runs"]
    checklists: Mapping[str, list[str]] = key["checklists"]
    defects: list[str] = key["defects"]
    answers = read_json(answers_file)
    if not isinstance(answers, dict) or not answers:
        raise Refused(f"{answers_file}: the reply must be a non-empty JSON object")

    errors: list[str] = []
    for run_id in answers:
        if run_id not in runs:
            errors.append(f"{run_id}: not an id in {blind_dir / KEY}")
    # One reply covers whole clips: a judge that skips a clip, or a reply cut
    # short, must not leave some runs of a clip scored and others not.
    clips = {runs[i]["clip"] for i in answers if i in runs}
    for run_id, run in sorted(runs.items()):
        if run["clip"] in clips and run_id not in answers:
            errors.append(f"no answer for {run_id} (clip {run['clip']})")
    for run_id, entry in answers.items():
        if run_id in runs:
            clip = runs[run_id]["clip"]
            errors.extend(check_entry(run_id, entry, checklists[clip], defects))

    targets: dict[str, pathlib.Path] = {}
    for run_id in answers:
        if run_id not in runs:
            continue
        run = runs[run_id]
        arm, clip, seed = str(run["arm"]), str(run["clip"]), int(run["seed"])
        if not (SAFE.match(arm) and SAFE.match(clip)):
            errors.append(f"{run_id}: key.json names an unsafe arm or clip")
            continue
        path = scores_root / arm / f"{clip}-s{seed}.json"
        targets[run_id] = path
        if path.exists() and not overwrite:
            errors.append(f"{path} exists; pass --overwrite to replace it")
    if errors:
        raise Refused("refused, nothing written:\n  " + "\n  ".join(errors))

    stamp = now_iso()
    written: list[pathlib.Path] = []
    for run_id, path in sorted(targets.items()):
        entry = answers[run_id]
        items = sorted(entry["items"], key=lambda item: item["n"])
        score: dict[str, Any] = {
            "schema": 1,
            "adherence": {
                "passed": sum(1 for item in items if item["true"]),
                "total": len(checklists[runs[run_id]["clip"]]),
            },
            "defects": len(entry["defects"]),
            "judge": judge_label,
            "notes": entry.get("notes", ""),
            "items": items,
            "defect_list": entry["defects"],
            "blind_id": run_id,
            "blind_dir": str(blind_dir.resolve()),
            "prompts": key.get("prompts"),
            "clip_sha256": runs[run_id]["clip_sha256"],
            "ingested": stamp,
        }
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
    b.add_argument("--clips", nargs="+", default=list(DEFAULT_CLIPS))
    b.add_argument("--out", type=pathlib.Path, required=True)
    b.add_argument("--seed", type=int, required=True)
    b.add_argument(
        "--root",
        type=pathlib.Path,
        default=DEFAULT_ROOT,
        help="the video-gen root that holds out/ and frames-out/",
    )
    b.add_argument("--prompts", type=pathlib.Path, default=PROMPTS_FILE)
    i = sub.add_parser("ingest", help="check a judge reply and write the scores")
    i.add_argument("--blind", type=pathlib.Path, required=True)
    i.add_argument("--answers", type=pathlib.Path, required=True)
    i.add_argument("--judge", required=True, help="the judge label in each score")
    i.add_argument("--scores-root", type=pathlib.Path, default=DEFAULT_ROOT / "scores")
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
                seed=args.seed,
                root=args.root.expanduser(),
                prompts_file=args.prompts,
            )
        else:
            ingest(
                args.blind.expanduser(),
                args.answers.expanduser(),
                judge_label=args.judge,
                scores_root=args.scores_root,
                overwrite=args.overwrite,
            )
    except (Refused, InsideRepo) as err:
        logger.error("%s", err)
        return REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
