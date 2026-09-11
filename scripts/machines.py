"""The hardware this project manages -- the single source of truth (#302).

A machine's name is derived from the machine itself by `scripts/hardware_id.py`,
and `benchmarks/agent/provenance.machine_slug()` already stamps that slug into
every log filename. This records the small set we actually run, so three things
read ONE list instead of drifting apart:

- the GitHub **machine label** a box queries for its own work,
- the generated **hardware/MACHINES.md**,
- the **contract test** (`tests/test_machines.py`).

`slug` is `provenance.machine_slug()` for the box, the bare machine identity;
its GitHub label is that slug under the `hardware:` namespace (`Machine.label`).
A machine finds its own queue with:

    gh issue list --label "hardware:$(uv run python scripts/hardware_id.py --slug)"

`directory` is its `hardware/<id>/` directory. `classes` are the broad
`platform:macOS`/`platform:Nvidia` tags that also apply, for a query across a
whole OS or vendor; they are NOT a substitute for the machine label. `name` is
the third-person form for prose -- a record names the box, never "this
machine" (#302).
"""

from __future__ import annotations

import logging
import pathlib
import sys
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)

#: The GitHub machine label is the slug under this namespace: `hardware:<slug>`.
#: The slug stays the bare machine identity (it is what `machine_slug()` emits
#: and what a log filename carries); the prefix is only how the label is
#: presented, so a machine's query is `--label "hardware:$(hardware_id --slug)"`.
HARDWARE_PREFIX = "hardware:"

#: Broad labels for a query across an OS or a vendor, under the `platform:`
#: namespace. A class tag never identifies a machine on its own -- the RTX
#: 3080 Ti desktop and the DGX Spark are both `platform:Nvidia` -- so it never
#: satisfies the machine-label requirement (#302).
CLASS_LABELS: tuple[str, ...] = ("platform:macOS", "platform:Nvidia")


@dataclass(frozen=True)
class Machine:
    """One box we manage. `slug` is the bare machine identity (== its
    `machine_slug()`); `label` is its namespaced GitHub label."""

    slug: str
    directory: str
    name: str
    os: str
    arch: str
    accelerator: str
    memory: str
    classes: tuple[str, ...]
    note: str

    @property
    def label(self) -> str:
        """The GitHub machine label: the slug under the `hardware:` namespace."""
        return f"{HARDWARE_PREFIX}{self.slug}"


MACHINES: tuple[Machine, ...] = (
    Machine(
        slug="M5-Max-128GB",
        directory="MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A",
        name="the M5 Max MacBook Pro (128 GB)",
        os="macOS",
        arch="arm64",
        accelerator="Apple M5 Max GPU (Metal)",
        memory="128 GiB unified",
        classes=("platform:macOS",),
        note="primary machine; the laptop this project is premised on",
    ),
    Machine(
        slug="Cortex-X925-GB10",
        directory="Cortex-X925-128GB-GB10",
        name="the DGX Spark (GB10, 128 GB)",
        os="Linux",
        arch="aarch64",
        accelerator="NVIDIA GB10 Grace Blackwell GPU",
        memory="128 GiB unified",
        classes=("platform:Nvidia",),
        note="DGX Spark; unified memory, so a VRAM-based judgement does not apply",
    ),
    Machine(
        slug="Ryzen9-7900X-RTX3080Ti",
        directory="Ryzen9-7900X-32GB-RTX3080Ti-12GB",
        name="the Ryzen 9 7900X + RTX 3080 Ti desktop",
        os="Linux",
        arch="x86_64",
        accelerator="NVIDIA RTX 3080 Ti (12 GB VRAM)",
        memory="32 GiB system",
        classes=("platform:Nvidia",),
        note="desktop; a discrete GPU with 12 GB of VRAM",
    ),
)

DOC = REPO / "hardware" / "MACHINES.md"


def by_slug(slug: str) -> Machine | None:
    """The machine with this slug, or None."""
    return next((m for m in MACHINES if m.slug == slug), None)


