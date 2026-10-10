#!/usr/bin/env python3
"""Merge model-archive manifests without losing the archive's own copy (#999).

The archive host keeps one `manifest.json` per machine tree: a header plus an
`items` list, one record per archived model or image. A second copy of the
manifest lives on the machine that archives, and the two drift: on 2026-10-10
the archive's copy had the newer header and 35 items, and the head's copy had
the older header and 36. A hand-run jq merge got that right once, but a merge
by hand can also drop the newer header or list an item twice, and nothing
would say so.

`merge` takes the base manifest (normally the archive host's copy, whose
header wins), any number of other manifests (`--from`) and single entry files
(`--entry`), and writes the union of their items in that order. It refuses:

- two different records under one name (exit 1, names the item);
- a base that already lists one name twice;
- an entry with no name;
- an `--out` path that is also an input (exit 2), so a mistake cannot
  overwrite the copy it was meant to preserve.

It writes nothing unless the merge succeeds, then reads the file back and
checks the item count. Hosts and paths are arguments: this repo is public.

    uv run python scripts/archive_manifest.py merge ARCHIVE.json \\
        --from HEAD.json --entry DELTA-entry.json --out MERGED.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")


class ConflictError(ValueError):
    """The inputs disagree, and picking one record would hide that."""


def _name(item: dict[str, Any], where: str) -> str:
    name = item.get("name")
    if not isinstance(name, str) or not name:
        raise ConflictError(f"an item in {where} has no name")
    return name


def merge(
    base: dict[str, Any],
    others: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    now: str,
) -> dict[str, Any]:
    """The base's header and items, plus every item the others add.

    An item already present under the same name must be identical; a
    different record for the name raises ConflictError.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for item in base.get("items", []):
        name = _name(item, "the base")
        if name in by_name:
            raise ConflictError(f"the base lists {name!r} twice")
        by_name[name] = item
    extra = [
        (f"--from #{n + 1}", i)
        for n, o in enumerate(others)
        for i in o.get("items", [])
    ]
    extra += [(f"--entry #{n + 1}", e) for n, e in enumerate(entries)]
    for where, item in extra:
        name = _name(item, where)
        held = by_name.get(name)
        if held is None:
            by_name[name] = item
        elif held != item:
            raise ConflictError(f"{where} has a different record for {name!r}")
    out = {k: v for k, v in base.items() if k != "items"}
    out["updated"] = now
    out["items"] = list(by_name.values())
    return out


def _load(path: pathlib.Path) -> dict[str, Any]:
    doc = json.loads(path.read_text())
    if not isinstance(doc, dict):
        raise ConflictError(f"{path} is not a JSON object")
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("merge", help="merge manifests; the base's header wins")
    m.add_argument("base", type=pathlib.Path, help="the archive host's manifest.json")
    m.add_argument(
        "--from", dest="others", type=pathlib.Path, action="append", default=[]
    )
    m.add_argument(
        "--entry", dest="entries", type=pathlib.Path, action="append", default=[]
    )
    m.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    inputs = [args.base, *args.others, *args.entries]
    out = args.out.resolve()
    if any(p.resolve() == out for p in inputs):
        logger.error("REFUSING: --out %s is also an input", args.out)
        return 2
    now = datetime.datetime.now(NY).isoformat(timespec="seconds")
    try:
        base = _load(args.base)
        merged = merge(
            base,
            [_load(p) for p in args.others],
            [_load(p) for p in args.entries],
            now,
        )
    except (ConflictError, json.JSONDecodeError) as exc:
        logger.error("REFUSING: %s; wrote nothing", exc)
        return 1
    args.out.write_text(json.dumps(merged, indent=2) + "\n")
    back = _load(args.out)
    if len(back["items"]) != len(merged["items"]):
        logger.error(
            "read-back of %s has %d items, wrote %d",
            args.out,
            len(back["items"]),
            len(merged["items"]),
        )
        return 1
    added = len(merged["items"]) - len(base.get("items", []))
    logger.info(
        "wrote %s: %d items (%d from the base, %d added); header from %s",
        args.out,
        len(merged["items"]),
        len(base.get("items", [])),
        added,
        args.base,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
