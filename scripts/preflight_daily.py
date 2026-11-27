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

A locally built image (`image_source = "local"`) has no registry. It is
checked on the nodes instead (#1515, `node_image.local_image_check`): it
exists on each node, holds the same layers on both nodes of a two-node
backend, and its `org.local-llm.vllm-pr-sha` label, where it has one, starts
with the backend's `recipe_pin`.

    uv run python scripts/preflight_daily.py

Installed as a systemd --user timer from `systemd/local-llm-preflight.*`.
"""

from __future__ import annotations

import argparse
import dataclasses
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
import node_image

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
        # A locally built image has no registry: `local_images` checks it on
        # the nodes instead (#1515).
        if path.is_dir():
            recipes.add(path)
            if spec.get("image") and spec.get("image_source") != "local":
                images.add(spec["image"])
    return recipes, images


@dataclasses.dataclass
class LocalImage:
    """A locally built image, as the live backends that serve it declare it."""

    two_node: bool = False
    pins: set[str] = dataclasses.field(default_factory=set)


def local_images(cfg: dict) -> dict[str, LocalImage]:
    """Each `image_source = "local"` image whose checkout is here (#1515).

    Such an image has no registry, so asking one for its digest refused every
    local backend each day ("could not read ... from its registry"), and a
    check that always refuses hides a real refusal. Its checkout is the
    recipe check above; this adds the image itself, on each node it serves
    from. One image can serve several backends: it is two-node if any of them
    is, and it must match every pin they declare.
    """
    found: dict[str, LocalImage] = {}
    for spec in cfg.get("backend", {}).values():
        raw, ref = spec.get("recipe_dir"), spec.get("image")
        if spec.get("retired") or spec.get("image_source") != "local" or not ref:
            continue
        if not raw or not pathlib.Path(raw).expanduser().is_dir():
            continue
        entry = found.setdefault(str(ref), LocalImage())
        entry.two_node = entry.two_node or node_image.two_node_backend(spec)
        if spec.get("recipe_pin"):
            entry.pins.add(str(spec["recipe_pin"]))
    return found


def local_image_refusals(
    images: dict[str, LocalImage],
    inspect: Callable[[int, str], node_image.ImageFacts | None] | None = None,
) -> list[str]:
    """The refusal for each local image that is absent, split, or off its pin."""
    inspect = inspect or node_image.inspect_image
    refusals = []
    for ref, entry in sorted(images.items()):
        check = node_image.local_image_check(
            ref, two_node=entry.two_node, pins=entry.pins, inspect=inspect
        )
        # An unlabeled image is not a pass on its pin: say so loudly (#1515).
        for warning in check.warnings:
            logger.warning("preflight: %s", warning)
        if check.refusals:
            refusals.append("preflight: " + "\n  ".join(check.refusals))
        else:
            logger.info(
                "preflight: local image %s: present on %s",
                ref,
                "both nodes, same layers" if entry.two_node else "the head",
            )
    return refusals


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
        item = currency.recipe_item(path, run, pin=pin)
        if item.ok:
            logger.info("recipe %s: pinned at %s (%s)", path, pin, latest.latest)
            # Names any upstream release the pin lacks (#1257).
            currency.log_items([item])
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
    local = local_images(cfg)
    refusals.extend(local_image_refusals(local))
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
    if not (recipes or images or local):
        logger.info("preflight: no recipe or serving image declared on this machine")
    for why in refusals:
        logger.error("%s", why)
    logger.info(
        "preflight daily: %d recipe(s), %d image(s), %d local image(s) checked, "
        "%d refusal(s)",
        len(recipes),
        len(images),
        len(local),
        len(refusals),
    )
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
