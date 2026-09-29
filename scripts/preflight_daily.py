#!/usr/bin/env python3
"""The daily currency check, for every machine in use. #726

The operator's rule, 2026-09-28: preflight runs at least once per day on any
machine we are running, and it is not advisory. The batch entry points
(`client_container.py` on a client, `server_facts.py` on a server) already
refuse on a stale component; this runs the same checks on a timer, so a
machine that sat idle for a day is known to be stale before anyone starts a
batch on it.

It checks what this machine can see: every recipe checkout and serving image
declared in tasks.toml that exists here, and the client image if it is built
here. It exits 1 if anything is behind or unknown, and prints the fixes.

    uv run python scripts/preflight_daily.py

Installed as a systemd --user timer from `systemd/local-llm-preflight.*`.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import build_client_image
import client_container
import currency
from lib import logs

logger = logging.getLogger(__name__)


def targets(cfg: dict) -> tuple[set[pathlib.Path], set[str]]:
    """The recipe checkouts present here and the images they declare."""
    recipes: set[pathlib.Path] = set()
    images: set[str] = set()
    for spec in cfg.get("backend", {}).values():
        raw = spec.get("recipe_dir")
        if not raw:
            continue
        path = pathlib.Path(raw).expanduser()
        if path.is_dir():
            recipes.add(path)
            if spec.get("image"):
                images.add(spec["image"])
    return recipes, images


def client_images(run: currency.Runner = currency._run) -> set[str]:
    """Every local-llm-client:<tag> image on this machine."""
    got = run(
        [
            "docker",
            "image",
            "ls",
            "local-llm-client",
            "--format",
            "{{.Repository}}:{{.Tag}}",
        ],
        30,
    )
    return {
        line for line in (got or "").split() if line.startswith("local-llm-client:")
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--tasks-file",
        type=pathlib.Path,
        default=REPO / "benchmarks" / "agent" / "tasks.toml",
    )
    args = p.parse_args(argv)
    logs.configure()
    cfg = tomllib.loads(args.tasks_file.read_text())
    recipes, images = targets(cfg)
    refusals = []
    for path in sorted(recipes):
        why = currency.gate(f"recipe:{path}", [currency.recipe_item(path)])
        if why:
            refusals.append(why)
    for ref in sorted(images):
        why = currency.gate(f"image:{ref}", [currency.image_item(ref)])
        if why:
            refusals.append(why)
    tag = f"local-llm-client:{build_client_image.PINS['opencode']}"
    others = client_images()
    if client_container.image_pins(tag) is not None:
        why = client_container.check_image_current(tag)
        if why:
            refusals.append(why)
    elif others:
        # A client machine (it holds older client images) without the image
        # this checkout expects is stale, not "nothing to check".
        refusals.append(
            f"preflight: this client holds {', '.join(sorted(others))} but not {tag}; "
            "build it with `uv run python scripts/build_client_image.py`"
        )
    if not (recipes or images):
        logger.info("preflight: no recipe or serving image declared on this machine")
    for why in refusals:
        logger.error("%s", why)
    logger.info(
        "preflight daily: %d recipe(s), %d image(s) checked, %d refusal(s)",
        len(recipes),
        len(images),
        len(refusals),
    )
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
