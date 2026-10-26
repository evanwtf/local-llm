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
import shutil
import sys
import tomllib
from collections.abc import Callable

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import build_client_image
import client_container
import currency

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import logs

logger = logging.getLogger(__name__)


def targets(cfg: dict) -> tuple[set[pathlib.Path], set[str]]:
    """The recipe checkouts present here and the images they declare."""
    recipes: set[pathlib.Path] = set()
    images: set[str] = set()
    for spec in cfg.get("backend", {}).values():
        raw = spec.get("recipe_dir")
        # A retired backend is out of the matrix (run.py); its old image must
        # not hold the gate shut (#1049).
        if not raw or spec.get("retired"):
            continue
        path = pathlib.Path(raw).expanduser()
        if path.is_dir():
            recipes.add(path)
            if spec.get("image"):
                images.add(spec["image"])
    return recipes, images


def recipe_pins(cfg: dict) -> dict[pathlib.Path, set[str]]:
    """Each recipe checkout's pins, from the live backends that declare one.

    One checkout can serve a pinned backend and an unpinned one, as
    ~/dsv41_recipe serves the pick at cc59c36 and its v2.0 arm (#1257).
    """
    pins: dict[pathlib.Path, set[str]] = {}
    for spec in cfg.get("backend", {}).values():
        raw, pin = spec.get("recipe_dir"), spec.get("recipe_pin")
        if raw and pin and not spec.get("retired"):
            pins.setdefault(pathlib.Path(raw).expanduser(), set()).add(str(pin))
    return pins


def recipe_refusal(
    path: pathlib.Path, pins: set[str], run: currency.Runner = currency._run
) -> str | None:
    """None when the checkout is at upstream or at a live backend's pin."""
    latest = currency.recipe_item(path, run)
    if latest.ok:
        return None
    for pin in sorted(pins):
        if currency.recipe_item(path, run, pin=pin).ok:
            logger.info("recipe %s: pinned at %s (%s)", path, pin, latest.latest)
            return None
    return currency.gate(f"recipe:{path}", [latest])


def client_images(
    run: currency.Runner = currency._run,
    which: Callable[[str], str | None] = shutil.which,
) -> set[str] | None:
    """Every local-llm-client:<tag> image on this machine, or None.

    None when docker is installed but the listing failed (daemon down, access
    denied). That is "could not look", not "no images": read as an empty set,
    a client machine with a broken docker passed the gate (review). A machine
    with no docker at all holds no client image, so it has nothing to check.
    """
    if which("docker") is None:
        return set()
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
    if got is None:
        return None
    return {line for line in got.split() if line.startswith("local-llm-client:")}


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
    pins = recipe_pins(cfg)
    refusals = []
    for path in sorted(recipes):
        why = recipe_refusal(path, pins.get(path, set()))
        if why:
            refusals.append(why)
    for ref in sorted(images):
        items = [currency.image_item(ref)]
        # #1049: the digest alone passed v0.30.0 a week after v0.31.0 shipped.
        engine_item = currency.engine_release_item(ref)
        if engine_item is not None:
            items.append(engine_item)
        why = currency.gate(f"image:{ref}", items)
        if why:
            refusals.append(why)
    tag = f"local-llm-client:{build_client_image.PINS['opencode']}"
    others = client_images()
    if others is None:
        refusals.append(
            "preflight: cannot list the client images (`docker image ls` failed);"
            " start docker or fix access, then rerun"
        )
    elif client_container.image_pins(tag) is not None:
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