def by_directory(directory: str) -> Machine | None:
    """The machine in this `hardware/<id>/` directory, or None."""
    return next((m for m in MACHINES if m.directory == directory), None)


def this_machine() -> Machine | None:
    """The registry entry for the machine running this, or None if unmanaged.

    Anyone may run this project's code; most will not be on one of our boxes.
    `None` is the honest answer for a 3090 when we publish 3080 Ti numbers --
    the hardware is close, the numbers will not match, and a reader has to know
    which. See `check_this_machine`.
    """
    import hardware_id

    facts, platform = hardware_id.facts_for_this_machine()
    return by_slug(hardware_id.short_slug(facts, platform))


def check_this_machine() -> int:
    """Say whether this machine is one we manage. 0 if it is, 1 if not.

    Not an error either way -- the non-zero exit is so a script can branch on
    "are these numbers comparable to the published ones?" without parsing text.
    """
    import hardware_id

    facts, platform = hardware_id.facts_for_this_machine()
    slug = hardware_id.short_slug(facts, platform)
    match = by_slug(slug)
    if match:
        logger.info(
            "managed machine: %s (label %s) -- %s", match.slug, match.label, match.name
        )
        return 0
    logger.warning(
        "UNMANAGED machine: slug %r is not in the registry (hardware/MACHINES.md). "
        "This project's published numbers come from the specific machines listed "
        "there; results measured here will not match them -- a close card is not "
        "the same card (a 3090 is not our 3080 Ti). The code runs fine; the "
        "comparison does not hold.",
        slug,
    )
    return 1


def render_markdown() -> str:
    """hardware/MACHINES.md, generated so the list cannot drift from the code."""
    lines = [
        "<!-- generated by scripts/machines.py -- do not edit; edit the registry there -->",
        "",
        "# The hardware we manage",
        "",
        "The single source of truth is `scripts/machines.py`; this file is",
        "generated from it. Each machine's name is derived from the hardware by",
        "`scripts/hardware_id.py` (see `hardware/README.md`), never typed.",
        "",
        "## The machines",
        "",
        "| machine | label (slug) | directory | OS / arch | accelerator | memory |",
        "|---|---|---|---|---|---|",
    ]
    for m in MACHINES:
        lines.append(
            f"| {m.name} | `{m.label}` | `{m.directory}` | {m.os} / {m.arch} "
            f"| {m.accelerator} | {m.memory} |"
        )
    lines += [
        "",
        "## The label is `hardware:` + the slug, so a machine queries its own work",
        "",
        "The GitHub **machine label** is the slug (what `provenance.machine_slug()`",
        "stamps into every log filename) under the `hardware:` namespace. A box",
        "finds its own queue without anyone typing a name:",
        "",
        "```sh",
        'gh issue list --label "hardware:$(uv run python scripts/hardware_id.py --slug)"',
        "```",
        "",
        "`platform:macOS` and `platform:Nvidia` stay as broad **class** tags for",
        "a query across a whole OS or vendor -- the RTX 3080 Ti desktop and the",
        "DGX Spark are both `platform:Nvidia`. A class tag never identifies a",
        "machine on its own, so an issue that consumes a machine's time carries",
        "exactly one machine label as well.",
        "",
        '## Name the machine, never "this machine"',
        "",
        "Three agents read this repo, one per box. A record -- an issue, a commit,",
        "a results doc, a sweep -- names the machine in the third person and never",
        'writes "this machine", "this box", or "here", which resolve differently',
        "depending on which agent reads them. Use the `machine` column above.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    import argparse

    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    import logs

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check",
        action="store_true",
        help="report whether THIS machine is one we manage (exit 1 if not)",
    )
    args = p.parse_args()
    logs.configure()
    if args.check:
        return check_this_machine()
    DOC.write_text(render_markdown())
    logger.info("wrote %s (%d machines)", DOC, len(MACHINES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
