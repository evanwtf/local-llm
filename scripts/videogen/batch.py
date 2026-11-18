"""Make every clip x seed of one video-gen arm, then extract their frames (#1406, #1407).

    batch.py --arm h3-t2v --server http://HOST:8040

The order is fixed: the clips, then the seeds, as `arms-v1.json` lists them
(dog-a s1, s2, s3, dog-b s1, ...). Each output goes to
`~/bench-logs/videogen/out/<arm>/<clip>-s<seed>/` through `generate.run_one`.

- A directory is skipped when its run is good: `run.json` says ok and not a
  probe, `clip.mp4` still has the recorded sha256, the arms file was locked
  (or `--allow-draft`), and the outlet energy is complete.
- A good-looking run that records another request or another server block
  (the arms file or the first frame changed) stops the batch: replacing a
  scored clip by accident is worse than stopping.
- Any other run is made again, once. The first failure stops the batch.
  There is no retry loop: a failure is a finding, so read run.json.

Then `frames.py` runs on each good clip into
`~/bench-logs/videogen/frames-out/<arm>/<clip>-s<seed>/`. A frames directory
whose `meta.json` already names the clip's sha256 is skipped.

After each clip it makes (good or failed), the batch rebuilds the gallery
(`gallery.py`, #1413) in `<out-root>/../gallery/`. The rebuild is
best-effort: a failure logs a warning and never stops the batch, because a
web page is not worth a lost 40-minute clip. `--no-gallery` turns it off.

**Image arms (#1424).** An arm in `image-arms-v1.json` or
`edit-arms-v1.json` runs the same way: its prompts, then the seeds, each to
`out/<arm>/<prompt>-s<seed>/image.png`. There are no frames to extract.
One image takes a few seconds, too short for the outlet, so the runs a
batch makes are one block (`imagegen.Block`):

- Before the first run, one discarded warm-up request (its wall time is
  logged and kept in block.json, never scored), then one GPU settle.
- The runs go back to back: no settle, no outlet wait, and no gallery or
  metrics rebuild between them.
- After the last run (or the first failure), one outlet read over the whole
  block fills each run's `outlet` and writes `out/<arm>/block.json` with
  the block's Wh, image count, Wh per image, readings and duration. An
  earlier block.json is kept as `block-<start>.json`.

The warm-up is once per batch run; run one batch per server start. An image
run is good when `image.png` still has the recorded sha256, the rest above
holds (except the per-run energy, which a short run does not have), and its
block was read.

After the gallery, it writes the Prometheus metrics (`metrics.py`) into
node_exporter's textfile directory, the same best-effort way: a missing or
unwritable directory logs one warning, and the batch goes on. `--no-metrics`
turns it off.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen import gallery, generate, imagegen, metrics
from videogen.common import (
    DEFAULT_ROOT,
    IMAGE_INPUTS_ROOT,
    InsideRepo,
    refuse_inside_repo,
    sha256,
)

import logs

logger = logging.getLogger(__name__)

FRAMES_PY = pathlib.Path(__file__).resolve().parent / "frames.py"

#: (clip, out dir, overwrite) -> exit status of frames.py.
FramesRunner = Callable[[pathlib.Path, pathlib.Path, bool], int]
#: (plan, out dir) -> exit status of one generate run.
#: The plan is a generate.Plan or an imagegen.ImagePlan (generate.AnyPlan).
#: Typed Any so a fake that takes only one of the two still fits.
Generator = Callable[[Any, pathlib.Path], int]
#: (video-gen root) -> anything; rebuilds the gallery for that root.
GalleryBuilder = Callable[[pathlib.Path], object]
#: (video-gen root) -> anything; writes the Prometheus textfile for that root.
MetricsWriter = Callable[[pathlib.Path], object]
#: (server, node, metrics host) -> the block of an image arm's runs.
BlockMaker = Callable[[str, str | None, str | None], imagegen.Block]

GOOD, TODO, MISMATCH = "good", "todo", "mismatch"


def run_frames(clip: pathlib.Path, out: pathlib.Path, overwrite: bool) -> int:
    """frames.py in its own process, as an operator would run it."""
    argv = [sys.executable, str(FRAMES_PY), str(clip), str(out)]
    if overwrite:
        argv.append("--overwrite")
    return subprocess.run(argv, check=False).returncode


def build_gallery(root: pathlib.Path) -> object:
    """The gallery for `root`, in `root/gallery`."""
    return gallery.build(root, root / "gallery")


def refresh_gallery(builder: GalleryBuilder | None, root: pathlib.Path) -> None:
    """Run `builder` and log, never raise: the gallery must not stop a batch."""
    if builder is None:
        return
    try:
        builder(root)
    except Exception as err:  # noqa: BLE001 -- any failure; a page must not cost a clip
        logger.warning("gallery rebuild failed (the batch goes on): %r", err)


def write_metrics(root: pathlib.Path) -> object:
    """`videogen.prom` for `root`, in node_exporter's textfile directory."""
    return metrics.write(root, metrics.DEFAULT_TEXTFILE_DIR)


