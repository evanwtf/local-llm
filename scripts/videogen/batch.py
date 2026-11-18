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

from videogen import generate
from videogen.common import DEFAULT_ROOT, InsideRepo, refuse_inside_repo, sha256

import logs

logger = logging.getLogger(__name__)

FRAMES_PY = pathlib.Path(__file__).resolve().parent / "frames.py"

#: (clip, out dir, overwrite) -> exit status of frames.py.
FramesRunner = Callable[[pathlib.Path, pathlib.Path, bool], int]
#: (plan, out dir) -> exit status of one generate run.
Generator = Callable[[generate.Plan, pathlib.Path], int]

GOOD, TODO, MISMATCH = "good", "todo", "mismatch"


def run_frames(clip: pathlib.Path, out: pathlib.Path, overwrite: bool) -> int:
    """frames.py in its own process, as an operator would run it."""
    argv = [sys.executable, str(FRAMES_PY), str(clip), str(out)]
    if overwrite:
        argv.append("--overwrite")
    return subprocess.run(argv, check=False).returncode


def read_run(out: pathlib.Path) -> dict[str, Any] | None:
    try:
        data = json.loads((out / "run.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def state(
    out: pathlib.Path, plan: generate.Plan, *, allow_draft: bool = False
) -> tuple[str, str]:
    """(GOOD, TODO or MISMATCH, and why) for the run in `out`."""
    run = read_run(out)
    if run is None:
        return TODO, "no run.json"
    if run.get("status") != "ok":
        return TODO, f"the last run failed: {run.get('error')}"
    if run.get("probe"):
        return TODO, "a probe, not a scored run"
    clip = out / "clip.mp4"
    want = (run.get("output") or {}).get("sha256")
    if not clip.is_file() or sha256(clip) != want:
        return TODO, "clip.mp4 is missing or does not match its recorded sha256"
    if run.get("request") != plan.request():
        return MISMATCH, "it records a different request"
    if run.get("server_config") != plan.server_config():
        return MISMATCH, "it records a different server block"
    if run.get("arms_status") != "locked" and not allow_draft:
        return TODO, "it was made from a draft arms file"
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
    arms_file: pathlib.Path = generate.ARMS_FILE,
    generator: Generator | None = None,
    frames_runner: FramesRunner = run_frames,
) -> int:
    """Make the arm's clips in order, then their frames. Returns 0 or the
    first nonzero status."""
    for root in (out_root, frames_out_root):
        try:
            refuse_inside_repo(root)
        except InsideRepo as err:
            logger.error("%s", err)
            return generate.REFUSED
    arms = generate.load_json(arms_file)

    def default_generator(plan: generate.Plan, out: pathlib.Path) -> int:
        # overwrite=True: state() already said this directory is not a good
        # run, so whatever is in it is a failed, partial or stale one.
        return generate.run_one(
            plan,
            server,
            out,
            node=node,
            metrics_host=metrics_host,
            allow_draft=allow_draft,
            overwrite=True,
        )

    make = generator or default_generator
    status = 0
    good: list[tuple[str, int, pathlib.Path]] = []
    for clip in arms["clips"]:
        for seed in arms["seeds"]:
            out = out_root / arm / f"{clip}-s{seed}"
            try:
                plan = generate.make_plan(
                    arm,
                    clip,
                    int(seed),
                    frames_root=frames_root,
                    fallback=fallback,
                    arms_file=arms_file,
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
            rc = make(plan, out)
            if rc != 0:
                logger.error("%s %s seed %d failed (%d); stopping", arm, clip, seed, rc)
                status = rc
                break
            good.append((clip, int(seed), out))
        if status:
            break

    for clip, seed, out in good:
        frames_out = frames_out_root / arm / f"{clip}-s{seed}"
        done = frames_done(out / "clip.mp4", frames_out)
        if done:
            continue
        rc = frames_runner(out / "clip.mp4", frames_out, done is False)
        if rc != 0:
            logger.error("frames.py failed (%d) on %s; stopping", rc, out)
            return status or rc
    logger.info("%s: %d clip(s) made or kept", arm, len(good))
    return status


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--arm", required=True, help="an arm in arms-v1.json")
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
        fallback=args.fallback,
        allow_draft=args.allow_draft,
        node=args.node,
        metrics_host=args.metrics_host,
    )


if __name__ == "__main__":
    sys.exit(main())
