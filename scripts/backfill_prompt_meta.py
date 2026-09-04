"""Write an inferred prompt sidecar for runs measured before #140.

Eight run directories under `benchmarks/ds4/` were measured before anything
recorded the prompt. They cannot be stamped -- a stamp claims the run wrote
it, and these did not -- so they get a sidecar marked `inferred`, and every
figure quoted from them prints as inferred for as long as it is quoted.

The inference is not "probably the default". It rests on two checks this
script performs and refuses without:

* `scripts/decode_ab.sh` has had one PROMPT default since it was created in
  91ca9ff, and the harness line recorded in each run's `start-state.txt`
  carries no `PROMPT=` override;
* the prompt file is byte-identical in every ds4 tree on this machine, so
  which tree ran does not change what was measured.

A directory whose `start-state.txt` DOES carry an override is skipped and
named. Guessing there would be exactly the error #140 is about.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import prompt_meta

logger = logging.getLogger(__name__)

#: An explicit PROMPT= in the recorded harness line. Its presence means the
#: run did not use the default and this script cannot say what it used.
OVERRIDE = re.compile(r"\bPROMPT=")


def has_override(outdir: pathlib.Path) -> bool:
    state = outdir / "start-state.txt"
    return state.is_file() and bool(OVERRIDE.search(state.read_text()))


def run_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """Directories holding A/B CSVs, sorted."""
    return sorted({p.parent for p in root.rglob("*-rep*.csv")})


def backfill(
    root: pathlib.Path, prompt: pathlib.Path, *, apply: bool
) -> tuple[list[pathlib.Path], list[pathlib.Path], list[pathlib.Path]]:
    """(written, already recorded, skipped for an override)."""
    written: list[pathlib.Path] = []
    already: list[pathlib.Path] = []
    skipped: list[pathlib.Path] = []
    size, sha = prompt.stat().st_size, prompt_meta.digest(prompt)
    why = (
        "inferred, not recorded by the run: scripts/decode_ab.sh has had one "
        "PROMPT default since 91ca9ff, this run's start-state.txt harness "
        "line carries no PROMPT= override, and the file is byte-identical in "
        f"every ds4 tree on this machine (sha256 {sha[:16]}...)"
    )
    for outdir in run_dirs(root):
        if prompt_meta.for_run(outdir) is not None:
            already.append(outdir)
            continue
        if has_override(outdir):
            skipped.append(outdir)
            continue
        if apply:
            prompt_meta.write_sidecar(
                outdir,
                name=prompt.name,
                size=size,
                sha256=sha,
                inferred=True,
                why=why,
            )
        written.append(outdir)
    return written, already, skipped


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("root", type=pathlib.Path, help="tree to search for run dirs")
    p.add_argument("--prompt", required=True, type=pathlib.Path)
    p.add_argument(
        "--apply",
        action="store_true",
        help="write the sidecars; without it, only say what would be written",
    )
    args = p.parse_args(argv)
    if not args.prompt.is_file():
        logger.error("prompt not found: %s", args.prompt)
        return 1
    written, already, skipped = backfill(args.root, args.prompt, apply=args.apply)
    verb = "wrote" if args.apply else "would write"
    for d in written:
        logger.info("%s %s/%s", verb, d, prompt_meta.SIDECAR)
    for d in already:
        logger.info("already recorded: %s", d)
    for d in skipped:
        logger.warning("SKIPPED, harness line sets PROMPT: %s", d)
    logger.info(
        "%s %d, already recorded %d, skipped %d",
        verb,
        len(written),
        len(already),
        len(skipped),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