def refresh_metrics(
    writer: MetricsWriter | None, root: pathlib.Path, *, quiet: bool = False
) -> bool:
    """Run `writer` and log, never raise: metrics must not stop a batch.

    Returns False on a failure. `quiet` logs a repeat failure at DEBUG, so a
    missing directory gives one warning per batch, not one per clip.
    """
    if writer is None:
        return True
    try:
        writer(root)
    except Exception as err:  # noqa: BLE001 -- any failure; a metric must not cost a clip
        logger.log(
            logging.DEBUG if quiet else logging.WARNING,
            "metrics export failed (the batch goes on): %r",
            err,
        )
        return False
    return True


def read_run(out: pathlib.Path) -> dict[str, Any] | None:
    try:
        data = json.loads((out / "run.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def state(
    out: pathlib.Path, plan: generate.AnyPlan, *, allow_draft: bool = False
) -> tuple[str, str]:
    """(GOOD, TODO or MISMATCH, and why) for the run in `out`."""
    run = read_run(out)
    if run is None:
        return TODO, "no run.json"
    if run.get("status") != "ok":
        return TODO, f"the last run failed: {run.get('error')}"
    if run.get("probe"):
        return TODO, "a probe, not a scored run"
    clip = out / plan.output_name
    want = (run.get("output") or {}).get("sha256")
    if not clip.is_file() or sha256(clip) != want:
        return TODO, (
            f"{plan.output_name} is missing or does not match its recorded sha256"
        )
    if run.get("request") != plan.request():
        return MISMATCH, "it records a different request"
    if run.get("server_config") != plan.server_config():
        return MISMATCH, "it records a different server block"
    if run.get("arms_status") != "locked" and not allow_draft:
        return TODO, "it was made from a draft arms file"
    if run.get("media") == "image":
        # An image's energy is its block's (block.json); its own window is
        # too short to have one. A run whose block was cut off before the
        # outlet read is made again.
        if (run.get("outlet") or {}).get("deferred"):
            return TODO, "its block stopped before the outlet was read"
        return GOOD, "done"
    if run.get("energy_wh") is None:
        outlet = run.get("outlet") or {}
        return TODO, (
            f"its outlet energy is incomplete (max gap {outlet.get('max_gap_s')} s, "
            f"{outlet.get('error') or 'the readings do not cover the window'})"
        )
    return GOOD, "done"


def frames_done(clip: pathlib.Path, frames_out: pathlib.Path) -> bool | None:
    """True if frames_out holds frames of this clip; False if of another clip;
    None if it holds none."""
    try:
        meta = json.loads((frames_out / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    return bool(meta.get("clip_sha256") == sha256(clip))


def run_batch(
    arm: str,
    *,
    server: str,
    out_root: pathlib.Path,
    frames_out_root: pathlib.Path,
    frames_root: pathlib.Path,
    fallback: bool = False,
    allow_draft: bool = False,
    node: str | None = None,
    metrics_host: str | None = None,
    arms_file: pathlib.Path | None = None,
    inputs_root: pathlib.Path = IMAGE_INPUTS_ROOT,
    generator: Generator | None = None,
    frames_runner: FramesRunner = run_frames,
    gallery_builder: GalleryBuilder | None = build_gallery,
    metrics_writer: MetricsWriter | None = None,
    block_maker: BlockMaker | None = None,
) -> int:
    """Make the arm's clips in order, then their frames. Returns 0 or the
    first nonzero status.

    `metrics_writer` is None by default, unlike the gallery, so a caller such
    as a test never writes the system textfile directory; `main` passes
    `write_metrics` unless `--no-metrics`.
    """
    for root in (out_root, frames_out_root):
        try:
            refuse_inside_repo(root)
        except InsideRepo as err:
            logger.error("%s", err)
            return generate.REFUSED
    if arms_file is None:
        try:
            arms_file = generate.find_arms_file(arm)
        except generate.Refused as err:
            logger.error("%s", err)
            return generate.REFUSED
    arms = generate.load_json(arms_file)
    is_video = generate.media_of(arms) == "video"

    # Inside an image block: no settle and no outlet wait per run.
    in_block = {} if is_video else {"settle": False, "read_outlet": False}

    def default_generator(plan: generate.AnyPlan, out: pathlib.Path) -> int:
        # overwrite=True: state() already said this directory is not a good
        # run, so whatever is in it is a failed, partial or stale one.
        return generate.run_plan(
            plan,
            server,
            out,
            node=node,
            metrics_host=metrics_host,
            allow_draft=allow_draft,
            overwrite=True,
            **in_block,
        )

    make = generator or default_generator
    block = (
        None
        if is_video
        else (block_maker or imagegen.Block)(server, node, metrics_host)
    )
    status = 0
    # After the first failed export, repeats log at DEBUG (one warning a batch).
    metrics_failed = False

    def refresh() -> None:
        nonlocal metrics_failed
        # out_root is <root>/out, so its parent holds probe/, ref/, scores/.
        refresh_gallery(gallery_builder, out_root.parent)
        if not refresh_metrics(metrics_writer, out_root.parent, quiet=metrics_failed):
            metrics_failed = True

    good: list[tuple[str, int, pathlib.Path]] = []
    for clip in arms["clips"]:
        for seed in arms["seeds"]:
            out = out_root / arm / f"{clip}-s{seed}"
            try:
                plan = generate.plan_for(
                    arm,
                    clip,
                    int(seed),
                    frames_root=frames_root,
                    fallback=fallback,
                    arms_file=arms_file,
                    inputs_root=inputs_root,
                )
            except (generate.Refused, ValueError, TypeError, KeyError) as err:
                logger.error("%s", err)
                status = generate.REFUSED
                break
            found, why = state(out, plan, allow_draft=allow_draft)
            if found == GOOD:
                logger.info("%s %s seed %d: done already, skipped", arm, clip, seed)
                good.append((clip, int(seed), out))
                continue
            if found == MISMATCH:
                logger.error(
                    "%s holds a good run, but %s; move it away or restore the "
                    "arms file it was made with",
                    out,
                    why,
                )
                status = generate.REFUSED
                break
            logger.info("%s %s seed %d: generating (%s)", arm, clip, seed, why)
            if block is not None and isinstance(plan, imagegen.ImagePlan):
                block.start(plan)
            rc = make(plan, out)
            if block is None:
                refresh()
            else:
                block.add(out)
            if rc != 0:
                logger.error("%s %s seed %d failed (%d); stopping", arm, clip, seed, rc)
                status = rc
                break
            good.append((clip, int(seed), out))
        if status:
            break

    if block is not None and block.runs:
        block.close(out_root / arm, arm)
        refresh()
    elif (
        block is not None
        and good
        and not (out_root / arm / imagegen.BLOCK_FILE).exists()
    ):
        logger.warning(
            "%s: every run is done but there is no %s; move out/%s away and "
            "run again to measure its energy",
            arm,
            imagegen.BLOCK_FILE,
            arm,
        )

    # An image has no frames to extract.
    for clip, seed, out in good if is_video else []:
        frames_out = frames_out_root / arm / f"{clip}-s{seed}"
        done = frames_done(out / "clip.mp4", frames_out)
        if done:
            continue
        rc = frames_runner(out / "clip.mp4", frames_out, done is False)
        if rc != 0:
            logger.error("frames.py failed (%d) on %s; stopping", rc, out)
            return status or rc
    logger.info("%s: %d run(s) made or kept", arm, len(good))
    return status


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--arm",
        required=True,
        help="an arm in arms-v1.json, image-arms-v1.json or edit-arms-v1.json",
    )
    p.add_argument("--server", required=True, help="http://HOST:PORT of vLLM-Omni")
    p.add_argument("--out-root", type=pathlib.Path, default=DEFAULT_ROOT / "out")
    p.add_argument(
        "--frames-out-root", type=pathlib.Path, default=DEFAULT_ROOT / "frames-out"
    )
    p.add_argument(
        "--frames-root",
        type=pathlib.Path,
        default=DEFAULT_ROOT / "frames",
        help="where frames.py wrote each reference clip's first.png",
    )
    p.add_argument(
        "--inputs-root",
        type=pathlib.Path,
        default=IMAGE_INPUTS_ROOT,
        help="where an edit arm's input images are",
    )
    p.add_argument("--fallback", action="store_true", help="use the fallback size")
    p.add_argument("--node", choices=["head", "worker"], help="override the arm's node")
    p.add_argument(
        "--metrics-host",
        help="host of the node's exporters (default: the --server host)",
    )
    p.add_argument(
        "--allow-draft",
        action="store_true",
        help="allow scored runs from a draft arms file, and count them as done",
    )
    p.add_argument(
        "--no-gallery",
        action="store_true",
        help="do not rebuild the gallery after each clip",
    )
    p.add_argument(
        "--no-metrics",
        action="store_true",
        help="do not write the Prometheus textfile after each clip",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    return run_batch(
        args.arm,
        server=args.server,
        out_root=args.out_root.expanduser().resolve(),
        frames_out_root=args.frames_out_root.expanduser().resolve(),
        frames_root=args.frames_root.expanduser(),
        inputs_root=args.inputs_root.expanduser(),
        fallback=args.fallback,
        allow_draft=args.allow_draft,
        node=args.node,
        metrics_host=args.metrics_host,
        gallery_builder=None if args.no_gallery else build_gallery,
        metrics_writer=None if args.no_metrics else write_metrics,
    )


if __name__ == "__main__":
    sys.exit(main())
